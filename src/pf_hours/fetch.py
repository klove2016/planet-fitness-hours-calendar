"""Load gym pages in headless Chromium (planetfitness.com answers plain HTTP clients with a
Cloudflare browser check). Retries with backoff; waits for the club data, not a fixed sleep."""
from __future__ import annotations

import time
from datetime import datetime, timezone


class FetchError(Exception):
    pass


class BrowserFetcher:
    def __init__(self, browser_cfg: dict):
        self.cfg = browser_cfg
        self._pw = self._browser = self._ctx = None
        self._last = 0.0

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._ctx = self._browser.new_context(
            user_agent=self.cfg["user_agent"], locale="en-US", timezone_id="America/Los_Angeles",
            viewport={"width": 1280, "height": 900},
        )
        return self

    def __exit__(self, *exc):
        for closer in (self._ctx, self._browser):
            try:
                closer and closer.close()
            except Exception:
                pass
        if self._pw:
            self._pw.stop()

    def get(self, url: str) -> tuple[str, datetime]:
        from playwright.sync_api import Error as PlaywrightError

        attempts = int(self.cfg["retries"]) + 1
        last_err = None
        for attempt in range(1, attempts + 1):
            wait = float(self.cfg["min_interval_seconds"]) - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            page = self._ctx.new_page()
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=int(self.cfg["timeout_ms"]))
                # Cloudflare's interstitial replaces itself with the real page; poll for the club data.
                deadline = time.monotonic() + int(self.cfg["ready_timeout_ms"]) / 1000
                html = page.content()
                while "clubHours" not in html and "Club Hours" not in html:
                    if time.monotonic() > deadline:
                        title = page.title()
                        what = "stuck on Cloudflare browser check" if "Just a moment" in title else f"title {title!r}"
                        raise FetchError(f"club data never appeared (HTTP {resp.status if resp else '?'}, {what})")
                    page.wait_for_timeout(1000)
                    html = page.content()
                return html, datetime.now(timezone.utc)
            except (PlaywrightError, FetchError) as e:
                last_err = e
                if attempt < attempts:
                    time.sleep(float(self.cfg["backoff_seconds"]) * attempt)
            finally:
                page.close()
        raise FetchError(f"{url}: gave up after {attempts} attempts: {last_err}")
