# planet-fitness-hours-calendar

Subscribable calendars of club opening hours (not classes) for two Planet Fitness gyms, rebuilt weekly from
planetfitness.com. Unofficial; confirm with the club, especially around holidays.

**Subscribe:** <https://klove2016.github.io/planet-fitness-hours-calendar/>

| Calendar | Feed |
|---|---|
| Milwaukie (Hwy 224 & SE Oak St) | [planet_fitness_milwaukie_hours.ics](https://klove2016.github.io/planet-fitness-hours-calendar/planet_fitness_milwaukie_hours.ics) |
| Portland SE 82nd (Clackamas) | [planet_fitness_se_82nd_hours.ics](https://klove2016.github.io/planet-fitness-hours-calendar/planet_fitness_se_82nd_hours.ics) |
| Both gyms | [planet_fitness_both_hours.ics](https://klove2016.github.io/planet-fitness-hours-calendar/planet_fitness_both_hours.ics) |

In Google Calendar: *Settings → Add calendar → From URL*, then paste a feed URL.

## How it works

A GitHub Action runs every Monday. It loads each gym page in headless Chromium (the site uses a Cloudflare
check), reads the Club Hours, and publishes one event per opening window for the next 90 days to `docs/`.

- 24-hour days are full midnight-to-midnight events.
- Holiday hours posted by Planet Fitness replace regular hours. Major holidays with nothing posted show
  regular hours labelled "unconfirmed".
- Each gym is handled separately: if one fails (e.g. Cloudflare blocks it), its last good feed stays up,
  `status.json` is marked stale, the job fails and opens an issue.
- Run it by hand from the Actions tab for an immediate refresh.

## Local

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
python -m playwright install chromium
python -m pytest -q                          # offline tests
PYTHONPATH=src python -m pf_hours --dry-run  # live check, writes nothing
PYTHONPATH=src python -m pf_hours            # refresh docs/
```
