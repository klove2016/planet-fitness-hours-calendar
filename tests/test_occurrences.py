from datetime import date, datetime, timedelta, timezone

import pytest
from icalendar import Calendar

from pf_hours.cli import to_ics_events
from pf_hours.hours import HolidayDay, Window, WeeklyHours, build_weekly
from pf_hours.ics import build_calendar
from pf_hours.occurrences import expand
from pf_hours.validate import ValidationError, validate_expansion, validate_ics
from conftest import TODAY
from test_hours import SEED

WATCH = ["Thanksgiving", "Christmas Eve", "Christmas", "New Year's Eve", "New Year's Day"]


@pytest.fixture
def gym(cfg):
    return cfg.gyms[0]


def run(cfg, gym, weekly=None, holidays=(), today=TODAY, days=90, watch=WATCH):
    exp = expand(gym, weekly or build_weekly(SEED), list(holidays), cfg.tz, today, days, watch)
    validate_expansion(exp, cfg.tz)
    return exp


def on(exp, d):
    return [e for e in exp.events if e.day == d]


def test_24h_days_are_full_midnight_to_midnight(cfg, gym):
    exp = run(cfg, gym)
    mon = on(exp, date(2026, 11, 2))
    assert len(mon) == 1 and mon[0].summary == "PF Milwaukie: Open 24 hours"
    assert mon[0].end - mon[0].start == timedelta(hours=24)
    assert mon[0].start.hour == 0 and mon[0].end.date() == date(2026, 11, 3) and mon[0].end.hour == 0


def test_weekly_pattern(cfg, gym):
    exp = run(cfg, gym, days=7)
    fri = on(exp, date(2026, 10, 30))[0]
    sat = on(exp, date(2026, 10, 31))[0]
    assert (fri.start.hour, fri.end.hour) == (0, 22)
    assert (sat.start.hour, sat.end.hour) == (6, 22)
    assert len(exp.events) == 7  # one window per day, no duplicates/overlaps


def test_dst_fall_back_keeps_local_hours(cfg, gym):
    exp = run(cfg, gym, days=7)
    sat, sun = on(exp, date(2026, 10, 31))[0], on(exp, date(2026, 11, 1))[0]
    assert sat.start.hour == sun.start.hour == 6
    assert sat.start.utcoffset() == timedelta(hours=-7) and sun.start.utcoffset() == timedelta(hours=-8)


def test_24h_day_on_dst_transitions(cfg, gym):
    every_day = build_weekly([("Daily", "12:00 AM - 12:00 AM", "24hrs")])
    fall = on(run(cfg, gym, every_day, today=date(2026, 11, 1), days=1), date(2026, 11, 1))[0]
    spring = on(run(cfg, gym, every_day, today=date(2027, 3, 14), days=1), date(2027, 3, 14))[0]
    utc = timezone.utc
    assert fall.end.astimezone(utc) - fall.start.astimezone(utc) == timedelta(hours=25)
    assert spring.end.astimezone(utc) - spring.start.astimezone(utc) == timedelta(hours=23)


def test_holiday_closure_supersedes_weekly(cfg, gym):
    xmas = HolidayDay(date(2026, 12, 25), "Christmas", True, (), "Closed")
    exp = run(cfg, gym, holidays=[xmas])
    day = on(exp, date(2026, 12, 25))
    assert len(day) == 1 and day[0].kind == "closed" and day[0].summary == "PF Milwaukie: CLOSED (Christmas)"
    assert day[0].start == date(2026, 12, 25) and day[0].end == date(2026, 12, 26)
    assert {"date": "2026-12-25", "holiday": "Christmas", "hours": "Closed"} in exp.overrides_applied
    assert "2026-12-25" not in {h["date"] for h in exp.unconfirmed_holidays}


def test_holiday_special_hours_supersede_weekly(cfg, gym):
    tg = HolidayDay(date(2026, 11, 26), "Thanksgiving", False, (Window(360, 840),), "Select hours")
    exp = run(cfg, gym, holidays=[tg])
    day = on(exp, date(2026, 11, 26))
    assert len(day) == 1
    assert (day[0].start.hour, day[0].end.hour) == (6, 14)
    assert day[0].summary == "PF Milwaukie: Open 6:00 AM – 2:00 PM (Thanksgiving hours)"


def test_unpublished_holidays_are_labelled_unconfirmed(cfg, gym):
    exp = run(cfg, gym)
    tg = on(exp, date(2026, 11, 26))[0]
    assert "holiday hours unconfirmed" in tg.summary and "UNCONFIRMED" in tg.note
    assert [h["holiday"] for h in exp.unconfirmed_holidays] == WATCH[:3] + ["New Year's Eve", "New Year's Day"]
    assert "unconfirmed" not in on(exp, date(2026, 11, 25))[0].summary


def test_past_holidays_do_not_apply(cfg, gym):
    old = HolidayDay(date(2026, 7, 4), "Independence Day", False, (Window(360, 1140),), "Select hours")
    assert run(cfg, gym, holidays=[old]).overrides_applied == []


def test_overlapping_windows_are_rejected(cfg, gym):
    # Monday 6 AM -> Tuesday 7 AM overlaps Tuesday's 6-8 AM window.
    overnight = build_weekly([("Daily", "6:00 AM - 8:00 AM", "open")])
    overnight = WeeklyHours(tuple(((Window(360, 1860),) if i == 0 else w) for i, w in enumerate(overnight.days)))
    with pytest.raises(ValidationError, match="overlapping"):
        run(cfg, gym, overnight, today=date(2026, 11, 2), days=2)


def test_uids_are_stable_across_days_and_unique(cfg, gym):
    a, b = run(cfg, gym), run(cfg, gym, today=TODAY + timedelta(days=5))
    assert len({e.uid for e in a.events}) == len(a.events)
    ua = {e.start: e.uid for e in a.events}
    shared = [e for e in b.events if e.start in ua]
    assert shared and all(ua[e.start] == e.uid for e in shared)
    other = expand(cfg.gyms[1], build_weekly(SEED), [], cfg.tz, TODAY, 90, WATCH)
    assert not {e.uid for e in a.events} & {e.uid for e in other.events}


def test_no_class_events_and_ics_round_trip(cfg, gym):
    exp = run(cfg, gym, holidays=[HolidayDay(date(2026, 12, 25), "Christmas", True, (), "Closed")])
    text = build_calendar(gym.calendar_name, "d", to_ics_events(gym, exp, "0" * 64),
                          datetime(2026, 10, 1, tzinfo=timezone.utc), "America/Los_Angeles")
    validate_ics(text, exp, cfg.tz)
    for ev in Calendar.from_ical(text).walk("VEVENT"):
        summary = str(ev["SUMMARY"])
        assert summary.startswith("PF Milwaukie: ") and "class" not in summary.lower()
        assert ("Open" in summary) or ("CLOSED" in summary)
        start, end = ev.decoded("DTSTART"), ev.decoded("DTEND")
        assert end > start
