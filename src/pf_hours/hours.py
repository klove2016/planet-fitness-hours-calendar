"""Opening-hours model and parsing: clock strings, day labels, weekly hours, holiday overrides.

Minutes are counted from local midnight of the day the window opens. A window may end at 1440
(midnight at the end of the day) or later (overnight). "12:00 AM - 12:00 AM" is 24 hours open,
never zero.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

DAY_MINUTES = 1440
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class HoursError(Exception):
    pass


@dataclass(frozen=True, order=True)
class Window:
    start: int  # minutes after local midnight, 0..1439
    end: int  # > start; 1440 = midnight ending the day; > 1440 = overnight

    @property
    def is_24h(self) -> bool:
        return self.start == 0 and self.end == DAY_MINUTES

    def label(self) -> str:
        if self.is_24h:
            return "24 hours"
        return f"{clock_label(self.start)} – {clock_label(self.end)}"


@dataclass(frozen=True)
class WeeklyHours:
    days: tuple[tuple[Window, ...], ...]  # index 0 = Monday; () = closed

    def as_text(self) -> dict[str, str]:
        return {WEEKDAY_NAMES[i]: ", ".join(w.label() for w in ws) or "Closed" for i, ws in enumerate(self.days)}


@dataclass(frozen=True)
class HolidayDay:
    day: date
    name: str
    closed: bool
    windows: tuple[Window, ...]
    action: str
    year_inferred: bool = False


# ---------------------------------------------------------------- clock & windows

_CLOCK_RX = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?\s*$", re.I)


def parse_clock(s: str) -> int:
    t = s.strip().lower()
    if t == "noon":
        return 720
    if t == "midnight":
        return 0
    m = _CLOCK_RX.match(t)
    if not m:
        raise HoursError(f"unrecognised clock time {s!r}")
    h, mi = int(m[1]), int(m[2] or 0)
    if not (1 <= h <= 12 and 0 <= mi < 60):
        raise HoursError(f"invalid clock time {s!r}")
    return (h % 12 + (12 if m[3].lower() == "p" else 0)) * 60 + mi


def clock_label(minutes: int) -> str:
    m = minutes % DAY_MINUTES
    h, mi = divmod(m, 60)
    return f"{(h % 12) or 12}:{mi:02d} {'AM' if h < 12 else 'PM'}"


def make_window(opens: str, closes: str) -> Window:
    start, end = parse_clock(opens), parse_clock(closes)
    if end <= start:
        end += DAY_MINUTES  # closes at/after midnight; equal times = open the full 24 hours
    return Window(start, end)


def parse_range(text: str) -> Window:
    parts = re.split(r"\s*[-–—]\s*|\s+to\s+", text.strip(), maxsplit=1)
    if len(parts) != 2:
        raise HoursError(f"unrecognised hours range {text!r}")
    return make_window(parts[0], parts[1])


# ---------------------------------------------------------------- day labels

_DAY_TOKENS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def parse_days(label: str) -> list[int]:
    """'Monday' | 'SatSun' | 'Sat & Sun' | 'Mon-Thu' | 'Weekdays' | 'Weekends' | 'Daily' -> weekday ints."""
    s = label.strip().lower()
    if s in {"daily", "every day", "everyday", "all week"}:
        return list(range(7))
    if s in {"weekdays", "mon-fri"}:
        return list(range(5))
    if s in {"weekends", "weekend"}:
        return [5, 6]
    tokens = re.findall(r"mon|tue|wed|thu|fri|sat|sun|[-–]|to", s)
    if not tokens:
        raise HoursError(f"unrecognised day label {label!r}")
    days: list[int] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _DAY_TOKENS:
            if i + 2 < len(tokens) and tokens[i + 1] in {"-", "–", "to"} and tokens[i + 2] in _DAY_TOKENS:
                a, b = _DAY_TOKENS[tok], _DAY_TOKENS[tokens[i + 2]]
                d = a
                while True:
                    days.append(d)
                    if d == b:
                        break
                    d = (d + 1) % 7
                i += 3
                continue
            days.append(_DAY_TOKENS[tok])
        i += 1
    if not days:
        raise HoursError(f"unrecognised day label {label!r}")
    return days


def build_weekly(entries: list[tuple[str, str, str | None]]) -> WeeklyHours:
    """entries: (day_label, 'h:mm AM - h:mm PM', status or None). Every weekday exactly once."""
    days: dict[int, tuple[Window, ...]] = {}
    for label, text, status in entries:
        st = (status or "").strip().lower()
        if st == "closed" or text.strip().lower() == "closed":
            wins: tuple[Window, ...] = ()
        else:
            w = parse_range(text)
            if st in {"24hrs", "24hr", "24 hours", "24/7"} and w.end - w.start != DAY_MINUTES:
                raise HoursError(f"{label}: status {status!r} contradicts time {text!r}")
            if st and st not in {"open", "24hrs", "24hr", "24 hours", "24/7"}:
                raise HoursError(f"{label}: unknown status {status!r}")
            wins = (w,)
        for d in parse_days(label):
            if d in days:
                raise HoursError(f"{WEEKDAY_NAMES[d]} listed more than once")
            days[d] = wins
    missing = [WEEKDAY_NAMES[d] for d in range(7) if d not in days]
    if missing:
        raise HoursError(f"no hours for {', '.join(missing)}")
    return WeeklyHours(tuple(days[d] for d in range(7)))


# ---------------------------------------------------------------- holidays

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], start=1)}


def parse_month_day(text: str) -> tuple[int, int, int | None]:
    m = re.match(r"^\s*([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?\s*$", text)
    if not m:
        raise HoursError(f"unrecognised holiday date {text!r}")
    name = m[1].lower()
    month = next((v for k, v in _MONTHS.items() if k.startswith(name[:3])), None)
    if month is None:
        raise HoursError(f"unrecognised month in {text!r}")
    return month, int(m[2]), int(m[3]) if m[3] else None


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    if n > 0:
        d = date(year, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def easter(year: int) -> date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def holiday_date(name: str, year: int) -> tuple[date | None, bool]:
    """(date, is_moving). None for names we don't recognise."""
    n = name.lower().replace("’", "'")
    if "new year" in n and "eve" in n:
        return date(year, 12, 31), False
    if "new year" in n:
        return date(year, 1, 1), False
    if "christmas eve" in n:
        return date(year, 12, 24), False
    if "christmas" in n:
        return date(year, 12, 25), False
    if "black friday" in n:
        return _nth_weekday(year, 11, 3, 4) + timedelta(days=1), True
    if "thanksgiving" in n:
        return _nth_weekday(year, 11, 3, 4), True
    if "easter" in n:
        return easter(year), True
    if "memorial" in n:
        return _nth_weekday(year, 5, 0, -1), True
    if "labor" in n or "labour" in n:
        return _nth_weekday(year, 9, 0, 1), True
    if "independence" in n or "4th of july" in n or "fourth of july" in n:
        return date(year, 7, 4), False
    if "juneteenth" in n:
        return date(year, 6, 19), False
    if "martin luther" in n or "mlk" in n:
        return _nth_weekday(year, 1, 0, 3), True
    if "president" in n:
        return _nth_weekday(year, 2, 0, 3), True
    if "veteran" in n:
        return date(year, 11, 11), False
    if "halloween" in n:
        return date(year, 10, 31), False
    return None, False


def resolve_moving(name: str, month_days: list[tuple[int, int, int | None]], today: date) -> list[date] | None:
    """Moving holidays (Easter, Thanksgiving, ...) pin the year: listed dates must sit within a week of the
    computed holiday. Returns None for fixed-date/unknown names, [] when no nearby year fits."""
    if holiday_date(name, today.year)[0] is None or not holiday_date(name, today.year)[1]:
        return None
    fits = []
    for year in (today.year - 1, today.year, today.year + 1):
        h, _ = holiday_date(name, year)
        out = [min((date(y, m, d) for y in (year - 1, year, year + 1)), key=lambda c: abs((c - h).days))
               for m, d, _y in month_days]
        worst = max(abs((c - h).days) for c in out)
        if worst <= 7:
            fits.append((worst, out))
    # Closest fit wins: Memorial Day "May 25th" is 2026 (exact), not 2025 (one day off).
    return min(fits, key=lambda f: f[0])[1] if fits else []


def resolve_fixed(month_days: list[tuple[int, int, int | None]], anchors: list[date], today: date
                  ) -> tuple[list[date], bool]:
    """Fixed-date or unknown holidays: take the year nearest the list's moving holidays (same listing
    cycle). With no anchors, take the next occurrence on/after yesterday and flag the year as inferred."""
    out = []
    for m, d, _y in month_days:
        cands = [date(y, m, d) for y in (today.year - 1, today.year, today.year + 1)]
        if anchors:
            out.append(min(cands, key=lambda c: min(abs((c - a).days) for a in anchors)))
        else:
            out.append(next(c for c in cands if c >= today - timedelta(days=1)))
    return out, not anchors


def build_holidays(raw: list[dict], today: date) -> tuple[list[HolidayDay], list[str]]:
    """Turn the site's holidayHours list into dated overrides. Unknown shapes raise HoursError."""
    notes: list[str] = []
    parsed = []
    for h in raw or []:
        name = (h.get("holidayName") or "").strip() or "Holiday"
        entries = h.get("dates") or []
        parsed.append((name, entries, [parse_month_day(e.get("date", "")) for e in entries]))

    resolved: dict[int, tuple[list[date], bool]] = {}
    anchors: list[date] = []
    for i, (name, entries, mds) in enumerate(parsed):
        if all(md[2] is not None for md in mds):
            resolved[i] = ([date(y, m, d) for m, d, y in mds], False)
            continue
        moving = resolve_moving(name, mds, today)
        if moving is not None:
            resolved[i] = (moving, False)
            anchors.extend(moving)
    for i, (name, entries, mds) in enumerate(parsed):
        if i not in resolved:
            resolved[i] = resolve_fixed(mds, anchors, today)

    days: list[HolidayDay] = []
    for i, (name, entries, mds) in enumerate(parsed):
        dates, inferred = resolved[i]
        if not dates:
            notes.append(f"Holiday '{name}' dates {[e.get('date') for e in entries]} do not match any nearby "
                         f"year; ignored.")
            continue
        for e, d in zip(entries, dates):
            status = (e.get("status") or "").strip().lower()
            action = (e.get("action") or "").strip()
            if status == "closed":
                days.append(HolidayDay(d, name, True, (), action, inferred))
            elif status == "open":
                if not e.get("opens") or not e.get("closes"):
                    raise HoursError(f"holiday {name} {e.get('date')}: open without times")
                days.append(HolidayDay(d, name, False, (make_window(e["opens"], e["closes"]),), action, inferred))
            else:
                raise HoursError(f"holiday {name} {e.get('date')}: unknown status {e.get('status')!r}")
    by_day: dict[date, HolidayDay] = {}
    for hd in days:
        if hd.day in by_day and by_day[hd.day] != hd:
            raise HoursError(f"conflicting holiday hours for {hd.day}")
        by_day[hd.day] = hd
    return sorted(by_day.values(), key=lambda x: x.day), notes
