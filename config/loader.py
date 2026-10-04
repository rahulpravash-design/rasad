"""Loaders and validation for the YAML files in config/.

Nothing here is cached: constraints.yaml is live-editable, and the other files are small.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

ACCESS_MODES = {"truck", "mule", "heli"}
# Loose bounding box around Ladakh; catches swapped lat/lon and typos, not survey errors.
LADAKH_BBOX = {"lat": (32.0, 35.5), "lon": (75.0, 80.0)}


def _load(name: str) -> dict[str, Any]:
    with (CONFIG_DIR / name).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_sector() -> dict[str, Any]:
    sector = _load("sector.yaml")
    validate_sector(sector)
    return sector


def load_consumption() -> dict[str, Any]:
    return _load("consumption.yaml")


def load_constraints() -> dict[str, Any]:
    return _load("constraints.yaml")


def load_weather_cfg() -> dict[str, Any]:
    return _load("weather.yaml")


def supply_classes() -> list[str]:
    return list(load_consumption()["classes"])


def winter_bounds(timeline: dict[str, Any], winter: int) -> tuple[date, date]:
    """First and last day of the winter season that starts in year `winter`."""
    sm, sd = (int(x) for x in timeline["season_start"].split("-"))
    em, ed = (int(x) for x in timeline["season_end"].split("-"))
    return date(winter, sm, sd), date(winter + 1, em, ed)


def winters(timeline: dict[str, Any]) -> list[int]:
    return list(range(int(timeline["first_winter"]), int(timeline["last_winter"]) + 1))


def validate_sector(sector: dict[str, Any]) -> None:
    """Raise ValueError on any structural problem in sector.yaml."""
    errors: list[str] = []

    def unique_ids(kind: str, rows: list[dict[str, Any]]) -> set[str]:
        ids = [r["id"] for r in rows]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            errors.append(f"duplicate {kind} ids: {sorted(dupes)}")
        return set(ids)

    formations = set(sector["formations"])
    depots = unique_ids("depot", sector["depots"])
    passes = unique_ids("pass", sector["passes"])
    unique_ids("post", sector["posts"])

    for kind in ("depots", "passes", "posts"):
        for row in sector[kind]:
            lat, lon = row["lat"], row["lon"]
            if not (
                LADAKH_BBOX["lat"][0] <= lat <= LADAKH_BBOX["lat"][1]
                and LADAKH_BBOX["lon"][0] <= lon <= LADAKH_BBOX["lon"][1]
            ):
                errors.append(
                    f"{row['id']}: lat/lon ({lat}, {lon}) outside the Ladakh bounding box"
                )
            if not 1000 <= row["altitude_m"] <= 6500:
                errors.append(f"{row['id']}: implausible altitude {row['altitude_m']} m")

    for post in sector["posts"]:
        pid = post["id"]
        if post["formation"] not in formations:
            errors.append(f"{pid}: unknown formation {post['formation']}")
        if post["depot"] not in depots:
            errors.append(f"{pid}: unknown depot {post['depot']}")
        if post["via_pass"] not in passes:
            errors.append(f"{pid}: unknown via_pass {post['via_pass']}")
        access = set(post["access"])
        if not access or not access <= ACCESS_MODES:
            errors.append(f"{pid}: access must be a non-empty subset of {sorted(ACCESS_MODES)}")
        if post["troops"] <= 0:
            errors.append(f"{pid}: troops must be positive")

    if errors:
        raise ValueError("invalid sector.yaml:\n  " + "\n  ".join(errors))
