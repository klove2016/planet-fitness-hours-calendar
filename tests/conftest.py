from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from pf_hours.config import load_config
from pf_hours.fetch import FetchError

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
TODAY = date(2026, 10, 28)
NOW = datetime(2026, 10, 28, 14, 7, tzinfo=timezone.utc)
SLUGS = {"milwaukie": "milwaukie-hwy-224-se-oak-st-or", "se_82nd": "portland-se-82nd-clackamas-or"}


@pytest.fixture(scope="session")
def cfg():
    return load_config(ROOT / "config" / "config.toml")


@pytest.fixture(scope="session")
def pages() -> dict[str, str]:
    return {
        "milwaukie": (FIXTURES / "milwaukie_2026-10-28.html").read_text(),
        "se_82nd": (FIXTURES / "se_82nd_2026-10-28.html").read_text(),
    }


@pytest.fixture(scope="session")
def challenge_html() -> str:
    return (FIXTURES / "cloudflare_challenge.html").read_text()


class FakeFetcher:
    """gym url -> html; a missing entry fails like a blocked/timeout fetch."""

    def __init__(self, cfg, by_key: dict[str, str]):
        self.by_url = {g.url: by_key[g.key] for g in cfg.gyms if g.key in by_key}

    def get(self, url):
        if url not in self.by_url:
            raise FetchError(f"{url}: gave up after 4 attempts: Timeout 45000ms exceeded")
        return self.by_url[url], NOW


@pytest.fixture
def fetcher(cfg, pages):
    def _make(**overrides):
        merged = {**pages, **overrides}
        return FakeFetcher(cfg, {k: v for k, v in merged.items() if v is not None})
    return _make
