"""Isolated Facebook public-post access diagnostic. Never publishes or changes the RSS."""
import argparse
import base64
import binascii
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results" / "reteller_lab.json"
URL = "https://www.facebook.com/reteller"
POST_RE = re.compile(r"/(?:posts/[^/?#]+|reel/\d+|videos/\d+)(?:[/?#]|$)")
LOGIN_RE = re.compile(r"(?:log into facebook|log in|войти в facebook|вход на facebook)", re.I)

EXTRACT_JS = r"""() => {
  const all = [...document.querySelectorAll('[role="article"]')];
  const articles = all.filter(el => !el.parentElement?.closest('[role="article"]'));
  const out = [];
  for (const root of articles) {
    const links = [...root.querySelectorAll('a[href]')].map(a => ({
      href: a.href, label: a.getAttribute('aria-label') || '', text: (a.innerText || '').trim()
    }));
    const permalink = links.find(a => /\/posts\/pfbid|\/reel\/\d+|\/videos\/\d+/.test(a.href));
    if (!permalink) continue;
    const message = [...root.querySelectorAll('[data-ad-preview="message"],[data-ad-comet-preview="message"]')]
      .find(el => el.closest('[role="article"]') === root);
    let text = message ? (message.innerText || '').trim() : '';
    if (!text) {
      const raw = (root.innerText || '').split(/\n(?:Like|Нравится|Комментировать|Comment|Share|Поделиться)(?:\n|$)/)[0];
      const lines = raw.split('\n').map(s => s.trim()).filter(Boolean);
      const skip = /^(?:Денис Караваев|Избранное|\d+|\d+ (?:мин\.|ч\.|дн\.)|Поделился\/-ась с Доступно всем|Смотреть другие комментарии|See more)$/i;
      text = lines.filter(s => !skip.test(s)).slice(0, 150).join('\n');
    }
    const stamp = root.querySelector('time[datetime]')?.getAttribute('datetime') || null;
    const dateLabel = (links.find(a => a.href === permalink.href)?.label || '') + ' ' +
      ([...root.querySelectorAll('[aria-label]')].map(e=>e.getAttribute('aria-label')||'')
        .find(v=>/\d{4}.*\d{1,2}:\d{2}|\d{1,2}:\d{2}.*\d{4}/.test(v))||'');
    const media = {
      photo: Boolean(root.querySelector('a[href*="/photo/"],img[src*="scontent"]')),
      video: /\/reel\/|\/videos\//.test(permalink.href) || Boolean(root.querySelector('video'))
    };
    out.push({url: permalink.href, text, datetime: stamp, date_label: dateLabel.trim(),
      public_label: /Доступно всем|Public/.test(root.innerText || ''), media, has_message_node: Boolean(message)});
  }
  return {dom_articles: all.length, posts: out};
}""";


def canonical(url):
    parsed = urlsplit(url)
    if parsed.hostname not in ("www.facebook.com", "facebook.com"):
        return None
    path = parsed.path.rstrip("/")
    if not POST_RE.search(path + "/"):
        return None
    return urlunsplit(("https", "www.facebook.com", path, "", ""))


def inspect(page, scrolls):
    seen = {}
    observations = []
    for iteration in range(scrolls + 1):
        sample = page.evaluate(EXTRACT_JS)
        new = 0
        for item in sample["posts"]:
            link = canonical(item["url"])
            if not link:
                continue
            item["url"] = link
            item["kind"] = "media_only" if not item["text"] else "text_or_caption"
            if not item["public_label"]:
                continue
            if link not in seen:
                seen[link] = item
                new += 1
            else:
                old = seen[link]
                if len(item["text"]) > len(old["text"]):
                    seen[link] = item
        observations.append({"iteration": iteration, "dom_articles": sample["dom_articles"],
                             "post_links": len(sample["posts"]), "new_unique": new,
                             "total_unique": len(seen)})
        if iteration < scrolls:
            page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 1.4, 1000))")
            page.wait_for_timeout(1800)
    return list(seen.values()), observations


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scrolls", type=int, default=5)
    args = parser.parse_args()
    if not 0 <= args.scrolls <= 12:
        raise ValueError("--scrolls must be between 0 and 12")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    result = {"source": URL, "checked_at": datetime.now(timezone.utc).isoformat(),
              "mode": "anonymous", "status": "unknown", "posts": [], "observations": []}
    state_encoded = os.getenv("FB_STORAGE_STATE_B64", "").strip()
    state = None
    if state_encoded:
        try:
            state = json.loads(base64.b64decode(state_encoded, validate=True))
            if not isinstance(state, dict) or not isinstance(state.get("cookies"), list):
                raise ValueError("Invalid Playwright storage state structure")
            # Never print, upload, or write the credential-bearing state.
            result["mode"] = "authenticated"
        except (ValueError, TypeError, binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
            result["status"] = "invalid_session_secret"
            OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
            print("RETELLER_LAB: invalid_session_secret (no credential value logged)")
            return 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context_options = {"locale": "ru-RU", "viewport": {"width": 1365, "height": 900}}
        if state:
            context_options["storage_state"] = state
        context = browser.new_context(**context_options)
        state = None
        page = context.new_page()
        try:
            response = page.goto(URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3500)
            body = page.locator("body").inner_text(timeout=10000)
            result["http_status"] = response.status if response else None
            result["final_url_path"] = urlsplit(page.url).path
            if "/login" in urlsplit(page.url).path or (LOGIN_RE.search(body) and
                    page.locator("input[type=password]").count()):
                result["status"] = "session_expired_or_blocked" if state_encoded else "login_required"
            else:
                posts, observations = inspect(page, args.scrolls)
                result["posts"] = posts
                result["observations"] = observations
                result["status"] = "posts_found" if posts else "no_public_posts_found"
        except Exception as exc:
            result["status"] = "error"
            result["error"] = type(exc).__name__  # Never log exception details containing request/session data
        finally:
            context.close()
            browser.close()
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("RETELLER_LAB: " + json.dumps({k: result[k] for k in (
        "source", "checked_at", "mode", "status", "http_status", "final_url_path")
        if k in result}, ensure_ascii=False))
    print("RETELLER_LAB_POST_COUNT: " + str(len(result["posts"])))
    for row in result["observations"]:
        print("RETELLER_LAB_SCROLL: " + json.dumps(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
