import os
import time

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

IG_BRAND_NAME = os.getenv("IG_BRAND_NAME", "IG")
IG_DASHBOARD_URL = os.getenv("IG_DASHBOARD_URL", "")
PROFILE = os.path.join(BASE_DIR, "browser-profile")

if not IG_DASHBOARD_URL:
    raise SystemExit("Set IG_DASHBOARD_URL in .env")

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        PROFILE,
        headless=False,
        viewport={"width": 1400, "height": 1000}
    )

    page = context.pages[0] if context.pages else context.new_page()

    page.goto(IG_DASHBOARD_URL, wait_until="domcontentloaded")

    print()
    print("==============================================")
    print(f" {IG_BRAND_NAME.upper()} MONITOR SETUP")
    print("==============================================")
    print()
    print("A browser window has opened.")
    print()
    print(f"1. Log into {IG_BRAND_NAME}.")
    print("2. Go to Available Assignments.")
    print("3. Set Location to VIC.")
    print("4. Make sure the assignment list is visible.")
    print()
    print("When you're ready, come back to Terminal")
    print("and press ENTER.")
    print()

    input()

    # Save a snapshot of the page text for us to inspect
    text = page.locator("body").inner_text()

    with open(os.path.join(BASE_DIR, "dashboard_snapshot.txt"), "w", encoding="utf-8") as f:
        f.write(text)

    print("Snapshot saved as:")
    print("dashboard_snapshot.txt")
    print()
    print("Current page:", page.url)
    print()
    print("The browser will remain open.")
    print("Press Ctrl+C in Terminal when finished.")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        context.close()
