import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from facebook_scraper import get_posts

BASE = Path(__file__).resolve().parent
config = json.loads((BASE / "pages.json").read_text(encoding="utf-8"))
results = []

for page in config["pages"]:
    if not page.get("enabled", True):
        continue
    url = page["url"].rstrip("/")
    parsed = urlparse(url)
    if parsed.hostname not in ("facebook.com", "www.facebook.com", "m.facebook.com"):
        print(f"SKIP: Unsupported domain: {url}", flush=True)
        continue
    page_id = parsed.path.strip("/")
    if not page_id or "/" in page_id or page_id == "profile.php":
        print(f"SKIP: Use a simple Facebook Page URL: {url}", flush=True)
        continue

    print(f"\n=== {page['name']}: {url} ===", flush=True)
    try:
        response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        print(f"PUBLIC_PAGE_HTTP_STATUS: {response.status_code}", flush=True)
        print(f"PUBLIC_PAGE_FINAL_URL: {response.url}", flush=True)
    except requests.RequestException as exc:
        print(f"PUBLIC_PAGE_REQUEST_ERROR: {type(exc).__name__}: {exc}", flush=True)

    try:
        count = 0
        for post in get_posts(page_id, pages=2, options={"comments": False, "allow_extra_requests": False}):
            text = post.get("text") or ""
            item = {
                "source": page["name"],
                "post_id": post.get("post_id"),
                "date": post["time"].isoformat() if post.get("time") else None,
                "url": post.get("post_url"),
                "text": text,
            }
            results.append(item)
            count += 1
            print(f"POST {count}: id={item['post_id']} text_chars={len(text)} url={item['url']}", flush=True)
            if text:
                print(f"TEXT_PREVIEW: {text[:500]!r}", flush=True)
            if count >= int(config.get("posts_per_page", 3)):
                break
        print(f"EXTRACTED_POSTS: {count}", flush=True)
        if count == 0:
            print("NO_POSTS: public access may be restricted or scraper incompatible", flush=True)
    except Exception as exc:
        print(f"SCRAPER_ERROR: {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc(limit=3)

output = BASE / "results" / "facebook_test.json"
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps({
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "posts": results,
}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nRESULT: {len(results)} posts saved to {output}", flush=True)
