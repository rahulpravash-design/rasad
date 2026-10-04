"""Rows for the demand models.

Target: consumption per soldier on day t divided by the class's base rate from consumption.yaml,
so every class sits near 1 and one model can serve all five.

Features for target day t never look at anything later than t - 30, so a 30-day forecast needs no
recursion: every lag and window is clipped at the start of the season and ends at t - 30 at the
latest, which is on or before any forecast origin t - h with h <= 30.

    lag30, lag37           target at t-30 and t-37
    mean_30_44, mean_30_58 mean target over [t-44, t-30] and [t-58, t-30]
    doy_sin, doy_cos       day of year of t
    season_frac            how far into the 1 Oct - 31 Mar season t is
    cold                   max(0, 15 - temperature at t) / 20
    alt                    max(0, altitude - 2500 m) / 2000
    troops                 log(troops) / 5
    one-hot class          five columns

Temperature at t is the observed value when building training rows. When forecasting, horizons up
to 16 days use the observed value as a stand-in for a 16-day weather forecast (optimistic: a real
forecast has error) and longer horizons use the post's climatology.

All scaling is fixed and data-independent, so federated clients never have to share statistics.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from config.loader import winter_bounds

FORECAST_DAYS = 30
FORECAST_TEMP_DAYS = 16
MIN_TARGET_INDEX = 30  # training rows need t - 30 inside the season
BASE_FEATURES = (
    "lag30",
    "lag37",
    "mean_30_44",
    "mean_30_58",
    "doy_sin",
    "doy_cos",
    "season_frac",
    "cold",
    "alt",
    "troops",
)


@dataclass(frozen=True)
class PostMeta:
    formation: str
    troops: int
    altitude_m: int


@dataclass
class Panel:
    """Daily target series per (post, class, winter), temperatures and climatology."""

    classes: list[str]
    rates: dict[str, float]
    posts: dict[str, PostMeta]
    timeline: dict[str, Any]
    y: dict[tuple[str, str, int], np.ndarray]  # NaN where no trusted report exists
    temps: dict[tuple[str, int], np.ndarray]  # post, winter -> t_mean per season day
    clim: dict[str, np.ndarray]  # post -> mean temperature per season day (training winters)

    @property
    def feature_names(self) -> list[str]:
        return [*BASE_FEATURES, *(f"class_{c}" for c in self.classes)]

    def season(self, winter: int) -> tuple[date, date]:
        return winter_bounds(self.timeline, winter)


def winter_of(d: date) -> int:
    return d.year if d.month >= 10 else d.year - 1


def load_panel(
    conn: sqlite3.Connection,
    sector: dict[str, Any],
    ccfg: dict[str, Any],
    posts: list[str] | None = None,
    climatology_before: int | None = None,
) -> Panel:
    """Read trusted reports and post weather. `posts` limits the read (the API needs one post);
    `climatology_before` is the first winter excluded from climatology (default: the holdout)."""
    timeline = sector["timeline"]
    classes = list(ccfg["classes"])
    rates = {c: float(ccfg["classes"][c]["rate"]) for c in classes}
    meta = {
        p["id"]: PostMeta(p["formation"], int(p["troops"]), int(p["altitude_m"]))
        for p in sector["posts"]
        if posts is None or p["id"] in posts
    }
    ids = list(meta)
    marks = ",".join("?" * len(ids))
    reports = pd.read_sql(
        f"SELECT post_id, supply_class, report_date, consumed FROM trusted_reports "
        f"WHERE post_id IN ({marks})",
        conn,
        params=ids,
    )
    weather = pd.read_sql(
        f"SELECT location_id, date, t_mean_c FROM weather_daily WHERE location_id IN ({marks})",
        conn,
        params=ids,
    )

    y: dict[tuple[str, str, int], np.ndarray] = {}
    temps: dict[tuple[str, int], np.ndarray] = {}
    winters = range(int(timeline["first_winter"]), int(timeline["last_winter"]) + 1)
    index = {
        w: pd.date_range(*winter_bounds(timeline, w), freq="D").strftime("%Y-%m-%d")
        for w in winters
    }

    for (post, cls), grp in reports.groupby(["post_id", "supply_class"]):
        series = grp.set_index("report_date")["consumed"]
        series = series[~series.index.duplicated(keep="first")]
        scale = meta[post].troops * rates[cls]
        for w, days in index.items():
            values = series.reindex(days).to_numpy(dtype=float) / scale
            if not np.isnan(values).all():
                y[(post, cls, w)] = values

    for post, grp in weather.groupby("location_id"):
        series = grp.set_index("date")["t_mean_c"]
        for w, days in index.items():
            temps[(post, w)] = series.reindex(days).to_numpy(dtype=float)

    cut = climatology_before if climatology_before is not None else int(timeline["holdout_winter"])
    clim = {}
    for post in ids:
        stack = [temps[(post, w)] for w in winters if w < cut and (post, w) in temps]
        n = max(len(t) for t in stack)
        padded = np.vstack([np.pad(t, (0, n - len(t)), constant_values=np.nan) for t in stack])
        mean = np.nanmean(padded, axis=0)
        clim[post] = pd.Series(mean).rolling(15, center=True, min_periods=1).mean().to_numpy()
    return Panel(classes, rates, meta, timeline, y, temps, clim)


def _window_mean(cum: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """Mean of y[lo..hi] inclusive from cumulative sums (cum[k] = sum of y[:k])."""
    return (cum[hi + 1] - cum[lo]) / (hi - lo + 1)


def features_for(
    panel: Panel, post: str, cls: str, winter: int, idx: np.ndarray, temp_c: np.ndarray
) -> np.ndarray:
    """Feature matrix for target day indices `idx` of one (post, class, winter) series."""
    y = panel.y[(post, cls, winter)]
    filled = pd.Series(y).ffill().bfill().to_numpy()  # gaps (rare) carry the last value forward
    cum = np.concatenate([[0.0], np.cumsum(filled)])
    end = np.clip(idx - 30, 0, None)
    start, end_date = panel.season(winter)
    n_days = (end_date - start).days + 1
    days = [start + timedelta(days=int(i)) for i in idx]
    doy = np.array([d.timetuple().tm_yday for d in days], dtype=float)
    meta = panel.posts[post]
    cols = [
        filled[end],
        filled[np.clip(idx - 37, 0, None)],
        _window_mean(cum, np.clip(idx - 44, 0, None), end),
        _window_mean(cum, np.clip(idx - 58, 0, None), end),
        np.sin(2 * np.pi * doy / 365.25),
        np.cos(2 * np.pi * doy / 365.25),
        idx / n_days,
        np.maximum(0.0, 15.0 - temp_c) / 20.0,
        np.full(len(idx), max(0.0, meta.altitude_m - 2500) / 2000.0),
        np.full(len(idx), np.log(meta.troops) / 5.0),
    ]
    onehot = np.zeros((len(idx), len(panel.classes)))
    onehot[:, panel.classes.index(cls)] = 1.0
    return np.column_stack([*cols, onehot])


def training_rows(
    panel: Panel, winters: set[int], formations: set[str] | None = None
) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    """(X, y, meta) for every target day with t - 30 inside the season, observed temperatures.
    `meta` has post_id, supply_class, formation, winter, date and scale (troops x base rate) so
    targets can be turned back into quantities."""
    xs, ys, metas = [], [], []
    for (post, cls, w), y in sorted(panel.y.items()):
        meta = panel.posts[post]
        if w not in winters or (formations is not None and meta.formation not in formations):
            continue
        idx = np.arange(MIN_TARGET_INDEX, len(y))
        idx = idx[~np.isnan(y[idx])]
        if len(idx) == 0:
            continue
        xs.append(features_for(panel, post, cls, w, idx, panel.temps[(post, w)][idx]))
        ys.append(y[idx])
        start = panel.season(w)[0]
        metas.append(
            pd.DataFrame(
                {
                    "post_id": post,
                    "supply_class": cls,
                    "formation": meta.formation,
                    "winter": w,
                    "date": [start + timedelta(days=int(i)) for i in idx],
                    "scale": meta.troops * panel.rates[cls],
                }
            )
        )
    if not xs:
        return np.empty((0, len(panel.feature_names))), np.empty(0), pd.DataFrame()
    return np.vstack(xs), np.concatenate(ys), pd.concat(metas, ignore_index=True)


def evaluation_rows(
    panel: Panel, winter: int, temperature: str
) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    """Held-out rows with temperature either 'observed' (stands for horizons up to 16 days) or
    'climatology' (horizons 17-30)."""
    x, y, meta = training_rows(panel, {winter})
    if temperature == "climatology" and len(meta):
        temp_col = BASE_FEATURES.index("cold")
        starts = {w: panel.season(w)[0] for w in meta["winter"].unique()}
        clim_t = np.array(
            [
                panel.clim[p][(d - starts[w]).days]
                for p, d, w in zip(meta["post_id"], meta["date"], meta["winter"], strict=True)
            ]
        )
        x = x.copy()
        x[:, temp_col] = np.maximum(0.0, 15.0 - clim_t) / 20.0
    return x, y, meta


def forecast_rows(
    panel: Panel, post: str, cls: str, origin: date, days: int = FORECAST_DAYS
) -> tuple[np.ndarray, list[date], np.ndarray]:
    """Features for origin+1 .. origin+days (within the origin's season), plus the actual target
    where the data has it (the held-out winter is replayed, so it usually does)."""
    winter = winter_of(origin)
    start, end = panel.season(winter)
    targets = [origin + timedelta(days=h) for h in range(1, days + 1)]
    targets = [t for t in targets if t <= end]
    idx = np.array([(t - start).days for t in targets])
    observed = panel.temps[(post, winter)][idx]
    climate = panel.clim[post][idx]
    horizon = np.arange(1, len(idx) + 1)
    temp = np.where((horizon <= FORECAST_TEMP_DAYS) & ~np.isnan(observed), observed, climate)
    x = features_for(panel, post, cls, winter, idx, temp)
    actual = panel.y[(post, cls, winter)][idx]
    return x, targets, actual
