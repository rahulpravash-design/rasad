from __future__ import annotations

import pandas as pd
import pytest

from closure.labels import AT_RISK, CLOSED, OPEN, closure_labels, interim_status
from config.loader import load_constraints

RULE = load_constraints()["closure_rule"]


def _weather(
    start: str,
    snow: dict[str, float],
    temp: float | dict[str, float] = -10.0,
    end: str = "2022-03-31",
    pass_id: str = "P",
) -> pd.DataFrame:
    dates = pd.date_range(start, end)
    t = [temp.get(d.strftime("%Y-%m-%d"), -10.0) if isinstance(temp, dict) else temp for d in dates]
    return pd.DataFrame(
        {
            "location_id": pass_id,
            "date": dates,
            "t_mean_c": t,
            "snowfall_cm": [snow.get(d.strftime("%Y-%m-%d"), 0.0) for d in dates],
        }
    )


def _at(labels: pd.DataFrame, day: str) -> pd.Series:
    return labels[labels["date"] == day].iloc[0]


def test_heavy_snow_after_season_start_closes_the_pass():
    labels = closure_labels(_weather("2021-10-01", {"2021-11-10": 16.0}), RULE)
    assert not _at(labels, "2021-11-09")["closed"]
    assert _at(labels, "2021-11-10")["closed"]
    assert _at(labels, "2021-11-12")["days_closed"] == 3


def test_snow_before_the_season_start_is_ignored():
    labels = closure_labels(_weather("2021-10-01", {"2021-10-20": 40.0}), RULE)
    assert not labels["closed"].any()


def test_threshold_is_cumulative_over_three_days():
    snow = {"2021-12-01": 5.0, "2021-12-02": 5.0, "2021-12-03": 5.0}
    labels = closure_labels(_weather("2021-10-01", snow), RULE)
    assert not _at(labels, "2021-12-02")["closed"]  # 10 cm so far
    assert _at(labels, "2021-12-03")["closed"]  # 15 cm over three days


def test_snow_older_than_three_days_does_not_count():
    snow = {"2021-12-01": 8.0, "2021-12-04": 8.0}
    labels = closure_labels(_weather("2021-10-01", snow), RULE)
    assert not labels["closed"].any()


def test_stays_closed_while_cold_and_reopens_when_14_day_mean_exceeds_zero():
    warm = {d.strftime("%Y-%m-%d"): 5.0 for d in pd.date_range("2022-02-01", "2022-03-31")}
    labels = closure_labels(_weather("2021-10-01", {"2021-11-10": 20.0}, temp=warm), RULE)
    assert _at(labels, "2022-01-31")["closed"]  # still freezing
    assert _at(labels, "2022-02-05")["closed"]  # warm, but the 14-day mean is still < 0
    assert not _at(labels, "2022-03-31")["closed"]  # mean now > 0
    assert _at(labels, "2022-03-31")["days_closed"] == 0


def test_each_winter_starts_open():
    first = _weather("2021-10-01", {"2021-11-10": 20.0})
    second = _weather("2022-10-01", {}, end="2023-03-31")
    labels = closure_labels(pd.concat([first, second]), RULE)
    assert _at(labels, "2022-03-31")["closed"]
    assert not _at(labels, "2022-10-01")["closed"]
    assert not labels[labels["date"] >= "2022-10-01"]["closed"].any()


def test_passes_are_independent():
    labels = closure_labels(
        pd.concat(
            [
                _weather("2021-10-01", {"2021-11-10": 20.0}, pass_id="A"),
                _weather("2021-10-01", {}, pass_id="B"),
            ]
        ),
        RULE,
    )
    assert labels[labels["pass_id"] == "A"]["closed"].any()
    assert not labels[labels["pass_id"] == "B"]["closed"].any()


@pytest.mark.parametrize(
    ("closed", "snow", "expected"),
    [(True, 0.0, CLOSED), (False, 7.5, AT_RISK), (False, 7.4, OPEN), (False, 14.9, AT_RISK)],
)
def test_interim_status(closed, snow, expected):
    assert interim_status(closed, snow, RULE) == expected
