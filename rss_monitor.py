"""Build a stable RSS feed from publicly visible Facebook posts.

Reads the current diagnostic collector, preserves prior posts, and only publishes
permalink-backed posts whose text was expanded. No credentials or paid APIs.
"""
import json
import re
from datetime import datetime, timezone
from email.utils import format_datetime
from html import escape
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qs
from xml.etree import ElementTree as ET

import check_facebook
import telegram_collector

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "results" / "facebook_browser_test.json"
STATE = ROOT / "state" / "posts.json"
FEED = ROOT / "docs" / "feed.xml"
FEED_TEST = ROOT / "docs" / "feed-test.xml"
TELEGRAM_FEED = ROOT / "docs" / "telegram.xml"
MAX_STORED = 2000
MAX_FEED = 300
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
ET.register_namespace("content", CONTENT_NS)


def canonical_post_url(links):
    for kind in ("posts", "permalink", "other"):
        for link in links:
            parsed = urlsplit(link)
            if parsed.hostname not in ("www.facebook.com", "facebook.com", "m.facebook.com"):
                continue
            path = parsed.path.rstrip("/")
            query = parse_qs(parsed.query)
            if kind == "posts" and "/posts/" in path:
                return urlunsplit(("https", "www.facebook.com", path, "", ""))
            if kind == "permalink" and "story_fbid" in query:
                params = ["story_fbid=" + query["story_fbid"][0]]
                if "id" in query:
                    params.append("id=" + query["id"][0])
                return "https://www.facebook.com/permalink.php?" + "&".join(params)
            if kind == "other":
                if "/videos/" in path or "/reel/" in path:
                    return urlunsplit(("https", "www.facebook.com", path, "", ""))
                if path == "/photo" and query.get("fbid"):
                    return "https://www.facebook.com/photo/?fbid=" + query["fbid"][0]
                if path == "/watch" and query.get("v"):
                    return "https://www.facebook.com/watch/?v=" + query["v"][0]
    return None


def clean_text(raw, name):
    raw = str(raw or "").replace("\u00a0", " ").replace("\r", "")
    raw = re.sub(r"\s*See less(?=\s|$)", "", raw, flags=re.I)
    lines = [line.strip() for line in raw.splitlines() if line.strip() and line.strip() != "·"]
    if lines and (lines[0] == name or lines[0].startswith(name + " - ")):
        lines.pop(0)
    relative = r"(?:(?:about\s+)?(?:a|an|one|\d+)\s+(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)\s+ago|just now|yesterday|\d+[smhdwy])"
    absolute = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+\w+\s+\d{1,2},?\s+\d{4}\s+at\s+.+"
    # Facebook may put relative and absolute dates in either order.
    while lines and (re.fullmatch(relative, lines[0], flags=re.I) or re.fullmatch(absolute, lines[0], flags=re.I)):
        lines.pop(0)
    lines = [line for line in lines if not re.fullmatch(r"\d{1,2}:\d{2}\s*/\s*\d{1,2}:\d{2}", line)]
    return "\n".join(lines).strip()


def load_existing():
    if not STATE.exists():
        return {}
    data = json.loads(STATE.read_text(encoding="utf-8"))
    return {item["url"]: item for item in data if isinstance(item, dict) and item.get("url")}


def build_feed(items, *, title, site_url, feed_url, description, icon_url):
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    info = {
        "title": title,
        "link": site_url,
        "description": description,
        "language": "uk" if "Telegram" in title else "en",
    }
    for name, value in info.items():
        ET.SubElement(channel, name).text = value
    image = ET.SubElement(channel, "image")
    ET.SubElement(image, "url").text = icon_url
    ET.SubElement(image, "title").text = title
    ET.SubElement(image, "link").text = site_url
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))
    for item in sorted(items, key=lambda x: x["first_seen"], reverse=True)[:MAX_FEED]:
        node = ET.SubElement(channel, "item")
        ET.SubElement(node, "title").text = item["title"]
        ET.SubElement(node, "link").text = item["url"]
        ET.SubElement(node, "guid", isPermaLink="true").text = item["url"]
        ET.SubElement(node, "pubDate").text = format_datetime(datetime.fromisoformat(item["first_seen"]))
        ET.SubElement(node, "description").text = item["text"]
        ET.SubElement(node, "{" + CONTENT_NS + "}encoded").text = (
            "<p>" + escape(item["text"]).replace("\n", "<br/>") + "</p>"
        )
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)

def process(observations):
    existing = load_existing()
    for saved in existing.values():
        if saved.get("source_type") == "telegram":
            continue
        normalized = clean_text(saved.get("text", ""), saved.get("source", "Facebook"))
        if normalized:
            saved["text"] = normalized
            heading = next((line for line in normalized.splitlines() if line.strip()), "New post")
            saved["title"] = saved.get("source", "Facebook") + ": " + heading[:110]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new, candidates, unavailable = 0, 0, 0
    for page in observations:
        if page.get("error"):
            print("SOURCE_ERROR: {}: {}".format(page.get("name"), page["error"]), flush=True)
            unavailable += 1
        for post in page.get("posts", []):
            candidates += 1
            if post.get("still_truncated"):
                continue
            link = canonical_post_url(post.get("post_links", []))
            if not link:
                continue
            source = page.get("name", "Facebook")
            content = clean_text(post.get("text"), source)
            if not content or content.lower() in ("like", "comment", "share"):
                continue
            headline = next((line for line in content.splitlines() if line.strip()), "New post")
            if link not in existing:
                existing[link] = {
                    "url": link,
                    "source": source,
                    "title": source + ": " + headline[:110],
                    "text": content,
                    "first_seen": now,
                }
                new += 1
            elif content != existing[link].get("text"):
                existing[link]["text"] = content
                existing[link]["title"] = source + ": " + headline[:110]
    telegram_candidates = 0
    telegram_new = 0
    tg_results = Path("results/telegram_posts.json")
    if tg_results.exists():
        for channel in json.loads(tg_results.read_text(encoding="utf-8")):
            if channel.get("error"):
                unavailable += 1
                print("TELEGRAM_SOURCE_ERROR {}: {}".format(channel.get("name"),channel["error"]), flush=True)
                continue
            for item in channel.get("posts", []):
                telegram_candidates += 1
                url = item["url"]
                if url not in existing:
                    existing[url] = {
                        **item,
                        "first_seen": now,
                    }
                    telegram_new += 1
                elif item["text"] != existing[url].get("text"):
                    existing[url]["text"] = item["text"]
                    existing[url]["title"] = item["title"]
    print(f"TELEGRAM_RSS: candidates={telegram_candidates} new={telegram_new}", flush=True)
    kept = sorted(existing.values(), key=lambda x: x["first_seen"], reverse=True)[:MAX_STORED]
    STATE.parent.mkdir(parents=True, exist_ok=True)
    FEED.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(kept, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    facebook_items = [item for item in kept if not (
        item.get("source_type") == "telegram" or
        urlsplit(item.get("url", "")).hostname in ("t.me", "www.t.me", "telegram.me")
    )]
    telegram_items = [item for item in kept if item not in facebook_items]
    facebook_settings = {
        "site_url": "https://www.facebook.com/",
        "description": "Public Facebook page posts, collected when accessible",
        "icon_url": "https://geomack.github.io/facebook-rss/facebook-icon.svg",
    }
    telegram_settings = {
        "site_url": "https://t.me/prykhodkopolska",
        "description": "Public Telegram channel messages, collected when accessible",
        "icon_url": "https://geomack.github.io/facebook-rss/telegram-icon.svg",
    }
    FEED.write_bytes(build_feed(
        facebook_items, title="Facebook | personal RSS",
        feed_url="https://geomack.github.io/facebook-rss/feed.xml",
        **facebook_settings
    ))
    FEED_TEST.write_bytes(build_feed(
        facebook_items, title="Facebook | RSS delivery test",
        feed_url="https://geomack.github.io/facebook-rss/feed-test.xml",
        **facebook_settings
    ))
    TELEGRAM_FEED.write_bytes(build_feed(
        telegram_items, title="Telegram | personal RSS",
        feed_url="https://geomack.github.io/facebook-rss/telegram.xml",
        **telegram_settings
    ))
    for path in (FEED, FEED_TEST, TELEGRAM_FEED):
        ET.parse(path)
    print(f"RSS_DONE: facebook={len(facebook_items)} telegram={len(telegram_items)} "
          f"new_facebook={new} new_telegram={telegram_new} source_errors={unavailable}", flush=True)


def main():
    check_facebook.main()
    telegram_collector.main()
    if not SOURCE.exists():
        raise RuntimeError("Collector produced no results JSON")
    process(json.loads(SOURCE.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
