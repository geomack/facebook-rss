
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

CONFIG_FILE = "pages.json"
OUTPUT_FILE = Path("results/facebook_browser_test.json")


def main():
    with open(CONFIG_FILE, "r", encoding="utf-8") as file:
        config = json.load(file)

    pages = config.get("pages", [])
    results = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        for item in pages:
            if not item.get("enabled", True):
                continue

            name = item.get("name", "Unknown")
            url = item.get("url")

            if not url:
                continue

            print(f"\nCHECKING: {name}")
            print(f"URL: {url}")

            context = browser.new_context(
                locale="en-US",
                viewport={"width": 1365, "height": 900}
            )

            page = context.new_page()

            try:
                response = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=45000
                )

                page.wait_for_timeout(5000)

                status = response.status if response else None
                title = page.title()
                final_url = page.url

                articles = page.locator('[role="article"]').all()
                posts = []

                for article in articles[:10]:
                    text = article.inner_text(timeout=5000).strip()

                    if text:
                        posts.append({
                            "text": text,
                            "length": len(text)
                        })

                body_text = page.locator("body").inner_text(
                    timeout=10000
                )

                login_detected = any(
                    phrase in body_text.lower()
                    for phrase in [
                        "log in to facebook",
                        "log into facebook",
                        "you must log in"
                    ]
                )

                print(f"HTTP_STATUS: {status}")
                print(f"PAGE_TITLE: {title}")
                print(f"FINAL_URL: {final_url}")
                print(f"BODY_LENGTH: {len(body_text)}")
                print(f"LOGIN_DETECTED: {login_detected}")
                print(f"ARTICLES_FOUND: {len(articles)}")
                print(f"POSTS_EXTRACTED: {len(posts)}")

                results.append({
                    "name": name,
                    "url": url,
                    "status": status,
                    "title": title,
                    "final_url": final_url,
                    "login_detected": login_detected,
                    "body_length": len(body_text),
                    "articles_found": len(articles),
                    "posts": posts
                })

            except Exception as error:
                print(f"ERROR: {type(error).__name__}: {error}")

                results.append({
                    "name": name,
                    "url": url,
                    "error": str(error)
                })

            finally:
                context.close()

        browser.close()

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"\nRESULTS_SAVED: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
