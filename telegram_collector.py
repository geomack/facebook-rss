"""Collect publicly available Telegram channel messages from t.me/s/ (no bot token)."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

CONFIG = Path("telegram.json")
RESULTS = Path("results/telegram_posts.json")
MAX_BYTES = 3_000_000

def fetch_html(url):
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "uk,en-US;q=0.9,en;q=0.8",
    })
    with urlopen(request, timeout=25) as resp:
        data = resp.read(MAX_BYTES)
        if resp.status != 200:
            raise RuntimeError("Unexpected HTTP status " + str(resp.status))
        return data.decode("utf-8", errors="replace")

def collect_channel(channel, initial_limit):
    username = channel["username"].strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        raise ValueError("Invalid public Telegram channel name")
    url = "https://t.me/s/" + username
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    nodes = soup.select(".tgme_widget_message[data-post]")
    items = {}
    for node in nodes:
        post_id = node.get("data-post", "")
        if not re.fullmatch(re.escape(username) + r"/\d+", post_id, flags=re.I):
            continue
        content_node = node.select_one(".tgme_widget_message_text") or node.select_one(".tgme_widget_message_caption")
        if content_node is None:
            continue
        content = content_node.get_text("\n", strip=True)
        if not content:
            continue
        date_node = node.select_one("time[datetime]")
        published = date_node.get("datetime") if date_node else None
        if published:
            try:
                published = datetime.fromisoformat(published.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
            except ValueError:
                published = None
        permalink = "https://t.me/" + post_id
        items[permalink] = {
            "url": permalink,
            "source": channel["name"],
            "source_type": "telegram",
            "title": channel["name"] + ": " + content.split("\n", 1)[0][:110],
            "text": content,
            "published_at": published,
        }
    entries = sorted(items.values(), key=lambda x: int(x["url"].rsplit("/", 1)[-1]))
    return entries[-initial_limit:], {"url":url,"html_bytes":len(html),"messages_in_html":len(nodes),"text_posts":len(items)}

def main():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    limit = max(1, min(int(config.get("initial_posts_per_channel", 12)), 30))
    output = []
    for channel in config.get("telegram", []):
        if not channel.get("enabled", True):
            continue
        try:
            posts, metrics = collect_channel(channel, limit)
            print("TELEGRAM_SOURCE " + json.dumps({"name":channel["name"], **metrics, "selected":len(posts)}, ensure_ascii=False), flush=True)
            for post in posts[-3:]:
                print("TELEGRAM_SAMPLE " + post["url"] + " chars=" + str(len(post["text"])) + " title=" + post["title"][:140],flush=True)
            output.append({"name":channel["name"],"posts":posts,"metrics":metrics})
        except Exception as exc:
            print("TELEGRAM_ERROR {}: {}: {}".format(channel["name"], type(exc).__name__,exc),flush=True)
            output.append({"name":channel["name"],"posts":[],"error":str(exc)})
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

if __name__ == "__main__":
    main()
