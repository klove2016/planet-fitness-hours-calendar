"""Refresh pipeline. Each gym is fetched, parsed, validated and published independently; a failure for
one gym keeps that gym's last-good feed and never blocks the other.

Usage:
  PYTHONPATH=src python -m pf_hours                    # live run (headless Chromium), writes docs/
  PYTHONPATH=src python -m pf_hours --dry-run          # live run, writes nothing
  PYTHONPATH=src python -m pf_hours --html milwaukie=tests/fixtures/milwaukie_2026-10-28.html \\
      --html se_82nd=tests/fixtures/se_82nd_2026-10-28.html --today 2026-10-28 --docs /tmp/pf
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from icalendar import Calendar

from .config import Config, Gym, load_config
from .extract import ExtractError, extract
from .fetch import BrowserFetcher, FetchError
from .hours import HoursError, build_holidays
from .ics import IcsEvent, build_calendar, merge_calendars
from .occurrences import expand
from .store import load_json, write_json_atomic, write_text_atomic
from .validate import ValidationError, validate_count_change, validate_expansion, validate_ics, validate_weekly

GYM_ERRORS = (FetchError, ExtractError, HoursError, ValidationError)
ROOT = Path(__file__).resolve().parents[2]
HISTORY_LIMIT = 20
CAL_DESC = "Planet Fitness club opening hours (not classes), generated weekly from planetfitness.com."


class LocalFetcher:
    """Serves saved gym pages (offline runs and tests). Missing gyms fail like a network error."""

    def __init__(self, pages: dict[str, Path], cfg: Config):
        self.by_url = {g.url: pages[g.key] for g in cfg.gyms if g.key in pages}

    def get(self, url: str) -> tuple[str, datetime]:
        if url not in self.by_url:
            raise FetchError(f"offline mode: no saved page for {url}")
        return self.by_url[url].read_text(), datetime.now(timezone.utc)


def fingerprint(weekly_text: dict, holidays_raw) -> str:
    blob = json.dumps({"weekly": weekly_text, "holidays": holidays_raw}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def to_ics_events(gym: Gym, exp, fp: str) -> list[IcsEvent]:
    return [
        IcsEvent(
            uid=e.uid, start=e.start, end=e.end, summary=e.summary,
            description="\n".join(filter(None, [
                f"Club opening hours (not classes) for {gym.name}.",
                e.note,
                "Hours as published on planetfitness.com – confirm with the club before visiting.",
                f"Source: {gym.url} (hours data {fp[:12]})",
            ])),
            location=gym.location, url=gym.url,
        )
        for e in exp.events
    ]


def refresh_gym(gym: Gym, cfg: Config, docs: Path, fetcher, now: datetime, today: date, prev: dict,
                allow_large_change: bool, dry_run: bool) -> dict:
    st = {
        "name": gym.name,
        "url": gym.url,
        "file": gym.file,
        "status": "error",
        "stale": True,
        "last_checked": now.isoformat(),
        "last_success": prev.get("last_success"),
        "last_published": prev.get("last_published"),
        "fingerprint": prev.get("fingerprint"),
        "first_seen": prev.get("first_seen"),
        "hours": prev.get("hours"),
        "events": prev.get("events"),
        "holidays_listed": prev.get("holidays_listed"),
        "holiday_overrides_in_horizon": prev.get("holiday_overrides_in_horizon", []),
        "unconfirmed_holidays": prev.get("unconfirmed_holidays", []),
        "history": prev.get("history", []),
        "warnings": [],
        "errors": [],
    }
    try:
        html, fetched_at = fetcher.get(gym.url)
        club = extract(html, gym.slug)
        validate_weekly(club.weekly, int(cfg.validation["min_open_minutes"]))
        warnings: list[str] = []
        if club.holidays_raw is None:
            holidays = []
            warnings.append("Holiday hours were not available on the page; holidays are unconfirmed.")
        else:
            holidays, notes = build_holidays(club.holidays_raw, today)
            warnings += notes
        exp = expand(gym, club.weekly, holidays, cfg.tz, today, cfg.horizon_days,
                     list(cfg.validation["watch_holidays"]))
        validate_expansion(exp, cfg.tz)

        weekly_text = club.weekly.as_text()
        fp = fingerprint(weekly_text, club.holidays_raw)
        changed = fp != prev.get("fingerprint")
        first_seen = now.isoformat() if changed or not prev.get("first_seen") else prev["first_seen"]
        text = build_calendar(gym.calendar_name, CAL_DESC, to_ics_events(gym, exp, fp),
                              datetime.fromisoformat(first_seen), cfg.tz.key)
        validate_ics(text, exp, cfg.tz)
        path = docs / gym.file
        old = path.read_bytes().decode("utf-8") if path.exists() else None
        max_drop = 1.0 if allow_large_change else float(cfg.validation["max_event_drop_ratio"])
        count_note = validate_count_change(old, exp, cfg.tz, max_drop)

        file_changed = not path.exists() or path.read_bytes() != text.encode("utf-8")
        if file_changed and not dry_run:
            write_text_atomic(path, text)
            st["last_published"] = now.isoformat()

        if changed:
            st["history"].insert(0, {"seen": now.isoformat(), "fingerprint": fp, "hours": weekly_text,
                                     "previous_hours": prev.get("hours")})
            del st["history"][HISTORY_LIMIT:]
        if exp.unconfirmed_holidays:
            warnings.append("Holiday hours not yet published for: " + ", ".join(
                f"{h['holiday']} ({h['date']})" for h in exp.unconfirmed_holidays) + " – regular hours shown, labelled unconfirmed.")
        if any(h.year_inferred for h in holidays):
            warnings.append("Some holiday dates had no year on the site; year was inferred.")
        st.update({
            "status": "warning" if warnings else "ok",
            "stale": False,
            "last_success": now.isoformat(),
            "fetched_at": fetched_at.replace(microsecond=0).isoformat(),
            "extraction": {"weekly_source": club.weekly_source, "holidays_available": club.holidays_raw is not None,
                           "club_slug": club.slug, "club_name": club.name},
            "fingerprint": fp,
            "first_seen": first_seen,
            "hours_changed": changed and prev.get("fingerprint") is not None,
            "hours": weekly_text,
            "events": len(exp.events),
            "file_changed": file_changed,
            "event_count_check": count_note,
            "holidays_listed": [{"date": h.day.isoformat(), "holiday": h.name,
                                 "hours": "Closed" if h.closed else ", ".join(w.label() for w in h.windows)}
                                for h in holidays],
            "holiday_overrides_in_horizon": exp.overrides_applied,
            "unconfirmed_holidays": exp.unconfirmed_holidays,
            "warnings": warnings,
        })
    except GYM_ERRORS as e:
        st["errors"] = [f"{type(e).__name__}: {e}"]
        st["warnings"] = ["Feed NOT updated; the last validated feed for this gym remains published."]
    return st


def refresh(cfg: Config, docs: Path, fetcher, now: datetime, today: date,
            allow_large_change: bool = False, dry_run: bool = False) -> tuple[int, dict]:
    prev = load_json(docs / "status.json")
    prev_gyms = prev.get("gyms", {})
    gyms = {g.key: refresh_gym(g, cfg, docs, fetcher, now, today, prev_gyms.get(g.key, {}),
                               allow_large_change, dry_run)
            for g in cfg.gyms}

    # The combined feed is always the union of the per-gym feeds as currently published.
    combined_changed = False
    combined_error = None
    published = [(docs / g.file).read_bytes().decode("utf-8") for g in cfg.gyms if (docs / g.file).exists()]
    if published and not dry_run:
        combined = merge_calendars(cfg.combined_name, CAL_DESC, published, cfg.tz.key)
        expected = sum(len(Calendar.from_ical(t).walk("VEVENT")) for t in published)
        got = len(Calendar.from_ical(combined).walk("VEVENT"))
        path = docs / cfg.combined_file
        if got != expected:
            combined_error = f"combined feed has {got} events, expected {expected}; not updated"
        elif not path.exists() or path.read_bytes() != combined.encode("utf-8"):
            write_text_atomic(path, combined)
            combined_changed = True

    failed = [k for k, s in gyms.items() if s["status"] == "error"]
    status = {
        "project": "planet-fitness-hours-calendar",
        "status": "error" if failed or combined_error else ("warning" if any(s["warnings"] for s in gyms.values()) else "ok"),
        "stale": any(s["stale"] for s in gyms.values()),
        "stale_after_hours": cfg.stale_after_hours,
        "last_checked": now.isoformat(),
        "horizon": {"start": today.isoformat(), "days": cfg.horizon_days},
        "combined": {"file": cfg.combined_file, "name": cfg.combined_name, "changed": combined_changed,
                     "error": combined_error},
        "gyms": gyms,
        "limitations": [
            "Checked once a week (Mondays): hours or holiday changes can take up to a week to appear.",
            "Holiday hours are applied only when Planet Fitness publishes them; otherwise regular hours are "
            "shown and labelled unconfirmed on major holidays.",
            "planetfitness.com uses a Cloudflare browser check; if it blocks the automated browser, feeds go stale "
            "(never wrong) and the workflow fails.",
        ],
    }
    if not dry_run:
        write_json_atomic(docs / "status.json", status)
    return (1 if failed or combined_error else 0), status


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pf_hours", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=ROOT / "config" / "config.toml", type=Path)
    ap.add_argument("--docs", default=ROOT / "docs", type=Path)
    ap.add_argument("--html", action="append", default=[], metavar="GYM=FILE",
                    help="use a saved gym page instead of the browser (repeat per gym)")
    ap.add_argument("--today", type=date.fromisoformat, help="override today's date (local)")
    ap.add_argument("--allow-large-change", action="store_true", help="accept a large event-count drop")
    ap.add_argument("--dry-run", action="store_true", help="run every check but write nothing")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    today = args.today or now.astimezone(cfg.tz).date()
    if args.html:
        pages = dict(item.split("=", 1) for item in args.html)
        ctx = contextlib.nullcontext(LocalFetcher({k: Path(v) for k, v in pages.items()}, cfg))
    else:
        ctx = BrowserFetcher(cfg.browser)
    with ctx as fetcher:
        code, status = refresh(cfg, args.docs, fetcher, now, today, args.allow_large_change, args.dry_run)

    print(f"status={status['status']} stale={status['stale']}")
    for key, g in status["gyms"].items():
        print(f"[{key}] {g['status']}: {g.get('events')} events -> {g['file']}")
        for day, hours in (g.get("hours") or {}).items():
            print(f"    {day:<9} {hours}")
        for w in g["warnings"]:
            print(f"    WARNING: {w}")
        for e in g["errors"]:
            print(f"    ERROR: {e}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
