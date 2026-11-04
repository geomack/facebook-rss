import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright

CONFIG_FILE = Path('pages.json')
OUTPUT_FILE = Path('results/facebook_browser_test.json')
POST_PATTERNS = ('/posts/', '/videos/', '/reel/', '/permalink/', '/photo/', 'story_fbid=', '/watch/?v=')


def top_level_articles(page):
    return page.locator('[role="article"]').filter(
        has_not=page.locator('xpath=ancestor::*[@role="article"]')
    )


def expand_post(article):
    # Click only the actual text-expansion control, not "View more comments".
    return article.evaluate(r'''async root => {
      const els = [...root.querySelectorAll('div, span, a, button')];
      const candidates = els.filter(el => {
        const text = (el.innerText || '').trim();
        return /^(?:…\s*)?See more$/i.test(text) &&
               ![...el.children].some(c => /^(?:…\s*)?See more$/i.test((c.innerText || '').trim()));
      });
      if (!candidates.length) return false;
      candidates[0].click();
      return true;
    }''')


def article_data(article, base_url):
    return article.evaluate(r'''(root, baseUrl) => {
      const nested = [...root.querySelectorAll('[role="article"]')];
      let text = (root.innerText || '').trim();
      for (const comment of nested) {
        const commentText = (comment.innerText || '').trim();
        if (commentText) {
          const pos = text.indexOf(commentText);
          if (pos !== -1) text = text.slice(0, pos).trim();
        }
      }
      text = text.split(/\nAll reactions:/i)[0].trim();
      text = text.replace(/\n(?:Like|Comment|Share)\s*$/gi, '').trim();
      const links = [...root.querySelectorAll('a[href]')].map(a => {
        try { return new URL(a.getAttribute('href'), baseUrl).href; }
        catch { return null; }
      }).filter(Boolean);
      const postLinks = links.filter(h => /\/posts\/|\/videos\/|\/reel\/|\/permalink\/|\/photo\/|story_fbid=|\/watch\/\?v=/.test(h));
      return {text, links: [...new Set(postLinks)], nested_comments: nested.length};
    }''', base_url)


def main():
    config = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for item in config.get('pages', []):
            if not item.get('enabled', True):
                continue
            name, url = item.get('name', 'Unknown'), item.get('url')
            if not url:
                continue
            print(f'\nCHECKING: {name}\nURL: {url}', flush=True)
            context = browser.new_context(locale='en-US', viewport={'width': 1365, 'height': 900})
            page = context.new_page()
            try:
                response = page.goto(url, wait_until='domcontentloaded', timeout=45000)
                page.wait_for_timeout(3500)
                extracted_by_key = {}
                article_count = 0
                stable_rounds = 0
                previous_count = 0

                # Collect on EVERY scroll, not just at the end. Facebook may
                # virtualize earlier posts and remove them from the live DOM.
                for scroll in range(12):
                    articles = page.locator('[role="article"]')
                    count = min(articles.count(), 100)
                    article_count = max(article_count, count)
                    for i in range(count):
                        article = articles.nth(i)
                        try:
                            if article.evaluate("el => Boolean(el.parentElement?.closest('[role=article]'))"):
                                continue
                            expanded = expand_post(article)
                            if expanded:
                                page.wait_for_timeout(250)
                            data = article_data(article, page.url)
                        except Exception as exc:
                            print(f'ARTICLE_WARNING: {type(exc).__name__}: {exc}', flush=True)
                            continue
                        text = data.get('text', '').strip()
                        links = data.get('links', [])
                        if not text or not links:
                            continue
                        # A Facebook permalink, not a nested comment, must be present.
                        primary = next((link.split('?')[0] for link in links if
                                        '/posts/' in link or '/reel/' in link or
                                        '/videos/' in link or '/permalink/' in link), None)
                        if not primary:
                            primary = next((link.split('?')[0] for link in links), None)
                        if not primary:
                            continue
                        still_truncated = 'see more' in text.lower()
                        post = {
                            'text': text,
                            'length': len(text),
                            'expanded': expanded,
                            'still_truncated': still_truncated,
                            'post_links': links,
                            'nested_comments': data.get('nested_comments', 0),
                        }
                        old = extracted_by_key.get(primary)
                        if old is None or (old['still_truncated'] and not still_truncated) or (
                            old['still_truncated'] == still_truncated and len(text) > old['length']
                        ):
                            extracted_by_key[primary] = post
                    print(f'SCROLL_{scroll + 1}: dom_articles={count} unique_posts={len(extracted_by_key)}', flush=True)
                    if len(extracted_by_key) <= previous_count:
                        stable_rounds += 1
                    else:
                        stable_rounds = 0
                    previous_count = len(extracted_by_key)
                    if stable_rounds >= 4 and scroll >= 5:
                        break
                    page.mouse.wheel(0, 1450)
                    page.wait_for_timeout(1500)

                extracted = list(extracted_by_key.values())
                print(f'PAGE_TITLE: {page.title()}', flush=True)
                print(f'PAGE_BODY_PREVIEW: {page.locator("body").inner_text(timeout=10000)[:600]!r}', flush=True)
                for idx, post in enumerate(extracted, 1):
                    print(f'POST_CANDIDATE_{idx}: chars={post["length"]} expanded={post["expanded"]} truncated={post["still_truncated"]} links={len(post["post_links"])}', flush=True)
                    print(f'TEXT_PREVIEW: {post["text"][:240]!r}', flush=True)
                print(f'HTTP_STATUS: {response.status if response else "unknown"}', flush=True)
                print(f'ARTICLES_FOUND: {article_count}', flush=True)
                print(f'TOP_LEVEL_CANDIDATES: {len(extracted)}', flush=True)
                print(f'SOURCE_SUMMARY: {name}: candidates={len(extracted)}', flush=True)
                print(f'SOURCE_SUMMARY: {name}: articles={article_count} candidates={len(extracted)}', flush=True)
                results.append({
                    'name': name,
                    'url': url,
                    'status': response.status if response else None,
                    'final_url': page.url,
                    'articles_found': article_count,
                    'posts': extracted,
                })
            except Exception as exc:
                print(f'ERROR: {type(exc).__name__}: {exc}', flush=True)
                results.append({'name': name, 'url': url, 'error': str(exc)})
            finally:
                context.close()
        browser.close()
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'RESULTS_SAVED: {OUTPUT_FILE}', flush=True)
