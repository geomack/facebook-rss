"""Inspect Facebook public page JSON payloads for post-shaped content.
No login, no cookies, no private or raw network payloads are uploaded.
"""
import json
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

CONFIG = json.loads(Path("pages.json").read_text(encoding="utf-8"))
REPORT = Path("results/json_probe.json")

def traverse(obj, path, seen, messages, limit=300000):
    if len(seen) > limit:
        return
    if isinstance(obj, dict):
        if isinstance(obj.get("text"), str):
            value = obj["text"].strip()
            if len(value) >= 80:
                key = value[:160]
                if key not in seen:
                    seen.add(key)
                    messages.append({
                        "path": ".".join(path[-7:]),
                        "text_sample": value[:350],
                        "length": len(value),
                        "sibling_keys": list(obj.keys())[:12],
                    })
        for key, value in obj.items():
            traverse(value, path + [str(key)], seen, messages, limit)
    elif isinstance(obj, list):
        for value in obj[:20000]:
            traverse(value, path + ["[]"], seen, messages, limit)

def main():
    records = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for item in CONFIG["pages"]:
            if not item.get("enabled", True):
                continue
            ctx = browser.new_context(locale="en-US", viewport={"width": 1365, "height": 900})
            page = ctx.new_page()
            record = {"source": item["name"]}
            try:
                page.goto(item["url"], wait_until="domcontentloaded", timeout=35000)
                page.wait_for_timeout(3500)
                script_texts = page.locator('script[type="application/json"]').all_text_contents()
                record["json_scripts"] = len(script_texts)
                record["parsed_scripts"] = 0
                record["json_strings"] = []
                seen = set()
                msgs = []
                for t in script_texts:
                    try:
                        obj = json.loads(t)
                        record["parsed_scripts"] += 1
                        traverse(obj, [], seen, msgs)
                    except Exception:
                        pass
                record["candidate_text_nodes"] = len(msgs)
                record["samples"] = sorted(msgs, key=lambda x: x["length"], reverse=True)[:12]
                record["rendered_articles"] = []
                for article in page.locator('[role="article"]').all()[:10]:
                    record["rendered_articles"].append(article.inner_text()[:350])
                print(f'JSON_PROBE {item["name"]}: parsed={record["parsed_scripts"]} text_nodes={len(msgs)} articles={len(record["rendered_articles"])}', flush=True)
                for candidate in record["samples"][:5]:
                    print(f'JSON_SAMPLE: {candidate["path"]} {candidate["text_sample"][:200]!r}', flush=True)
            except Exception as exc:
                record["error"] = f"{type(exc).__name__}: {exc}"
                print(f'PROBE_ERROR {item["name"]}: {exc}', flush=True)
            finally:
                ctx.close()
            records.append(record)
        browser.close()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")

if __name__ == "__main__":
    main()
