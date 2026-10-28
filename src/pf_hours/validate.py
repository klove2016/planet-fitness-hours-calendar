"""Pre-publication checks. Any failure for a gym means that gym's last-good feed stays in place."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from icalendar import Calendar

from .hours import DAY_MINUTES, WeeklyHours
from .occurrences import Expansion


class ValidationError(Exception):
    pass


def validate_weekly(weekly: WeeklyHours, min_open_minutes: int) -> None:
    for i, wins in enumerate(weekly.days):
        total = sum(w.end - w.start for w in wins)
        for w in wins:
            if not 0 <= w.start < DAY_MINUTES or not w.start < w.end <= w.start + DAY_MINUTES:
                raise ValidationError(f"weekday {i}: impossible window {w}")
        if wins and total < min_open_minutes:
            raise ValidationError(f"weekday {i}: only {total} open minutes – implausible")
        if total > DAY_MINUTES:
            raise ValidationError(f"weekday {i}: windows add up to more than 24 hours")
    if not any(weekly.days):
        raise ValidationError("gym appears closed every day of the week")


def _exists(dt: datetime, tz: ZoneInfo) -> bool:
    """False for wall-clock times skipped by a DST jump."""
    return dt.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) == dt.replace(tzinfo=None)


def validate_expansion(exp: Expansion, tz: ZoneInfo) -> None:
    if not exp.events:
        raise ValidationError("no hours events generated")
    timed = [e for e in exp.events if isinstance(e.start, datetime)]
    for e in exp.events:
        if "class" in e.summary.lower():
            raise ValidationError(f"class-like event in hours feed: {e.summary}")
        if isinstance(e.start, datetime):
            if not (_exists(e.start, tz) and _exists(e.end, tz)):
                raise ValidationError(f"{e.summary} on {e.day} uses a wall-clock time skipped by DST")
            dur = e.end - e.start
            if dur <= timedelta(0):
                raise ValidationError(f"{e.summary} on {e.day} has zero/negative duration")
            if "24 hours" in e.summary:
                if e.start.time().hour or e.start.time().minute or e.end.date() != e.day + timedelta(days=1) \
                        or e.end.time().hour or e.end.time().minute:
                    raise ValidationError(f"24-hour event on {e.day} is not local midnight to midnight")
                if not timedelta(hours=23) <= dur <= timedelta(hours=25):
                    raise ValidationError(f"24-hour event on {e.day} lasts {dur}")
        elif e.kind != "closed" or e.end != e.start + timedelta(days=1):
            raise ValidationError(f"unexpected all-day event {e.summary}")
    timed.sort(key=lambda e: e.start)
    for a, b in zip(timed, timed[1:]):
        if b.start < a.end:
            raise ValidationError(f"overlapping hours events: {a.summary} and {b.summary} on {b.day}")
    uids = [e.uid for e in exp.events]
    if len(uids) != len(set(uids)):
        raise ValidationError("duplicate UIDs")


def validate_ics(text: str, exp: Expansion, tz: ZoneInfo) -> None:
    try:
        cal = Calendar.from_ical(text)
    except Exception as e:
        raise ValidationError(f"generated ICS does not parse: {e}") from e
    events = cal.walk("VEVENT")
    if len(events) != len(exp.events):
        raise ValidationError(f"ICS has {len(events)} events, expected {len(exp.events)}")
    want = {e.uid: e for e in exp.events}
    for ev in events:
        o = want.get(str(ev["UID"]))
        if o is None:
            raise ValidationError(f"unexpected UID {ev['UID']}")
        start, end = ev.decoded("DTSTART"), ev.decoded("DTEND")
        if isinstance(o.start, datetime):
            if start != o.start or end != o.end or start.astimezone(tz).time() != o.start.time():
                raise ValidationError(f"{o.summary}: time mismatch after ICS round-trip")
            if end <= start:
                raise ValidationError(f"{o.summary}: zero duration in ICS")
        elif (start, end) != (o.start, o.end):
            raise ValidationError(f"{o.summary}: all-day mismatch after ICS round-trip")


def count_events_between(ics_text: str, first: date, last: date, tz: ZoneInfo) -> int:
    n = 0
    for ev in Calendar.from_ical(ics_text).walk("VEVENT"):
        s = ev.decoded("DTSTART")
        d = s.astimezone(tz).date() if isinstance(s, datetime) else s
        n += first <= d <= last
    return n


def validate_count_change(old_ics: str | None, exp: Expansion, tz: ZoneInfo, max_drop: float) -> str | None:
    if not old_ics:
        return None
    try:
        old = count_events_between(old_ics, exp.window_start, exp.window_end, tz)
    except Exception:
        return "previous feed unreadable; skipped event-count comparison"
    new = len(exp.events)
    if old >= 10 and new < old * (1 - max_drop):
        raise ValidationError(f"event count would drop from {old} to {new} (> {max_drop:.0%}); re-run with "
                              "--allow-large-change if Planet Fitness really changed the hours")
    return f"{old} -> {new} events over {exp.window_start}..{exp.window_end}"
