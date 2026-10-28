# planet-fitness-hours-calendar

Google-Calendar-subscribable `.ics` feeds of the **club opening hours** of two Planet Fitness gyms. Each gym
gets its own calendar:

* **Milwaukie (Hwy 224 & SE Oak St)**, <https://www.planetfitness.com/gyms/milwaukie-hwy-224-se-oak-st-or>
* **Portland SE 82nd (Clackamas)**, <https://www.planetfitness.com/gyms/portland-se-82nd-clackamas-or>

A weekly GitHub Action (Mondays) re-reads both gym pages and rebuilds a rolling 90-day horizon.
**Hours only.** Classes and class schedules are never scraped or published.

> Unofficial. Check with the club before you go, especially around holidays.

## Subscribe

Once GitHub Pages is enabled (see [Setup](#setup)), the site is at
`https://<owner>.github.io/planet-fitness-hours-calendar/`. The steps below assume the `klove2016` account;
swap in your own owner if it's different.

| Feed | URL |
|---|---|
| Milwaukie hours | `https://klove2016.github.io/planet-fitness-hours-calendar/planet_fitness_milwaukie_hours.ics` |
| SE 82nd hours | `https://klove2016.github.io/planet-fitness-hours-calendar/planet_fitness_se_82nd_hours.ics` |
| Both gyms (optional) | `https://klove2016.github.io/planet-fitness-hours-calendar/planet_fitness_both_hours.ics` |

The site has **Add to Google**, `webcal://`, and copy-URL buttons for each feed. It also shows each gym's current
weekly hours and a data-health panel built from `status.json`. To subscribe in Google Calendar by hand:
*Settings → Add calendar → From URL*, then paste the `https://…ics` URL.

## What the events look like

One event per opening window per day, in `America/Los_Angeles`. Events are marked *free* (`TRANSP:TRANSPARENT`),
so they never block your availability.

| Day | Event (hours verified 2026-10-28) |
|---|---|
| Mon–Thu | `PF Milwaukie: Open 24 hours`, 00:00 → 00:00 next day (a full 24 h, never zero-length) |
| Fri | `PF Milwaukie: Open 12:00 AM – 10:00 PM` |
| Sat–Sun | `PF Milwaukie: Open 6:00 AM – 10:00 PM` |
| Published holiday hours | `PF Milwaukie: Open 6:00 AM – 7:00 PM (Thanksgiving hours)`, replacing that day's regular hours |
| Published holiday closure | All-day `PF Milwaukie: CLOSED (Christmas)` |
| Major holiday, nothing published yet | `PF Milwaukie: Open 24 hours (Thanksgiving: holiday hours unconfirmed)`, plus an UNCONFIRMED note in the description |

Midnight-to-midnight days are separate daily events that meet but never overlap (Mon 00:00–Tue 00:00,
Tue 00:00–Wed 00:00, …, Fri 00:00–22:00). The hours above are a seed for the tests only. The feeds always use
whatever planetfitness.com shows on the day of the run.

## How it works

```
weekly cron (Mondays) / manual dispatch
  └─ for each gym, independently:
      ├─ load the gym page in headless Chromium (Cloudflare blocks plain HTTP), retries + backoff
      ├─ extract Club Hours from the page's embedded Remix data (clubHours, holidayHours, clubSlug)
      │    and cross-check against the visible "Club Hours" block; any disagreement is an error
      ├─ verify the page is for the expected club (clubSlug)
      ├─ resolve holiday dates ("April 4th" has no year; see below), apply overrides over weekly hours
      ├─ expand 90 days → stage ICS → validate (durations, 24 h windows, DST, overlaps, round-trip, counts)
      └─ OK → replace that gym's feed  |  failure → keep that gym's last-good feed, mark it stale
  └─ rebuild the combined feed from the two per-gym feeds as published
  └─ write status.json; exit non-zero if any gym failed (workflow fails, alert issue opened)
```

| Module | Role |
|---|---|
| `src/pf_hours/fetch.py` | Headless Chromium (Playwright), polls for club data through the Cloudflare check, retries with backoff, min interval between pages |
| `src/pf_hours/extract.py` | Turbo-stream decoder for the page's embedded data, DOM "Club Hours" parser, cross-check, club-slug check, Cloudflare-page detection |
| `src/pf_hours/hours.py` | Clock/range parsing (12:00 AM–12:00 AM = 24 h), day labels (`SatSun`, `Mon-Thu`, …), weekly model, holiday parsing and year resolution |
| `src/pf_hours/occurrences.py` | Dated events, overrides, unconfirmed-holiday labelling, stable UIDs |
| `src/pf_hours/ics.py` | Deterministic RFC 5545 writer (TZID + VTIMEZONE, all-day closures, folding, CRLF) and feed merge |
| `src/pf_hours/validate.py` | Pre-publication checks |
| `src/pf_hours/cli.py` | Per-gym pipeline and `status.json` |

### Holidays

Planet Fitness lists holiday hours without years (e.g. *Easter: April 4th closes 10 PM, April 5th closed, April
6th reopens 6 AM*). For each listing:

* **Moving holidays** (Easter, Memorial Day, Labor Day, Thanksgiving, MLK, Presidents' Day) fix the year. The
  listed dates must sit within a week of the computed holiday, and the closest year wins. Memorial Day "May 25th"
  is 2026, because Memorial Day 2025 was May 26.
* **Fixed-date holidays** (July 4th, Christmas, …) take the year of the moving holidays in the same listing.
  With nothing to anchor them, they take the next occurrence and are flagged `year inferred`.
* Overrides **replace** that day's weekly hours: special hours, or an all-day CLOSED event.
* No holiday hours are ever assumed. If a watched holiday (New Year's, Easter, Memorial Day, July 4, Labor Day,
  Thanksgiving, Christmas Eve/Day, New Year's Eve) falls in the horizon with no published hours, the day shows
  regular hours **labelled unconfirmed**. It is also listed in `status.json → gyms.*.unconfirmed_holidays` and on
  the site.

As of 2026-10-28, the site lists hours only for holidays that have already passed (Easter, Memorial Day, July 4,
Labor Day 2026). So Thanksgiving, Christmas Eve, Christmas, New Year's Eve and New Year's Day currently show as
unconfirmed.

### Pre-publication validation (per gym; fail ⇒ that gym's last-good feed kept)

* Page isn't a Cloudflare challenge, belongs to the expected club, and has Club Hours
* Embedded data and the visible Club Hours block agree (when both exist)
* Every weekday listed exactly once; a `24hrs` status must match a 24-hour time range
* Each open day has ≥ 120 open minutes and ≤ 24 h in total; windows are well-formed
* Every timed event has positive duration. "Open 24 hours" events run exactly local midnight → next local
  midnight (23–25 h of real time on DST days). No events overlap. No wall-clock times are skipped by DST
* No event title contains "class"
* Generated ICS re-parsed with `icalendar`; UIDs, counts and every start/end instant round-trip
* Over the same dates, a gym's new feed must not have more than 50% fewer events than its published feed.
  Override with the `allow_large_change` workflow input or `--allow-large-change`.

### `docs/status.json`

Top level: `status`, `stale`, `last_checked`, `horizon`, `combined`, `limitations`. Per gym (`gyms.milwaukie`,
`gyms.se_82nd`): `status`, `stale`, `last_checked`, `last_success`, `last_published`, `fetched_at`, `extraction`
(source used, holiday data present, club slug/name), `hours` (current weekly hours), `fingerprint` +
`first_seen` (hours unchanged since), `hours_changed`, `events`, `holidays_listed`,
`holiday_overrides_in_horizon`, `unconfirmed_holidays`, `history` (previous → new hours on each change),
`warnings`, `errors`.

UIDs are `sha256(gym | kind | local start | local end)`. They stay the same across runs, so calendar apps
update events in place. Output is byte-deterministic, so feeds are committed only when something changes.

## Setup

1. Create an **empty** GitHub repo named `planet-fitness-hours-calendar`, then push:
   ```bash
   cd planet-fitness-hours-calendar
   git init -b main && git add . && git commit -m "Initial import"
   git remote add origin git@github.com:<owner>/planet-fitness-hours-calendar.git
   git push -u origin main
   ```
2. **Settings → Pages →** *Deploy from a branch*, branch `main`, folder **`/docs`**.
3. **Settings → Actions → General**: allow Actions. The workflow declares its own minimal
   permissions (`contents: write`, `issues: write`).
4. **Actions → Refresh Planet Fitness hours → Run workflow** once, and check that both gyms show `ok`/`warning`
   on the site.
5. Optional: set the repository variable `ALERT_ISSUES=false` to turn off the failure-alert issue.

No secrets or credentials are needed.

## Local commands

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium                 # add --with-deps on Linux

python -m pytest -q                                    # full test suite (offline, no browser)
PYTHONPATH=src python -m pf_hours --dry-run            # live: browser fetch + parse + validate, writes nothing
PYTHONPATH=src python -m pf_hours                      # live refresh → docs/
PYTHONPATH=src python -m pf_hours \
  --html milwaukie=tests/fixtures/milwaukie_2026-10-28.html \
  --html se_82nd=tests/fixtures/se_82nd_2026-10-28.html \
  --today 2026-10-28 --docs /tmp/pf-docs               # fully offline run from saved pages
```

### Smoke test

```bash
PYTHONPATH=src python -m pf_hours --dry-run; echo "exit=$?"   # expect exit=0, 90 events per gym, weekly hours printed
python -c "import icalendar; c=icalendar.Calendar.from_ical(open('docs/planet_fitness_both_hours.ics','rb').read()); print(len(c.walk('VEVENT')),'events')"
python -m http.server -d docs 8000   # open http://localhost:8000
```

## Tests

`tests/fixtures/*_2026-10-28.html` are rendered gym pages saved on 2026-10-28.
`cloudflare_challenge.html` is what a plain HTTP client receives.

| Requirement | Test |
|---|---|
| Each location parsed and updated independently | `test_extract.py::test_each_gym_parses_independently_and_matches_seed`, `test_pipeline.py::test_one_gym_failing_does_not_block_the_other` |
| 24 h windows never zero duration | `test_hours.py::test_midnight_to_midnight_is_24_hours_not_zero`, `test_occurrences.py::test_24h_days_are_full_midnight_to_midnight`, `test_24h_day_on_dst_transitions` |
| No class events | `test_occurrences.py::test_no_class_events_and_ics_round_trip` (plus a validation guard) |
| Holiday overrides | `test_holiday_closure_supersedes_weekly`, `test_holiday_special_hours_supersede_weekly`, `test_pipeline.py::test_published_holiday_hours_override_weekly`, `test_unpublished_holidays_are_labelled_unconfirmed`, `test_hours.py::test_holiday_years_come_from_moving_holidays` |
| Source-failure fail-safe | `test_cloudflare_block_keeps_last_good`, `test_unparseable_hours_keep_last_good`, `test_implausible_hours_keep_last_good`, `test_large_event_drop_requires_override`, `test_extract.py::test_visible_and_structured_hours_must_agree` |
| Stable UIDs, DST | `test_uids_are_stable_across_days_and_unique`, `test_dst_fall_back_keeps_local_hours`, `test_24h_day_on_dst_transitions` |

## Limitations

* Checked **once a week** (Mondays), because club hours rarely change and fewer visits mean fewer Cloudflare
  challenges. A change can take up to a week to appear (run the workflow by hand for an immediate refresh), and calendar apps add
  their own refresh delay.
* planetfitness.com uses a **Cloudflare browser check**. Headless Chromium passes it today, from a residential
  IP. Cloudflare may treat GitHub's datacenter IPs more strictly. If it does, the run fails with
  `stuck on Cloudflare browser check`, the feeds go **stale but stay correct**, and an alert issue opens. The fix
  then is a self-hosted runner, or running `python -m pf_hours` locally and committing `docs/`.
* Holiday hours appear only once Planet Fitness posts them, often just a week or two ahead.
* GitHub disables scheduled workflows after 60 days without repo activity. The weekly `status.json` commit
  normally prevents that. If it does happen, re-enable the workflow in the Actions tab.
