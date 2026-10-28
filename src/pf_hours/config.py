"""Load and type the TOML configuration."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Gym:
    key: str
    name: str
    short: str
    url: str
    slug: str
    location: str
    file: str
    calendar_name: str


@dataclass(frozen=True)
class Config:
    tz: ZoneInfo
    horizon_days: int
    stale_after_hours: int
    browser: dict
    validation: dict
    gyms: tuple[Gym, ...]
    combined_file: str
    combined_name: str


def load_config(path: str | Path) -> Config:
    raw = tomllib.loads(Path(path).read_text())
    gyms = tuple(Gym(**g) for g in raw["gyms"])
    if len({g.key for g in gyms}) != len(gyms) or len({g.file for g in gyms}) != len(gyms):
        raise ValueError("gym keys and files must be unique")
    return Config(
        tz=ZoneInfo(raw["timezone"]),
        horizon_days=int(raw["horizon_days"]),
        stale_after_hours=int(raw["stale_after_hours"]),
        browser=raw["browser"],
        validation=raw["validation"],
        gyms=gyms,
        combined_file=raw["combined"]["file"],
        combined_name=raw["combined"]["calendar_name"],
    )
