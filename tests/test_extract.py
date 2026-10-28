import pytest

from pf_hours.extract import ExtractError, extract, structured_club
from pf_hours.hours import Window
from conftest import SLUGS

SEED = {"Monday": "24 hours", "Tuesday": "24 hours", "Wednesday": "24 hours", "Thursday": "24 hours",
        "Friday": "12:00 AM – 10:00 PM", "Saturday": "6:00 AM – 10:00 PM", "Sunday": "6:00 AM – 10:00 PM"}


@pytest.mark.parametrize("key", ["milwaukie", "se_82nd"])
def test_each_gym_parses_independently_and_matches_seed(pages, key):
    club = extract(pages[key], SLUGS[key])
    assert club.slug == SLUGS[key]
    assert club.weekly_source == "structured+dom"  # page data and visible block agree
    assert club.weekly.as_text() == SEED
    assert club.weekly.days[0] == (Window(0, 1440),)  # Monday 24h, not zero
    assert [h["holidayName"] for h in club.holidays_raw] == ["Easter", "Memorial Day", "Independence Day", "Labor Day"]


def test_wrong_gym_page_is_rejected(pages):
    with pytest.raises(ExtractError, match="expected"):
        extract(pages["milwaukie"], SLUGS["se_82nd"])


def test_cloudflare_challenge_is_rejected(challenge_html):
    with pytest.raises(ExtractError, match="Cloudflare"):
        extract(challenge_html, SLUGS["milwaukie"])


def test_visible_and_structured_hours_must_agree(pages):
    html = pages["milwaukie"].replace("Fri: 12:00 AM - 10:00 PM", "Fri: 12:00 AM - 9:00 PM")
    with pytest.raises(ExtractError, match="disagree"):
        extract(html, SLUGS["milwaukie"])


def test_dom_only_fallback_has_no_holiday_data(pages):
    html = pages["milwaukie"].replace("streamController.enqueue(", "streamController.ignored(")
    club = extract(html, SLUGS["milwaukie"])
    assert club.weekly_source == "dom" and club.holidays_raw is None
    assert club.weekly.as_text() == SEED


def test_page_without_any_hours_fails(pages):
    html = pages["milwaukie"].replace("streamController.enqueue(", "x(").replace("Club Hours", "Amenities")
    with pytest.raises(ExtractError, match="no Club Hours"):
        extract(html, SLUGS["milwaukie"])


def test_structured_data_reads_only_hours_fields(pages):
    club = structured_club(pages["se_82nd"])
    assert club["clubSlug"] == SLUGS["se_82nd"]
    assert set(club["clubHours"]) == {"workingDays", "weekends", "status"}
