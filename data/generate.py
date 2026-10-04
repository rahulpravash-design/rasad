"""Synthetic daily consumption per post per supply class.

    consumed = troops x rate x altitude_factor(alt) x temp_factor(temp) x surge x lognormal noise

Everything except the rations rate is an assumption (config/consumption.yaml). Output is *demand*;
data/stock.py turns it into reports and can clip it when a post runs out.

Determinism: every (post, winter) pair draws from its own seeded stream keyed on the post id (not on
its position in a list), so adding a post never reshuffles the other posts' random numbers.
"""

from __future__ import annotations

import zlib
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from config.loader import winter_bounds, winters


def altitude_factor(altitude_m: float, slope: float, start_m: float) -> float:
    """1.0 below `start_m`, rising by `slope` per km above it."""
    return 1.0 + slope * max(0.0, altitude_m - start_m) / 1000.0


def temp_factor(temp_c: np.ndarray, slope: float, t_ref_c: float) -> np.ndarray:
    """1.0 at or above `t_ref_c`, rising by `slope` per 10 C below it."""
    return 1.0 + slope * np.maximum(0.0, t_ref_c - temp_c) / 10.0


def surge_multipliers(
    rng: np.random.Generator, n_days: int, classes: list[str], surge_cfg: dict[str, Any]
) -> np.ndarray:
    """(n_classes, n_days) array; 1.0 outside events, the largest event multiplier inside."""
    mult = np.ones((len(classes), n_days))
    n_events = rng.poisson(float(surge_cfg["rate_per_day"]) * n_days)
    types = sorted(surge_cfg["types"])
    weights = np.array([float(surge_cfg["types"][t]["weight"]) for t in types])
    lo_d, hi_d = surge_cfg["duration_days"]
    for _ in range(n_events):
        kind = surge_cfg["types"][types[rng.choice(len(types), p=weights / weights.sum())]]
        start = int(rng.integers(0, n_days))
        stop = min(n_days, start + int(rng.integers(lo_d, hi_d + 1)))
        for ci, cls in enumerate(classes):
            lo, hi = kind["multipliers"][cls]
            mult[ci, start:stop] = np.maximum(mult[ci, start:stop], rng.uniform(lo, hi))
    return mult


def generate_consumption(
    sector: dict[str, Any],
    ccfg: dict[str, Any],
    weather: pd.DataFrame,
    seed: int = 42,
) -> pd.DataFrame:
    """Return one row per post x class x day:
    post_id, supply_class, date, demand, temp_c, surge_mult.

    A formation only has data from its `history_start`; F3 therefore starts a winter-2024 and has a
    single winter before the held-out one.
    """
    classes = list(ccfg["classes"])
    posts = sorted(sector["posts"], key=lambda p: p["id"])
    timeline = sector["timeline"]
    sigma = float(ccfg["noise"]["lognormal_sigma"])
    alt_start = float(ccfg["altitude_start_m"])
    t_ref = float(ccfg["temp_ref_c"])

    temps = weather.set_index(["location_id", "date"])["t_mean_c"]
    frames: list[pd.DataFrame] = []

    for post in posts:
        history_start = date.fromisoformat(sector["formations"][post["formation"]]["history_start"])
        for winter in winters(timeline):
            start, end = winter_bounds(timeline, winter)
            if start < history_start:
                continue
            dates = pd.date_range(start, end, freq="D")
            n = len(dates)
            temp_c = temps.loc[post["id"]].reindex(dates).to_numpy()
            if np.isnan(temp_c).any():
                raise ValueError(f"{post['id']}: weather missing for winter {winter}")

            rng = np.random.default_rng([seed, 1, zlib.crc32(post["id"].encode()), winter])
            surge = surge_multipliers(rng, n, classes, ccfg["surge"])
            for ci, cls in enumerate(classes):
                c = ccfg["classes"][cls]
                expected = (
                    post["troops"]
                    * float(c["rate"])
                    * altitude_factor(post["altitude_m"], float(c["altitude_slope"]), alt_start)
                    * temp_factor(temp_c, float(c["temp_slope"]), t_ref)
                    * surge[ci]
                )
                noise = rng.lognormal(-(sigma**2) / 2.0, sigma, n)  # mean 1.0
                frames.append(
                    pd.DataFrame(
                        {
                            "post_id": post["id"],
                            "supply_class": cls,
                            "date": dates,
                            "demand": np.rint(expected * noise).astype("int64"),
                            "temp_c": temp_c,
                            "surge_mult": surge[ci].round(3),
                        }
                    )
                )
    return pd.concat(frames, ignore_index=True)
