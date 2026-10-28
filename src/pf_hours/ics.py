"""Deterministic RFC 5545 writer (byte-identical output for identical input)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

PRODID = "-//planet-fitness-hours-calendar//EN"

# US Pacific rules in force since 2007; Google/Apple/Outlook resolve the TZID themselves,
# this block is here for RFC compliance and for clients that need it.
VTIMEZONE_LA = """BEGIN:VTIMEZONE
TZID:America/Los_Angeles
X-LIC-LOCATION:America/Los_Angeles
BEGIN:DAYLIGHT
TZOFFSETFROM:-0800
TZOFFSETTO:-0700
TZNAME:PDT
DTSTART:19700308T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0700
TZOFFSETTO:-0800
TZNAME:PST
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
END:VTIMEZONE"""


@dataclass(frozen=True)
class IcsEvent:
    uid: str
    start: datetime | date  # tz-aware datetime, or date for all-day
    end: datetime | date
    summary: str
    description: str
    location: str
    url: str
    transparent: bool = True


def escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def fold(line: str) -> str:
    """Fold at 75 octets without splitting UTF-8 sequences."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        limit = 75 if not out else 74  # continuation lines start with a space
        if size + n > limit:
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    out.append(cur)
    return "\r\n ".join(out)


def _dt(prop: str, value: datetime | date, tzid: str) -> str:
    if isinstance(value, datetime):
        return f"{prop};TZID={tzid}:{value:%Y%m%dT%H%M%S}"
    return f"{prop};VALUE=DATE:{value:%Y%m%d}"


def sort_key(e: IcsEvent):
    d = e.start.date() if isinstance(e.start, datetime) else e.start
    t = e.start.strftime("%H%M") if isinstance(e.start, datetime) else ""
    return (d, t, e.summary, e.uid)


def vevent_lines(ev: IcsEvent, stamp: str, tzid: str) -> list[str]:
    return [
        "BEGIN:VEVENT",
        f"UID:{ev.uid}",
        f"DTSTAMP:{stamp}",
        _dt("DTSTART", ev.start, tzid),
        _dt("DTEND", ev.end, tzid),
        f"SUMMARY:{escape(ev.summary)}",
        f"DESCRIPTION:{escape(ev.description)}",
        f"LOCATION:{escape(ev.location)}",
        f"URL:{ev.url}",
        f"TRANSP:{'TRANSPARENT' if ev.transparent else 'OPAQUE'}",
        "STATUS:CONFIRMED",
        "END:VEVENT",
    ]


def _header(name: str, description: str, tzid: str) -> list[str]:
    return [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape(name)}",
        f"X-WR-CALDESC:{escape(description)}",
        f"X-WR-TIMEZONE:{tzid}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
        *VTIMEZONE_LA.splitlines(),
    ]


def build_calendar(name: str, description: str, events: list[IcsEvent], dtstamp: datetime, tzid: str) -> str:
    stamp = dtstamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = _header(name, description, tzid)
    for ev in sorted(events, key=sort_key):
        lines += vevent_lines(ev, stamp, tzid)
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


_VEVENT_RX = re.compile(r"BEGIN:VEVENT\r\n.*?END:VEVENT\r\n", re.S)
_DTSTART_RX = re.compile(r"^DTSTART[^:]*:(\S+)", re.M)


def merge_calendars(name: str, description: str, calendars: list[str], tzid: str) -> str:
    """Combine already-published feeds verbatim (their VEVENT blocks), ordered by start."""
    blocks = [b for text in calendars for b in _VEVENT_RX.findall(text)]
    blocks.sort(key=lambda b: (_DTSTART_RX.search(b).group(1)[:8], _DTSTART_RX.search(b).group(1), b))
    head = "\r\n".join(fold(l) for l in _header(name, description, tzid)) + "\r\n"
    return head + "".join(blocks) + "END:VCALENDAR\r\n"
