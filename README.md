# Facebook → RSS (free, GitHub-hosted)

A best-effort monitor for public Facebook Pages. It uses Playwright without Facebook login, stores new post URLs and their expanded text, and generates a single RSS 2.0 feed.

## Feeds

- Generated file: [docs/feed.xml](docs/feed.xml)
- Intended GitHub Pages URL: https://geomack.github.io/facebook-rss/feed.xml
- GitHub Pages must be configured once: **Settings → Pages → Deploy from a branch → main → /docs → Save**.

## Manage sources

Edit [pages.json](pages.json). Each entry has:
- `name`: display name.
- `url`: public Facebook Page URL.
- `enabled`: `true` or `false` to include/exclude a source.

Example:
```json
{
  "pages": [
    {"name": "NASA", "url": "https://www.facebook.com/NASA", "enabled": true}
  ],
  "posts_per_page": 3
}
```

A change to `pages.json` automatically triggers a build.

## Automation

The [Build Facebook RSS](.github/workflows/rss.yml) workflow runs every 2 hours (GitHub schedules are best-effort) and whenever `pages.json`, the collector, or workflow code changes.

- `check_facebook.py`: existing Playwright diagnostic collector.
- `rss_monitor.py`: cleans, deduplicates, saves the content and builds XML.
- `state/posts.json`: persistent post registry (not manually edited).
- `docs/feed.xml`: generated RSS.
- `results/`: temporary diagnostics in Actions artifacts.

## Limitations

- Only publicly visible content accessible to GitHub-hosted Chromium can be collected.
- A successful workflow does **not** guarantee every recent post was visible or captured.
- Facebook may restrict or change access and rendering.
- Only items with an identifiable Facebook permalink and expanded text are included.
- Feed `pubDate` is when an item was **first detected** by the monitor, not necessarily the original Facebook publishing time.
- RSS on public GitHub Pages is publicly readable. Respect source rights and platform rules.
