"""One-time setup for the isolated Reteller lab.
Opens a dedicated browser for a manual Facebook login and, with explicit approval,
passes Facebook-only Playwright storage state directly to GitHub Actions secrets.
Never saves the session to disk or prints the secret.
"""
import argparse
import base64
import json
import subprocess
import sys

from playwright.sync_api import sync_playwright

REPO = "geomack/facebook-rss"
SECRET = "FB_STORAGE_STATE_B64"
PROFILE = "https://www.facebook.com/reteller"


def check_gh():
    result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if result.returncode:
        print("GitHub CLI is not authenticated. Run: gh auth login", file=sys.stderr)
        return False
    access = subprocess.run(
        ["gh", "repo", "view", REPO, "--json", "nameWithOwner"],
        capture_output=True, text=True,
    )
    if access.returncode:
        print("GitHub repository is inaccessible to gh.", file=sys.stderr)
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="One-time Facebook authentication in a separate browser")
    parser.add_argument("--browser", choices=("chrome", "firefox", "webkit", "chromium"),
                        default="chrome", help="chrome=installed genuine Google Chrome")
    args = parser.parse_args()
    if not check_gh():
        return 1

    print("Opening a SEPARATE " + args.browser + " browser. Sign in to Facebook there.")
    print("This script does NOT read any cookies from your existing Chrome.")
    print("Do not close the new browser until this script finishes.")
    with sync_playwright() as pw:
        if args.browser == "chrome":
            browser = pw.chromium.launch(channel="chrome", headless=False)
        else:
            browser = getattr(pw, args.browser).launch(headless=False)
        context = browser.new_context(locale="ru-RU", viewport={"width": 1365, "height": 900})
        page = context.new_page()
        page.goto(PROFILE, wait_until="domcontentloaded", timeout=60000)

        while True:
            input("After you log in and see Denis Karavaev's profile, press Enter here...")
            state = context.storage_state()
            cookies = [
                c for c in state["cookies"]
                if c.get("domain", "").lower().lstrip(".") in
                ("facebook.com", "www.facebook.com", "m.facebook.com")
                or c.get("domain", "").lower().endswith(".facebook.com")
            ]
            # Required auth signals without printing cookies.
            cookie_names = {c["name"] for c in cookies}
            if {"c_user", "xs"}.issubset(cookie_names):
                # Cookies may exist even when Facebook has not cleared its checkpoint.
                try:
                    page.goto(PROFILE, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(1500)
                    if "/checkpoint" not in page.url and "/login" not in page.url and (
                        page.locator('[role="article"]').count() > 0
                        or page.locator('a[href*="/following/"]').count() > 0
                    ):
                        break
                except Exception:
                    pass
                print("Facebook login exists, but the profile is not accessible yet.")
            else:
                print("No active Facebook session detected in the new browser.")
            choice = input("Press Enter to try again, or type q to quit: ").strip().lower()
            if choice == "q":
                browser.close()
                return 1

        payload = {
            "cookies": cookies,
            "origins": [],
        }
        encoded = base64.b64encode(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        print()
        print("Login detected. Session cookies can grant access to your Facebook account.")
        print("Destination: GitHub Actions secret " + SECRET + " in " + REPO)
        print("No password, cookies or tokens will be shown in terminal output.")
        print("The secret will be accessible to workflows permitted to use repository secrets.")
        answer = input("Type YES to upload the session securely to GitHub (anything else cancels): ")
        if answer != "YES":
            print("Cancelled. Nothing uploaded.")
            browser.close()
            return 0

        result = subprocess.run(
            ["gh", "secret", "set", SECRET, "--repo", REPO],
            input=encoded,
            capture_output=True, text=True,
        )
        browser.close()
        encoded = ""
        payload = {}
        if result.returncode:
            print("GitHub secret upload failed: " + result.stderr.strip(), file=sys.stderr)
            return 1
        print("Success: GitHub secret is configured. No local session file was created.")
        print("Starting the isolated cloud test now...")
        launch = subprocess.run(
            ["gh", "workflow", "run", "reteller-lab.yml", "--repo", REPO,
             "--ref", "main"], capture_output=True, text=True
        )
        if launch.returncode:
            print("Session saved, but workflow start failed: " + launch.stderr.strip())
            print("Run manually: gh workflow run reteller-lab.yml --repo " + REPO)
            return 1
        print("Cloud test requested. Check https://github.com/" + REPO +
              "/actions/workflows/reteller-lab.yml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
