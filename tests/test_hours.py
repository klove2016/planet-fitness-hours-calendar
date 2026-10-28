from datetime import date

import pytest

from pf_hours.hours import (HoursError, Window, build_holidays, build_weekly, easter, make_window, parse_clock,
                            parse_days, parse_range)

TODAY = date(2026, 10, 28)


def test_midnight_to_midnight_is_24_hours_not_zero():
    w = parse_range("12:00 AM - 12:00 AM")
    assert w == Window(0, 1440)
    assert w.is_24h and w.end - w.start == 1440
    assert w.label() == "24 hours"


def test_close_at_midnight_and_overnight():
    assert make_window("6:00 AM", "12:00 AM") == Window(360, 1440)
    assert make_window("10:00 PM", "2:00 AM") == Window(1320, 1560)
    assert make_window("12:00 AM", "10:00 PM") == Window(0, 1320)


@pytest.mark.parametrize("s,m", [("12:00 AM", 0), ("12:00 PM", 720), ("6:00 AM", 360), ("10:00 PM", 1320),
                                 ("7 pm", 1140), ("noon", 720)])
def test_parse_clock(s, m):
    assert parse_clock(s) == m


@pytest.mark.parametrize("bad", ["25:00 AM", "6:00", "six", "13:00 PM"])
def test_parse_clock_rejects(bad):
    with pytest.raises(HoursError):
        parse_clock(bad)


@pytest.mark.parametrize("label,days", [("Monday", [0]), ("SatSun", [5, 6]), ("Sat & Sun", [5, 6]),
                                        ("Mon-Thu", [0, 1, 2, 3]), ("Fri", [4]), ("Weekdays", [0, 1, 2, 3, 4]),
                                        ("Daily", list(range(7))), ("Sat - Sun", [5, 6])])
def test_parse_days(label, days):
    assert parse_days(label) == days


SEED = [("Monday", "12:00 AM - 12:00 AM", "24hrs"), ("Tuesday", "12:00 AM - 12:00 AM", "24hrs"),
        ("Wednesday", "12:00 AM - 12:00 AM", "24hrs"), ("Thursday", "12:00 AM - 12:00 AM", "24hrs"),
        ("Friday", "12:00 AM - 10:00 PM", "open"), ("SatSun", "6:00 AM - 10:00 PM", "open")]


def test_build_weekly_seed_hours():
    wk = build_weekly(SEED)
    assert wk.days[:4] == ((Window(0, 1440),),) * 4
    assert wk.days[4] == (Window(0, 1320),)
    assert wk.days[5] == wk.days[6] == (Window(360, 1320),)


def test_build_weekly_requires_every_day_once():
    with pytest.raises(HoursError, match="no hours for Sunday"):
        build_weekly(SEED[:-1] + [("Saturday", "6:00 AM - 10:00 PM", "open")])
    with pytest.raises(HoursError, match="more than once"):
        build_weekly(SEED + [("Monday", "6:00 AM - 10:00 PM", "open")])


def test_status_24hrs_must_match_time():
    with pytest.raises(HoursError, match="contradicts"):
        build_weekly([("Monday", "6:00 AM - 10:00 PM", "24hrs")] + SEED[1:])


def test_closed_day():
    wk = build_weekly(SEED[:-1] + [("Saturday", "6:00 AM - 10:00 PM", "open"), ("Sunday", "", "closed")])
    assert wk.days[6] == ()


@pytest.mark.parametrize("year,d", [(2025, date(2025, 4, 20)), (2026, date(2026, 4, 5)), (2027, date(2027, 3, 28))])
def test_easter(year, d):
    assert easter(year) == d


SITE_HOLIDAYS = [
    {"holidayName": "Easter", "dates": [
        {"date": "April 4th", "action": "Closes at", "status": "open", "opens": "6:00 AM", "closes": "10:00 PM"},
        {"date": "April 5th", "action": "Closed", "status": "closed"},
        {"date": "April 6th", "action": "Reopens at", "status": "open", "opens": "6:00 AM", "closes": "12:00 AM"}]},
    {"holidayName": "Memorial Day", "dates": [{"date": "May 25th", "action": "Open regular hours for the holiday",
                                               "status": "open", "opens": "12:00 AM", "closes": "12:00 AM"}]},
    {"holidayName": "Independence Day", "dates": [{"date": "July 4th", "action": "Select hours", "status": "open",
                                                   "opens": "6:00 AM", "closes": "7:00 PM"}]},
    {"holidayName": "Labor Day", "dates": [{"date": "September 7th", "action": "Open regular hours for the holiday",
                                            "status": "open", "opens": "12:00 AM", "closes": "12:00 AM"}]},
]


def test_holiday_years_come_from_moving_holidays():
    days, notes = build_holidays(SITE_HOLIDAYS, TODAY)
    got = {h.day: h for h in days}
    # Memorial Day 2026 = May 25 exactly (2025's was May 26) -> 2026, and fixed July 4 anchored to the same cycle.
    assert set(got) == {date(2026, 4, 4), date(2026, 4, 5), date(2026, 4, 6), date(2026, 5, 25), date(2026, 7, 4),
                        date(2026, 9, 7)}
    assert got[date(2026, 4, 5)].closed
    assert got[date(2026, 4, 6)].windows == (Window(360, 1440),)
    assert got[date(2026, 7, 4)].windows == (Window(360, 1140),)
    assert not any(h.year_inferred for h in days) and notes == []


def test_upcoming_thanksgiving_and_christmas():
    raw = [{"holidayName": "Thanksgiving", "dates": [{"date": "November 26th", "status": "open", "action": "Select hours",
                                                       "opens": "6:00 AM", "closes": "2:00 PM"}]},
           {"holidayName": "Christmas", "dates": [{"date": "December 25th", "status": "closed", "action": "Closed"}]}]
    days, _ = build_holidays(raw, TODAY)
    assert [(h.day, h.closed) for h in days] == [(date(2026, 11, 26), False), (date(2026, 12, 25), True)]


def test_fixed_holiday_without_anchor_is_next_occurrence_and_flagged():
    raw = [{"holidayName": "Christmas", "dates": [{"date": "December 25th", "status": "closed"}]}]
    days, _ = build_holidays(raw, TODAY)
    assert days[0].day == date(2026, 12, 25) and days[0].year_inferred


def test_moving_holiday_with_wrong_date_is_ignored():
    raw = [{"holidayName": "Thanksgiving", "dates": [{"date": "October 2nd", "status": "closed"}]}]
    days, notes = build_holidays(raw, TODAY)
    assert days == [] and "ignored" in notes[0]


def test_unknown_holiday_status_fails():
    with pytest.raises(HoursError):
        build_holidays([{"holidayName": "Christmas", "dates": [{"date": "December 25th", "status": "maybe"}]}], TODAY)
