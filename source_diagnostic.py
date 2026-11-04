"""Diagnose public Facebook HTML and network payloads without saving credentials.

Only records aggregate counts, response metadata, and public post URL samples.
Does not attempt to access content behind login or bypass restrictions.
"""
import json
import re
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

CONFIG = Path("pages.json")
OUTPUT = Path("results/source_diagnostic.json")
POST_URL_RE = re.compile(r'(?:https?:)?(?:\\?/\\?/)?(?:www\\.)?facebook\\.com(?:\\/|\\u002f)(?:[^"\\s<]{0,130}?)(?:posts|reel|videos|permalink)', re.I)
MARKERS = (
    "This page isn't available right now",
    "Log in to Facebook",
    "See more",
    "ProfileCometTimelineFeed",
    "CometSinglePost",
    "creation_time",
    "story_fbid",
    "feedback_id",
    "message",
    "post_id",
    "__bbox",
    "RelayPrefetchedStreamCache",
)

def summarize_html(html):
    return {
        "html_chars": len(html),
        "script_elements": html.lower().count("<script"),
        "markers": {marker: html.lower().count(marker.lower()) for marker in MARKERS},
        "post_url_fragments": len(re.findall(r'(?:/posts/|/reel/|story_fbid=|/videos/)', html, re.I)),
        "inline_json_scripts": len(re.findall(r'<script[^>]+type=["\']application/json', html, re.I)),
        "requires_login": bool(re.search(r"(?:Log in to Facebook|You must log in to continue)", html, re.I)),
        "unavailable": "This page isn't available right now" in html,
    }

def diagnostic():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for item in config["pages"]:
            if not item.get("enabled", True):
                continue
            url = item["url"]
            record = {"name": item["name"], "url": url}
            for variant in ("desktop", "mobile"):
                network = []
                ctx = browser.new_context(
                    viewport={"width": 1365, "height": 900} if variant == "desktop" else {"width": 390, "height": 844},
                    is_mobile=(variant == "mobile"),
                    has_touch=(variant == "mobile"),
                    locale="en-US",
                )
                page = ctx.new_page()
                def onresponse(resp):
                    parsed = urlparse(resp.url)
                    if not (parsed.hostname or "").endswith("facebook.com"):
                        return
                    if any(t in parsed.path for t in ("graphql", "ajax", "api")):
                        network.append({"path": parsed.path[:140], "status": resp.status, "type": resp.request.resource_type})
                page.on("response", onresponse)
                try:
                    response = page.goto(url, wait_until="domcontentloaded", timeout=35000)
                    page.wait_for_timeout(2500)
                    initial_html = page.content()
                    initial = summarize_html(initial_html)
                    articles_before = page.locator('[role="article"]').count()
                    for _ in range(3):
                        page.mouse.wheel(0, 1300)
                        page.wait_for_timeout(1200)
                    final_html = page.content()
                    record[variant] = {
                        "status": response.status if response else None,
                        "final_path": urlparse(page.url).path,
                        "title": page.title()[:120],
                        "before": initial,
                        "after": summarize_html(final_html),
                        "articles_before": articles_before,
                        "articles_after": page.locator('[role="article"]').count(),
                        "network": network[:20],
                        "body_preview": page.locator("body").inner_text()[:350],
                    }
                    print(f'DIAG {item["name"]} {variant}: {json.dumps(record[variant], ensure_ascii=False)}', flush=True)
                except Exception as exc:
                    record[variant] = {"error": f"{type(exc).__name__}: {exc}"[:400]}
                    print(f'DIAG_ERROR {item["name"]} {variant}: {exc}', flush=True)
                finally:
                    ctx.close()
            results.append(record)
        browser.close()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"DIAGNOSTIC_COMPLETE sources={len(results)}", flush=True)

if __name__ == "__main__":
    diagnostic()
