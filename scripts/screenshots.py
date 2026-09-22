"""Capture screenshots of the running --demo app for docs/media.

Usage: python scripts/screenshots.py [base_url]
Requires the app already running with --demo (see README).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "docs" / "media"


def shoot(page, path: Path) -> None:
    page.screenshot(path=str(path))
    print("wrote", path)


def main() -> None:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18815"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    import glob

    candidates = glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome")
    exe = candidates[0] if candidates else None

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)

        page.goto(base_url, wait_until="networkidle")
        page.wait_for_selector("text=Babel's Hoard")
        page.fill("input", "Client get request")
        time.sleep(0.6)
        shoot(page, OUT_DIR / "search.png")

        page.click("text=httpx.Client.get")
        time.sleep(0.5)
        shoot(page, OUT_DIR / "lookup-detail.png")
        page.click(".detail-backdrop")
        time.sleep(0.2)

        page.locator(".nav-item").nth(1).click()  # Libraries
        time.sleep(0.4)
        shoot(page, OUT_DIR / "libraries.png")

        page.locator(".nav-item").nth(2).click()  # Check code
        time.sleep(0.3)
        page.fill(
            "textarea",
            "import httpx\n\nclient = httpx.Client()\nclient.get('https://example.com', bogus_kw=1)\n",
        )
        page.locator(".card button.btn-primary").click()
        page.wait_for_selector(".finding", timeout=5000)
        time.sleep(0.3)
        shoot(page, OUT_DIR / "check-code.png")

        browser.close()


if __name__ == "__main__":
    main()
