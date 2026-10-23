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
                page.wait_for_timeout(4000)
                # A nested comment can be role=article; keep only top-level containers.
                articles = page.locator('[role="article"]')
                article_count = articles.count()
                extracted = []
                for i in range(min(article_count, 30)):
                    article = articles.nth(i)
                    if article.evaluate("el => Boolean(el.parentElement?.closest('[role=article]'))"):
                        continue
                    expanded = False
                    try:
                        expanded = expand_post(article)
                        if expanded:
                            page.wait_for_timeout(700)
                    except Exception as exc:
                        print(f'EXPAND_WARNING: {type(exc).__name__}: {exc}', flush=True)
                    try:
                        data = article_data(article, page.url)
                    except Exception as exc:
                        print(f'EXTRACT_WARNING: {type(exc).__name__}: {exc}', flush=True)
                        continue
                    text = data['text']
                    if not text:
                        continue
                    still_truncated = 'see more' in text.lower()
                    post = {
                        'text': text,
                        'length': len(text),
                        'expanded': expanded,
                        'still_truncated': still_truncated,
                        'post_links': data['links'],
                        'nested_comments': data['nested_comments'],
                    }
                    extracted.append(post)
                    print(f'POST_CANDIDATE_{len(extracted)}: chars={len(text)}, expanded={expanded}, truncated={still_truncated}, links={len(data["links"])}', flush=True)
                    print(f'TEXT_PREVIEW: {text[:350]!r}', flush=True)
                print(f'HTTP_STATUS: {response.status if response else "unknown"}', flush=True)
                print(f'ARTICLES_FOUND: {article_count}', flush=True)
                print(f'TOP_LEVEL_CANDIDATES: {len(extracted)}', flush=True)
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
