"""Extract Club Hours and holiday hours from a rendered planetfitness.com gym page.

Primary source: the page's embedded Remix "turbo-stream" data (clubHours / holidayHours / clubSlug).
Cross-check:    the visible "Club Hours" block in the HTML. If both exist they must agree.
Only club opening hours are read; class schedules are never touched.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from .hours import HoursError, WeeklyHours, build_weekly


class ExtractError(Exception):
    pass


@dataclass
class ClubData:
    slug: str | None
    name: str | None
    weekly: WeeklyHours
    holidays_raw: list[dict] | None  # None = holiday data not available on the page
    weekly_source: str  # "structured+dom" | "structured" | "dom"


def is_challenge_page(html: str) -> bool:
    return "<title>Just a moment...</title>" in html or (
        "challenges.cloudflare.com" in html and "clubHours" not in html and "Club Hours" not in html
    )


# ---------------------------------------------------------------- turbo-stream

_ENQUEUE_RX = re.compile(r'streamController\.enqueue\(("(?:[^"\\]|\\.)*")\)')
_SPECIAL = {-1: None, -2: None, -3: float("nan"), -4: float("inf"), -5: float("-inf"), -6: 0, -7: None}


def decode_turbo_stream(html: str) -> list | None:
    m = _ENQUEUE_RX.search(html)
    if not m:
        return None
    try:
        data = json.loads(json.loads(m.group(1)))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def _hydrate(data: list, idx, depth: int = 0):
    if depth > 40:
        raise ExtractError("turbo-stream nesting too deep")
    if isinstance(idx, int) and idx < 0:
        return _SPECIAL.get(idx)
    if not isinstance(idx, int) or idx >= len(data):
        raise ExtractError(f"bad turbo-stream reference {idx!r}")
    v = data[idx]
    if isinstance(v, dict):
        out = {}
        for k, ref in v.items():
            if not k.startswith("_"):
                raise ExtractError(f"unexpected turbo-stream key {k!r}")
            out[data[int(k[1:])]] = _hydrate(data, ref, depth + 1)
        return out
    if isinstance(v, list):
        if v and isinstance(v[0], str):  # typed value (promise, date, ...) - not needed here
            return {"$type": v[0]}
        return [_hydrate(data, x, depth + 1) for x in v]
    return v


def structured_club(html: str) -> dict | None:
    data = decode_turbo_stream(html)
    if data is None or "clubHours" not in data:
        return None
    key = f"_{data.index('clubHours')}"
    owners = [i for i, v in enumerate(data) if isinstance(v, dict) and key in v]
    if len(owners) != 1:
        raise ExtractError(f"expected one club object in page data, found {len(owners)}")
    return _hydrate(data, owners[0])


def weekly_from_structured(club_hours: dict) -> WeeklyHours:
    if not isinstance(club_hours, dict) or club_hours.get("status") is False:
        raise ExtractError(f"club hours unavailable in page data: {club_hours!r}")
    rows = list(club_hours.get("workingDays") or []) + list(club_hours.get("weekends") or [])
    if not rows:
        raise ExtractError("club hours list is empty")
    try:
        return build_weekly([(r["dayOfWeek"], r["time"], r.get("status")) for r in rows])
    except (KeyError, TypeError) as e:
        raise ExtractError(f"unexpected club hours shape: {rows!r}") from e


# ---------------------------------------------------------------- visible DOM

_DOM_LINE_RX = re.compile(
    r"^((?:mon|tue|wed|thu|fri|sat|sun)[a-z]*(?:\s*(?:&|-|–|and|to)\s*(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*)*)\s*:\s*(.+)$",
    re.I,
)


def weekly_from_dom(html: str) -> WeeklyHours | None:
    text = BeautifulSoup(html, "html.parser").get_text("\n")
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    try:
        i = lines.index("Club Hours")
    except ValueError:
        return None
    entries = []
    for line in lines[i + 1:]:
        m = _DOM_LINE_RX.match(line)
        if not m:
            break
        entries.append((m[1], m[2], None))
    if not entries:
        raise ExtractError("'Club Hours' heading found but no day lines under it")
    return build_weekly(entries)


# ---------------------------------------------------------------- entry point

def extract(html: str, expected_slug: str) -> ClubData:
    if is_challenge_page(html):
        raise ExtractError("got the Cloudflare browser-check page instead of the gym page")
    try:
        club = structured_club(html)
        dom = weekly_from_dom(html)
        weekly = weekly_from_structured(club["clubHours"]) if club else None
    except HoursError as e:
        raise ExtractError(f"could not parse club hours: {e}") from e

    if club is not None:
        slug = club.get("clubSlug")
        if slug != expected_slug:
            raise ExtractError(f"page is for club {slug!r}, expected {expected_slug!r}")
    elif expected_slug not in html:
        raise ExtractError(f"page does not reference {expected_slug!r}")

    if weekly and dom and weekly != dom:
        raise ExtractError(f"page data and visible Club Hours disagree: {weekly.as_text()} vs {dom.as_text()}")
    if weekly is None and dom is None:
        raise ExtractError("no Club Hours found on page")

    holidays = club.get("holidayHours") if club else None
    if holidays is not None and not isinstance(holidays, list):
        raise ExtractError(f"unexpected holidayHours shape: {holidays!r}")
    return ClubData(
        slug=club.get("clubSlug") if club else expected_slug,
        name=club.get("clubName") if club else None,
        weekly=weekly or dom,
        holidays_raw=holidays,
        weekly_source="structured+dom" if weekly and dom else "structured" if weekly else "dom",
    )
