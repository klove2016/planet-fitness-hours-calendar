"""End-to-end acceptance tests of the refresh pipeline (no network, no browser)."""
import json
from datetime import timedelta

import pytest
from icalendar import Calendar

from pf_hours import cli
from pf_hours.hours import WeeklyHours
from conftest import NOW, ROOT, TODAY

MILW, SE82, BOTH = ("planet_fitness_milwaukie_hours.ics", "planet_fitness_se_82nd_hours.ics",
                    "planet_fitness_both_hours.ics")


def read(docs, name):
    return (docs / name).read_bytes().decode("utf-8")


def events(docs, name):
    return Calendar.from_ical(read(docs, name)).walk("VEVENT")


@pytest.fixture
def published(tmp_path, cfg, fetcher):
    code, _ = cli.refresh(cfg, tmp_path, fetcher(), NOW, TODAY)
    assert code == 0
    return tmp_path


def test_publishes_two_gym_feeds_and_combined(published):
    st = json.loads(read(published, "status.json"))
    assert st["status"] == "warning"  # unconfirmed Thanksgiving/Christmas/New Year
    assert set(st["gyms"]) == {"milwaukie", "se_82nd"}
    for key, f in (("milwaukie", MILW), ("se_82nd", SE82)):
        g = st["gyms"][key]
        assert g["status"] == "warning" and g["stale"] is False and g["events"] == 90
        assert g["extraction"]["weekly_source"] == "structured+dom"
        assert len(events(published, f)) == 90
    both = events(published, BOTH)
    assert len(both) == 180
    assert {str(e["SUMMARY"]).split(":")[0] for e in both} == {"PF Milwaukie", "PF SE 82nd"}


def test_rerun_is_idempotent(published, cfg, fetcher):
    before = {f: read(published, f) for f in (MILW, SE82, BOTH)}
    code, st = cli.refresh(cfg, published, fetcher(), NOW + timedelta(hours=2), TODAY)
    assert code == 0
    assert {f: read(published, f) for f in (MILW, SE82, BOTH)} == before
    assert not st["gyms"]["milwaukie"]["file_changed"] and not st["combined"]["changed"]


def test_one_gym_failing_does_not_block_the_other(published, cfg, fetcher, pages):
    milw_before = read(published, MILW)
    changed = pages["se_82nd"].replace("12:00 AM - 10:00 PM", "12:00 AM - 9:00 PM")
    code, st = cli.refresh(cfg, published, fetcher(milwaukie=None, se_82nd=changed), NOW + timedelta(days=1),
                           TODAY + timedelta(days=1))
    assert code == 1
    m, s = st["gyms"]["milwaukie"], st["gyms"]["se_82nd"]
    assert m["status"] == "error" and m["stale"] is True and "FetchError" in m["errors"][0]
    assert read(published, MILW) == milw_before  # last-good kept
    assert s["status"] == "warning" and s["hours"]["Friday"] == "12:00 AM – 9:00 PM" and s["hours_changed"]
    fri = [e for e in events(published, SE82) if e.decoded("DTSTART").weekday() == 4]
    assert fri and all(e.decoded("DTEND").hour == 21 for e in fri)
    # Combined = last-good Milwaukie + fresh SE 82nd.
    both = read(published, BOTH)
    assert "PF Milwaukie" in both and "SE 82nd: Open 12:00 AM – 9:00 PM" in both
    assert s["history"][0]["previous_hours"]["Friday"] == "12:00 AM – 10:00 PM"


def test_cloudflare_block_keeps_last_good(published, cfg, fetcher, challenge_html):
    before = {f: read(published, f) for f in (MILW, SE82, BOTH)}
    code, st = cli.refresh(cfg, published, fetcher(milwaukie=challenge_html, se_82nd=challenge_html),
                           NOW + timedelta(days=1), TODAY)
    assert code == 1 and st["status"] == "error" and st["stale"] is True
    assert {f: read(published, f) for f in (MILW, SE82, BOTH)} == before
    assert st["gyms"]["milwaukie"]["hours"]["Monday"] == "24 hours"  # last-good info retained


def test_unparseable_hours_keep_last_good(published, cfg, fetcher, pages):
    before = read(published, MILW)
    broken = pages["milwaukie"].replace("12:00 AM - 10:00 PM", "Call club")
    code, st = cli.refresh(cfg, published, fetcher(milwaukie=broken), NOW + timedelta(days=1), TODAY)
    assert code == 1 and st["gyms"]["milwaukie"]["status"] == "error"
    assert read(published, MILW) == before
    assert st["gyms"]["se_82nd"]["status"] == "warning"


def test_implausible_hours_keep_last_good(published, cfg, fetcher, pages):
    before = read(published, MILW)
    tiny = pages["milwaukie"].replace("6:00 AM - 10:00 PM", "6:00 AM - 6:30 AM")
    code, st = cli.refresh(cfg, published, fetcher(milwaukie=tiny), NOW + timedelta(days=1), TODAY)
    assert code == 1 and "implausible" in st["gyms"]["milwaukie"]["errors"][0]
    assert read(published, MILW) == before


def test_published_holiday_hours_override_weekly(published, cfg, fetcher, pages):
    html = pages["milwaukie"].replace("Independence Day", "Thanksgiving").replace("July 4th", "November 26th")
    code, st = cli.refresh(cfg, published, fetcher(milwaukie=html), NOW + timedelta(days=1), TODAY)
    assert code == 0
    g = st["gyms"]["milwaukie"]
    assert {"date": "2026-11-26", "holiday": "Thanksgiving", "hours": "6:00 AM – 7:00 PM"} in g["holiday_overrides_in_horizon"]
    assert "Thanksgiving" not in {h["holiday"] for h in g["unconfirmed_holidays"]}
    tg = [e for e in events(published, MILW) if str(e.decoded("DTSTART"))[:10] == "2026-11-26"]
    assert len(tg) == 1 and str(tg[0]["SUMMARY"]) == "PF Milwaukie: Open 6:00 AM – 7:00 PM (Thanksgiving hours)"
    assert st["gyms"]["se_82nd"]["unconfirmed_holidays"][0]["holiday"] == "Thanksgiving"  # independent


def test_large_event_drop_requires_override(published, cfg, fetcher, monkeypatch):
    # Site suddenly shows the club closed Monday-Saturday: ~85% fewer events -> suspicious, not published.
    real = cli.extract

    def sunday_only(html, slug):
        club = real(html, slug)
        if slug == "milwaukie-hwy-224-se-oak-st-or":
            club.weekly = WeeklyHours(((),) * 6 + (club.weekly.days[6],))
        return club

    monkeypatch.setattr(cli, "extract", sunday_only)
    before = read(published, MILW)
    code, st = cli.refresh(cfg, published, fetcher(), NOW + timedelta(days=1), TODAY)
    assert code == 1 and "would drop" in st["gyms"]["milwaukie"]["errors"][0]
    assert read(published, MILW) == before
    code, st = cli.refresh(cfg, published, fetcher(), NOW + timedelta(days=1), TODAY, allow_large_change=True)
    assert code == 0 and len(events(published, MILW)) < 20


def test_dry_run_writes_nothing(tmp_path, cfg, fetcher):
    code, _ = cli.refresh(cfg, tmp_path, fetcher(), NOW, TODAY, dry_run=True)
    assert code == 0 and list(tmp_path.iterdir()) == []


def test_repo_is_self_contained():
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text()
        assert "ncprd" not in text.lower() and "aquatic" not in text.lower(), path
