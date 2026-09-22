"""Capture screenshots of the running --demo app for docs/media.

Usage:
    python -m babels_hoard --demo --no-browser --port 18811
    python scripts/screenshots.py http://127.0.0.1:18811

Needs the Playwright Python package and a Chromium it can launch
(PLAYWRIGHT_BROWSERS_PATH). Writes 1440x900 PNGs, scale 1.
"""
from __future__ import annotations

import glob
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "docs" / "media"

CHECK_SNIPPET = """import httpx

with httpx.Client(timeout=10) as client:
    response = client.get("https://api.example.com/items", follow_redirects=True)
    response.raise_for_status()
    items = response.jsonify()
    client.post("https://api.example.com/items", json=items, retry=3)
"""


def shoot(page, name: str) -> None:
    path = OUT_DIR / name
    page.screenshot(path=str(path))
    print("wrote", path, f"{path.stat().st_size // 1024} KB")


def main() -> None:
    base_url = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8811").rstrip("/")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    candidates = sorted(glob.glob(f"{browsers}/chromium-*/chrome-linux/chrome"))
    exe = candidates[-1] if candidates else None

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1440, "height": 900}, device_scale_factor=1, locale="en-US", color_scheme="light"
        )
        page = context.new_page()

        page.goto(f"{base_url}/#/search", wait_until="networkidle")
        page.fill(".search-bar input", "send a get request")
        page.wait_for_selector(".result-item")
        time.sleep(0.4)
        shoot(page, "search.png")

        # "Ask the docs": only produces a real answer when a model is
        # connected (Settings -> Models); harmless (button stays disabled
        # with a reason) when nothing is configured.
        ask_button = page.locator("button", has_text="Ask the docs")
        if ask_button.count() and ask_button.first.is_enabled():
            ask_button.first.click()
            page.wait_for_selector(".ask-answer", timeout=15000)
            time.sleep(0.4)
            shoot(page, "ask.png")

        page.locator(".result-item", has_text="httpx.Client.get").first.click()
        page.wait_for_selector(".detail-panel .params-table")
        time.sleep(0.4)
        shoot(page, "lookup-detail.png")
        page.keyboard.press("Escape")

        page.goto(f"{base_url}/#/check", wait_until="networkidle")
        page.fill(".code-input", CHECK_SNIPPET)
        page.click(".toolbar .btn-primary")
        page.wait_for_selector(".check-result .finding")
        time.sleep(0.4)
        shoot(page, "check-code.png")

        page.goto(f"{base_url}/#/settings", wait_until="networkidle")
        page.wait_for_selector(".models-row")
        time.sleep(0.4)
        shoot(page, "models.png")

        page.goto(f"{base_url}/#/libraries", wait_until="networkidle")
        page.click(".disclosure")
        page.fill(".packages .input", "py")
        page.wait_for_selector(".package-scroll .lib-row")
        time.sleep(0.5)
        shoot(page, "libraries.png")

        browser.close()


if __name__ == "__main__":
    main()
