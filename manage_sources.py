"""Edit a public source using the inputs from the RSS workflow."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def source_key(feed, value):
    value = value.strip()
    if feed == "telegram":
        if re.match(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/", value, re.I):
            url = urlsplit(value if "://" in value else "https://" + value)
            parts = url.path.strip("/").split("/")
            if parts[0] == "s":
                parts.pop(0)
            if len(parts) > 2 or (len(parts) == 2 and not parts[1].isdigit()):
                raise ValueError("Enter a public Telegram channel or post link.")
            value = parts[0] if parts else ""
        username = value.removeprefix("@").casefold()
        if not re.fullmatch(r"[a-z0-9_]{5,32}", username):
            raise ValueError("Enter a public Telegram channel @username or t.me link.")
        return username

    url = urlsplit(value if "://" in value else "https://" + value)
    path = url.path.rstrip("/")
    if url.scheme not in ("http", "https") or url.netloc.casefold() not in (
        "facebook.com", "www.facebook.com", "m.facebook.com"
    ) or not path:
        raise ValueError("Enter the URL of a public Facebook page or profile.")
    if path.casefold() == "/profile.php":
        ids = parse_qs(url.query).get("id", [])
        if len(ids) != 1 or not re.fullmatch(r"[0-9]+", ids[0]):
            raise ValueError("The Facebook profile URL must include its numeric ID.")
        path = "/profile.php?id=" + ids[0]
    elif (not re.fullmatch(r"/[A-Za-z0-9._-]+", path)
          or path.casefold() in {"/reel", "/photo", "/watch", "/share", "/login", "/groups"}):
        raise ValueError("Enter the direct page or profile URL from Facebook.")
    return "https://www.facebook.com" + path.casefold()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("feed", choices=("facebook", "telegram"))
    parser.add_argument("action", choices=("add", "remove"))
    parser.add_argument("source")
    parser.add_argument("--name", default="")
    args = parser.parse_args()

    filename, collection, field = (
        ("pages.json", "pages", "url") if args.feed == "facebook"
        else ("telegram.json", "telegram", "username")
    )
    path = Path(__file__).resolve().with_name(filename)
    config = json.loads(path.read_text(encoding="utf-8"))
    try:
        key = source_key(args.feed, args.source)
        entry = next((item for item in config[collection]
                      if source_key(args.feed, item[field]) == key), None)
    except ValueError as exc:
        parser.error(str(exc))
    if args.action == "remove":
        if entry is None:
            parser.error("Source not found. Use the same URL or username as when adding it.")
        entry["enabled"] = False
    elif entry is not None:
        entry["enabled"] = True
    else:
        name = args.name.strip() or (
            "@" + key if args.feed == "telegram" else key.rsplit("/", 1)[-1]
        )
        if args.feed == "facebook" and any(item["name"] == name for item in config[collection]):
            parser.error("Choose a different display name for this Facebook source.")
        config[collection].append({"name": name, field: key, "enabled": True})
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{args.feed}: {args.action} {key}")


if __name__ == "__main__":
    main()
