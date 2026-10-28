"""Expand weekly hours + holiday overrides into dated opening-hours events for one gym."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import Gym
from .hours import DAY_MINUTES, HolidayDay, WeeklyHours, Window, holiday_date

UID_DOMAIN = "planet-fitness-hours-calendar"


@dataclass(frozen=True)
class HoursEvent:
    gym: str
    kind: str  # "open" | "closed"
    start: datetime | date  # date only for all-day "closed" days
    end: datetime | date
    summary: str
    note: str
    uid: str

    @property
    def day(self) -> date:
        return self.start.date() if isinstance(self.start, datetime) else self.start


@dataclass
class Expansion:
    window_start: date
    window_end: date
    events: list[HoursEvent]
    overrides_applied: list[dict] = field(default_factory=list)
    unconfirmed_holidays: list[dict] = field(default_factory=list)


def local_dt(d: date, minutes: int, tz: ZoneInfo) -> datetime:
    days, m = divmod(minutes, DAY_MINUTES)
    return datetime.combine(d + timedelta(days=days), time(m // 60, m % 60), tzinfo=tz)


def make_uid(gym: str, kind: str, start, end) -> str:
    key = f"{gym}|{kind}|{start.isoformat()}|{end.isoformat()}"
    return hashlib.sha256(key.encode()).hexdigest()[:32] + "@" + UID_DOMAIN


def watched_holidays_between(names: list[str], first: date, last: date) -> dict[date, str]:
    out = {}
    for name in names:
        for year in range(first.year, last.year + 1):
            d, _ = holiday_date(name, year)
            if d and first <= d <= last:
                out[d] = name
    return out


def _open_summary(gym: Gym, w: Window) -> str:
    return f"{gym.short}: Open 24 hours" if w.is_24h else f"{gym.short}: Open {w.label()}"


def expand(gym: Gym, weekly: WeeklyHours, holidays: list[HolidayDay], tz: ZoneInfo, today: date,
           horizon_days: int, watch_holidays: list[str]) -> Expansion:
    last = today + timedelta(days=horizon_days - 1)
    overrides = {h.day: h for h in holidays if today <= h.day <= last}
    watched = watched_holidays_between(watch_holidays, today, last)
    exp = Expansion(today, last, [])
    d = today
    while d <= last:
        hd = overrides.get(d)
        if hd and hd.closed:
            summary = f"{gym.short}: CLOSED ({hd.name})"
            exp.events.append(HoursEvent(gym.key, "closed", d, d + timedelta(days=1), summary,
                                         f"Holiday closure published by Planet Fitness: {hd.name}.",
                                         make_uid(gym.key, "closed", d, d + timedelta(days=1))))
            exp.overrides_applied.append({"date": d.isoformat(), "holiday": hd.name, "hours": "Closed"})
        elif hd:
            for w in hd.windows:
                start, end = local_dt(d, w.start, tz), local_dt(d, w.end, tz)
                exp.events.append(HoursEvent(
                    gym.key, "open", start, end, f"{_open_summary(gym, w)} ({hd.name} hours)",
                    f"Holiday hours published by Planet Fitness: {hd.name} – {hd.action or 'special hours'}."
                    + (" (Year not stated on the site; inferred.)" if hd.year_inferred else ""),
                    make_uid(gym.key, "open", start, end)))
            exp.overrides_applied.append({"date": d.isoformat(), "holiday": hd.name,
                                          "hours": ", ".join(w.label() for w in hd.windows)})
        else:
            note = ""
            if d in watched:
                note = (f"{watched[d]}: Planet Fitness has not published holiday hours for this date. "
                        "Regular hours shown – UNCONFIRMED, check with the club.")
                exp.unconfirmed_holidays.append({"date": d.isoformat(), "holiday": watched[d]})
            for w in weekly.days[d.weekday()]:
                start, end = local_dt(d, w.start, tz), local_dt(d, w.end, tz)
                summary = _open_summary(gym, w)
                if note:
                    summary += f" ({watched[d]}: holiday hours unconfirmed)"
                exp.events.append(HoursEvent(gym.key, "open", start, end, summary, note,
                                             make_uid(gym.key, "open", start, end)))
        d += timedelta(days=1)
    exp.events.sort(key=lambda e: (e.day, e.kind, str(e.start)))
    return exp
