"""Live forecasts for the API and the planner: one post, one origin, NumPy inference only."""

from __future__ import annotations

import math
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from config.loader import load_consumption, load_sector
from forecast import model as qm
from forecast.features import forecast_rows, load_panel

PRIMARY = "federated"
_cache: dict[tuple[str, float], dict[str, np.ndarray]] = {}


def models_dir(db_path: Path) -> Path:
    return db_path.with_name(db_path.stem + ".models")


def weights_for(db_path: Path, name: str = PRIMARY) -> dict[str, np.ndarray] | None:
    path = models_dir(db_path) / f"{name}.npz"
    if not path.exists():
        return None
    key = (str(path), path.stat().st_mtime)
    if key not in _cache:
        _cache[key] = qm.load(path)
    return _cache[key]


def post_forecasts(
    conn: sqlite3.Connection, db_path: Path, post: str, origin: str, name: str = PRIMARY
) -> dict[str, list[dict[str, Any]]] | None:
    """class -> 30 rows of {date, p10, p50, p90, actual} in the class's own units."""
    w = weights_for(db_path, name)
    if w is None:
        return None
    sector, ccfg = load_sector(), load_consumption()
    panel = load_panel(conn, sector, ccfg, posts=[post])
    out = {}
    for cls in panel.classes:
        if not any(k[0] == post and k[1] == cls for k in panel.y):
            continue
        x, days, actual = forecast_rows(panel, post, cls, date.fromisoformat(origin))
        scale = panel.posts[post].troops * panel.rates[cls]
        pred = np.maximum(qm.predict(w, x), 0.0) * scale
        out[cls] = [
            {
                "date": d.isoformat(),
                "p10": round(float(p[0]), 1),
                "p50": round(float(p[1]), 1),
                "p90": round(float(p[2]), 1),
                "actual": None if math.isnan(a) else round(float(a * scale), 1),
            }
            for d, p, a in zip(days, pred, actual, strict=True)
        ]
    return out
