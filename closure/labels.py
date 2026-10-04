"""Rule-based pass closure labels.

The stated rule (config/constraints.yaml -> closure_rule):

    * From `season_start_mmdd` (1 Nov), a pass closes when snowfall over the last 3 days reaches
      `snow_3d_close_cm` (15 cm).
    * It reopens once the 14-day mean temperature exceeds `reopen_mean_temp_14d_c` (0 C).

These labels are the training target for the Day 7 logistic-regression model, and they drive the
interim pass status shown on the dashboard until that model exists. They are rule-based, not
observed closures; docs/real-vs-mocked.md says so.

Note the consequence of the stated reopening rule: at pass altitude the 14-day mean stays below 0 C
until late spring, so once a pass closes it stays closed for the rest of the 1 Oct - 31 Mar window.
That is the "stock before the pass closes" behaviour the planner is built around.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

OPEN, AT_RISK, CLOSED = "OPEN", "AT_RISK", "CLOSED"


def closure_labels(pass_weather: pd.DataFrame, rule: dict[str, Any]) -> pd.DataFrame:
    """Daily closure state per pass.

    `pass_weather` needs columns location_id, date (datetime64), t_mean_c, snowfall_cm and holds
    one contiguous winter at a time per pass; windows never span the gap between seasons.

    Returns pass_id, date, snow_3d_cm, temp_14d_c, closed (bool), days_closed (int; consecutive
    closed days up to and including the row, 0 when open).
    """
    out: list[pd.DataFrame] = []
    threshold = float(rule["snow_3d_close_cm"])
    reopen_temp = float(rule["reopen_mean_temp_14d_c"])
    start_m, start_d = (int(x) for x in rule["season_start_mmdd"].split("-"))

    for pass_id, grp in pass_weather.groupby("location_id", sort=True):
        grp = grp.sort_values("date").reset_index(drop=True)
        # A winter is named by its start year; Jan-Sep belong to the winter that began last year.
        winter = grp["date"].dt.year.where(grp["date"].dt.month >= 10, grp["date"].dt.year - 1)
        snow_3d = grp.groupby(winter)["snowfall_cm"].transform(
            lambda s: s.rolling(3, min_periods=1).sum()
        )
        temp_14d = grp.groupby(winter)["t_mean_c"].transform(
            lambda s: s.rolling(14, min_periods=1).mean()
        )

        closed = [False] * len(grp)
        days_closed = [0] * len(grp)
        state, run, current_winter = False, 0, None
        for i, row in grp.iterrows():
            if winter.iloc[i] != current_winter:  # new season starts open
                current_winter, state, run = winter.iloc[i], False, 0
            d = row["date"]
            season_active = (d.month, d.day) >= (start_m, start_d) or d.month < 10
            if season_active:
                if not state and snow_3d.iloc[i] >= threshold:
                    state = True
                elif state and temp_14d.iloc[i] > reopen_temp:
                    state = False
            run = run + 1 if state else 0
            closed[i], days_closed[i] = state, run

        out.append(
            pd.DataFrame(
                {
                    "pass_id": pass_id,
                    "date": grp["date"],
                    "snow_3d_cm": snow_3d.round(2),
                    "temp_14d_c": temp_14d.round(2),
                    "closed": closed,
                    "days_closed": days_closed,
                }
            )
        )
    if not out:
        return pd.DataFrame(
            columns=["pass_id", "date", "snow_3d_cm", "temp_14d_c", "closed", "days_closed"]
        )
    return pd.concat(out, ignore_index=True)


def interim_status(closed: bool, snow_3d_cm: float, rule: dict[str, Any]) -> str:
    """OPEN / AT_RISK / CLOSED from the rule labels alone (no probability yet)."""
    if closed:
        return CLOSED
    if snow_3d_cm >= float(rule["at_risk_fraction"]) * float(rule["snow_3d_close_cm"]):
        return AT_RISK
    return OPEN
