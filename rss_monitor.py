"""Build a stable RSS feed from publicly visible Facebook posts.

Reads the current diagnostic collector, preserves prior posts, and only publishes
permalink-backed posts whose text was expanded. No credentials or paid APIs.
"""
import json
import os
from collections import Counter
import re
from datetime import datetime, timezone
from email.utils import format_datetime
from html import escape
from pathlib import Path
from textwrap import shorten
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
DC_NS = "http://purl.org/dc/elements/1.1/"
ATOM_NS = "http://www.w3.org/2005/Atom"
ET.register_namespace("content", CONTENT_NS)
ET.register_namespace("dc", DC_NS)
ET.register_namespace("atom", ATOM_NS)


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


def clean_text(raw, name, *, article_text=True):
    raw = str(raw or "").replace("\u00a0", " ").replace("\u202f", " ")
    lines = [line.strip() for line in raw.splitlines()]
    if not article_text:
        if lines and lines[-1].casefold() == "see less":
            lines.pop()
        return "\n".join(lines).strip()

    relative = r"(?:(?:about\s+)?(?:a|an|one|\d+)\s+(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)\s+ago|just now|yesterday|\d+[smhdwy])"
    absolute = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+\w+\s+\d{1,2},?\s+\d{4}\s+at\s+.+"
    header = re.compile(rf"(?:{relative}|{absolute}|AI (?:content|info))", re.I)
    check_in = bool(lines and lines[0].startswith(name + " is at "))
    while lines and (
        not lines[0] or lines[0] in (name, "·") or header.fullmatch(lines[0])
        or lines[0].startswith((name + " - ", name + " is at "))
    ):
        lines.pop(0)
    if check_in and lines and re.fullmatch(r"[^,]+, [^,.!?]+", lines[0]):
        if len(lines) > 1:
            lines.pop(0)

    for index, line in enumerate(lines):
        if (line.casefold() == "this content isn't available right now"
                or line.casefold().startswith("all reactions:")):
            lines = lines[:index]
            break
    controls = {"see less", "like", "comment", "share"}
    while lines and not lines[-1]:
        lines.pop()
    had_controls = False
    while lines and (lines[-1].casefold() in controls
                     or re.fullmatch(r"(?:view|see) (?:\d+ |more |all )?comments?", lines[-1], re.I)):
        had_controls |= lines.pop().casefold() != "see less"
    if had_controls and lines and re.fullmatch(r"\d+(?:[.,]\d+)?[KM]?", lines[-1], re.I):
        lines.pop()
    return "\n".join(lines).strip()


def post_title(item):
    first_line = item["text"].splitlines()[0]
    sentence = re.split(r"(?<=[.!?…])\s+", first_line, maxsplit=1)[0]
    if 24 <= len(sentence) <= 100:
        return sentence
    if len(first_line) <= 100:
        return first_line
    title = shorten(first_line, width=100, placeholder="…")
    return title if title != "…" else first_line[:99] + "…"


def post_html(item):
    text = item["text"]
    telegram = item.get("source_type") == "telegram"
    paragraphs = re.split(r"\n[ \t]*\n", text) if telegram else text.splitlines()
    return "".join(
        "<p>" + escape(paragraph.strip()).replace("\n", "<br/>") + "</p>"
        for paragraph in paragraphs if paragraph.strip()
    )


def enabled_sources():
    facebook = json.loads((ROOT / "pages.json").read_text(encoding="utf-8"))
    telegram = json.loads((ROOT / "telegram.json").read_text(encoding="utf-8"))
    facebook_names = {page["name"] for page in facebook["pages"] if page.get("enabled", True)}
    telegram_names = {
        channel["username"].strip().lstrip("@").casefold()
        for channel in telegram["telegram"] if channel.get("enabled", True)
    }
    return facebook_names, telegram_names


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
    ET.SubElement(channel, "{" + ATOM_NS + "}link", href=feed_url,
                  rel="self", type="application/rss+xml")
    image = ET.SubElement(channel, "image")
    ET.SubElement(image, "url").text = icon_url
    ET.SubElement(image, "title").text = title
    ET.SubElement(image, "link").text = site_url
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))
    for item in sorted(items, key=lambda x: x["first_seen"], reverse=True)[:MAX_FEED]:
        node = ET.SubElement(channel, "item")
        ET.SubElement(node, "title").text = post_title(item)
        ET.SubElement(node, "{" + DC_NS + "}creator").text = item["source"]
        ET.SubElement(node, "link").text = item["url"]
        ET.SubElement(node, "guid", isPermaLink="true").text = item["url"]
        ET.SubElement(node, "pubDate").text = format_datetime(datetime.fromisoformat(item["first_seen"]))
        html = post_html(item)
        ET.SubElement(node, "description").text = html
        ET.SubElement(node, "{" + CONTENT_NS + "}encoded").text = html
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)

def process(observations):
    existing = load_existing()
    for saved in existing.values():
        if saved.get("source_type") == "telegram":
            continue
        if not saved.get("message_only"):
            saved["text"] = clean_text(saved.get("text", ""), saved.get("source", "Facebook"))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new, unavailable = 0, 0
    new_by_source, updated_by_source = Counter(), Counter()
    results = {"facebook": {}, "telegram": {}}
    for page in observations:
        source = page.get("name", "Facebook")
        results["facebook"][source] = {
            "found": page.get("articles_found", 0),
            "selected": len(page.get("posts", [])),
            "error": page.get("error"),
            "access_attempts": page.get("access_attempts", []),
        }
        if page.get("error"):
            print("SOURCE_ERROR: {}: {}".format(page.get("name"), page["error"]), flush=True)
            unavailable += 1
        for post in page.get("posts", []):
            if post.get("still_truncated"):
                continue
            link = canonical_post_url(post.get("post_links", []))
            if not link:
                continue
            message_only = bool(post.get("message_only"))
            content = clean_text(post.get("text"), source, article_text=not message_only)
            if not content or (not message_only and content.lower() in ("like", "comment", "share")):
                continue
            if link not in existing:
                existing[link] = {
                    "url": link,
                    "source": source,
                    "text": content,
                    "first_seen": now,
                    "message_only": message_only,
                }
                new += 1
                new_by_source["facebook", source] += 1
            elif message_only or not existing[link].get("message_only"):
                if content != existing[link].get("text"):
                    updated_by_source["facebook", source] += 1
                existing[link].update(source=source, text=content, message_only=message_only)
    telegram_candidates = 0
    telegram_new = 0
    tg_results = ROOT / "results" / "telegram_posts.json"
    if tg_results.exists():
        for channel in json.loads(tg_results.read_text(encoding="utf-8")):
            name = channel["name"]
            source_id = channel.get("username", name)
            results["telegram"][source_id] = {
                "found": channel.get("metrics", {}).get("messages_in_html", 0),
                "selected": len(channel.get("posts", [])),
                "error": "TELEGRAM_FETCH_FAILED" if channel.get("error") else None,
            }
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
                    new_by_source["telegram", source_id] += 1
                elif item["text"] != existing[url].get("text"):
                    updated_by_source["telegram", source_id] += 1
                    existing[url]["text"] = item["text"]
                    existing[url]["title"] = item["title"]
    print(f"TELEGRAM_RSS: candidates={telegram_candidates} new={telegram_new}", flush=True)
    kept = sorted(existing.values(), key=lambda x: x["first_seen"], reverse=True)[:MAX_STORED]
    STATE.parent.mkdir(parents=True, exist_ok=True)
    FEED.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(kept, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    facebook_names, telegram_names = enabled_sources()
    facebook_items, telegram_items = [], []
    for item in kept:
        if not item.get("text"):
            continue
        url = urlsplit(item["url"])
        if item.get("source_type") == "telegram" or url.hostname in ("t.me", "www.t.me", "telegram.me"):
            username = url.path.strip("/").split("/")[0].casefold()
            if username in telegram_names:
                telegram_items.append({**item, "source_type": "telegram"})
        elif item.get("source") in facebook_names:
            facebook_items.append(item)
    facebook_settings = {
        "site_url": "https://www.facebook.com/",
        "description": "Public Facebook page posts, collected when accessible",
        "icon_url": "https://geomack.github.io/facebook-rss/facebook-icon.svg",
    }
    telegram_settings = {
        "site_url": "https://t.me/",
        "description": "Public Telegram channel messages, collected when accessible",
        "icon_url": "https://geomack.github.io/facebook-rss/telegram-icon.svg",
    }
    FEED.write_bytes(build_feed(
        facebook_items, title="Facebook",
        feed_url="https://geomack.github.io/facebook-rss/feed.xml",
        **facebook_settings
    ))
    FEED_TEST.write_bytes(build_feed(
        facebook_items, title="Facebook",
        feed_url="https://geomack.github.io/facebook-rss/feed-test.xml",
        **facebook_settings
    ))
    TELEGRAM_FEED.write_bytes(build_feed(
        telegram_items, title="Telegram",
        feed_url="https://geomack.github.io/facebook-rss/telegram.xml",
        **telegram_settings
    ))
    for path in (FEED, FEED_TEST, TELEGRAM_FEED):
        ET.parse(path)
    sources = []
    for kind, file_name, collection, published in (
        ("facebook", "pages.json", "pages", facebook_items),
        ("telegram", "telegram.json", "telegram", telegram_items),
    ):
        config = json.loads((ROOT / file_name).read_text(encoding="utf-8"))
        for entry in config[collection]:
            source = entry["url"] if kind == "facebook" else entry["username"].strip().lstrip("@")
            key = entry["name"] if kind == "facebook" else source.casefold()
            live_count = sum(
                (item["source"] == key if kind == "facebook"
                 else urlsplit(item["url"]).path.split("/")[1].casefold() == key)
                for item in published[:MAX_FEED]
            )
            row = results[kind].get(key, {})
            error = row.get("error")
            sources.append({
                "feed": kind, "name": entry["name"], "source": source,
                "enabled": entry.get("enabled", True), "checked": key in results[kind],
                "found": row.get("found", 0), "selected": row.get("selected", 0),
                "access_attempts": row.get("access_attempts", []),
                "new": new_by_source[kind, key], "updated": updated_by_source[kind, key],
                "in_feed": live_count,
                "error": (error if error in {"FACEBOOK_LOGIN_REQUIRED", "FACEBOOK_PAGE_UNAVAILABLE", "TELEGRAM_FETCH_FAILED"}
                          else "SOURCE_FETCH_FAILED" if error else None),
            })
    report = {"built_at": now, "run_id": os.getenv("GITHUB_RUN_ID"), "sources": sources}
    (ROOT / "docs" / "sources.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
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
