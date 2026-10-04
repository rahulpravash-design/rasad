"""Pass closure risk: P(the pass is closed within k days), k = 3, 7, 14.

One logistic regression per horizon, pooled over the four passes, trained on the rule labels of the
pre-holdout winters (closure/labels.py), so it predicts the stated rule, not observed closures.

Features on day d: snowfall over the last 3, 7 and 14 days, 14-day mean temperature, days since 1
Nov, pass altitude. Status: CLOSED only when the rule says the pass is closed (p_close 1); otherwise
AT_RISK from P14 >= 0.4, else OPEN. (The plan's scale also mapped P14 >= 0.8 to "Closed"; a pass
that is still open is shown as AT_RISK instead, so CLOSED always means closed.)
`days` is the shortest horizon whose probability reaches 0.5 (3, 7 or 14), or null if none does.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

HORIZONS = (3, 7, 14)
OPEN_BELOW = 0.4


def _features(weather: pd.DataFrame, altitude: dict[str, float]) -> pd.DataFrame:
    rows = []
    for pid, g in weather.groupby("location_id", sort=True):
        g = g.sort_values("date").reset_index(drop=True)
        winter = g["date"].dt.year.where(g["date"].dt.month >= 10, g["date"].dt.year - 1)
        snow = g.groupby(winter)["snowfall_cm"]
        f = pd.DataFrame(
            {
                "pass_id": pid,
                "date": g["date"],
                "winter": winter,
                "snow3": snow.transform(lambda s: s.rolling(3, min_periods=1).sum()),
                "snow7": snow.transform(lambda s: s.rolling(7, min_periods=1).sum()),
                "snow14": snow.transform(lambda s: s.rolling(14, min_periods=1).sum()),
                "temp14": g.groupby(winter)["t_mean_c"].transform(
                    lambda s: s.rolling(14, min_periods=1).mean()
                ),
            }
        )
        nov1 = pd.to_datetime(winter.astype(str) + "-11-01")
        f["season_day"] = (g["date"] - nov1).dt.days / 100.0
        f["alt"] = (altitude[pid] - 3500) / 1000.0
        rows.append(f)
    return pd.concat(rows, ignore_index=True)


COLS = ["snow3", "snow7", "snow14", "temp14", "season_day", "alt"]


def fit_and_score(
    pass_weather: pd.DataFrame,
    labels: pd.DataFrame,
    passes: list[dict[str, Any]],
    holdout_winter: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return per pass-day: p3, p7, p14, status, days; plus a small training report."""
    alt = {p["id"]: float(p["altitude_m"]) for p in passes}
    f = _features(pass_weather, alt).merge(
        labels[["pass_id", "date", "closed", "days_closed"]], on=["pass_id", "date"]
    )
    f = f.sort_values(["pass_id", "date"]).reset_index(drop=True)
    for k in HORIZONS:
        # 1 if the pass is closed on any of the next k days (same winter).
        fut = f.groupby(["pass_id", "winter"])["closed"].transform(
            lambda s, k=k: s[::-1].rolling(k, min_periods=1).max()[::-1].shift(-1).fillna(0)
        )
        f[f"y{k}"] = fut.astype(int)

    train = f[(f["winter"] < holdout_winter) & ~f["closed"]]
    report: dict[str, Any] = {"train_rows": int(len(train))}
    x_all = f[COLS].to_numpy()
    for k in HORIZONS:
        model = LogisticRegression(max_iter=2000, C=1.0).fit(train[COLS].to_numpy(), train[f"y{k}"])
        f[f"p{k}"] = np.where(f["closed"], 1.0, model.predict_proba(x_all)[:, 1])
        report[f"positives_{k}"] = int(train[f"y{k}"].sum())

    f["status"] = np.where(
        f["closed"], "CLOSED", np.where(f["p14"] >= OPEN_BELOW, "AT_RISK", "OPEN")
    )
    f["days"] = [
        int(r.days_closed)
        if r.closed
        else next((k for k in HORIZONS if getattr(r, f"p{k}") >= 0.5), None)
        for r in f.itertuples()
    ]
    return f, report
