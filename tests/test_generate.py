from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from config.loader import load_consumption, load_sector, load_weather_cfg
from data.generate import altitude_factor, generate_consumption, temp_factor
from data.weather import build_locations, synthetic_weather


@pytest.fixture(scope="module")
def world():
    sector = load_sector()
    weather = synthetic_weather(build_locations(sector), sector["timeline"], load_weather_cfg(), 42)
    return sector, load_consumption(), weather


def _subset(sector, ids):
    s = copy.deepcopy(sector)
    s["posts"] = [p for p in s["posts"] if p["id"] in ids]
    return s


def test_altitude_factor():
    assert altitude_factor(2000, 0.06, 2500) == 1.0
    assert altitude_factor(2500, 0.06, 2500) == 1.0
    assert altitude_factor(4500, 0.06, 2500) == pytest.approx(1.12)


def test_temp_factor():
    t = np.array([20.0, 15.0, 5.0, -15.0])
    assert temp_factor(t, 0.45, 15.0).tolist() == pytest.approx([1.0, 1.0, 1.45, 2.35])


def test_output_shape(world):
    sector, ccfg, weather = world
    ids = {"LEH-01", "HANLE-01"}
    df = generate_consumption(_subset(sector, ids), ccfg, weather, 42)
    assert set(df["post_id"]) == ids
    assert set(df["supply_class"]) == set(ccfg["classes"])
    assert (df["demand"] >= 0).all()
    assert df["demand"].dtype == "int64"
    assert not df.isna().any().any()


def test_formation_history_start(world):
    sector, ccfg, weather = world
    df = generate_consumption(_subset(sector, {"LEH-01", "HANLE-01"}), ccfg, weather, 42)
    first = df.groupby("post_id")["date"].min()
    assert first["LEH-01"] == pd.Timestamp("2021-10-01")  # F2: full history
    assert first["HANLE-01"] == pd.Timestamp("2024-10-01")  # F3: newly inducted


def test_mean_demand_follows_the_stated_formula(world):
    """Sum of demand over sum of the formula's expectation is 1 up to noise and rounding."""
    sector, ccfg, weather = world
    df = generate_consumption(_subset(sector, {"DRASS-01"}), ccfg, weather, 42)
    post = next(p for p in sector["posts"] if p["id"] == "DRASS-01")
    for cls, c in ccfg["classes"].items():
        sub = df[df["supply_class"] == cls]
        expected = (
            post["troops"]
            * c["rate"]
            * altitude_factor(post["altitude_m"], c["altitude_slope"], ccfg["altitude_start_m"])
            * temp_factor(sub["temp_c"].to_numpy(), c["temp_slope"], ccfg["temp_ref_c"])
            * sub["surge_mult"].to_numpy()
        )
        assert sub["demand"].sum() / expected.sum() == pytest.approx(1.0, abs=0.03), cls


def test_rations_use_the_sourced_rate(world):
    """Divide out altitude, cold and surge: 2.5 kg/soldier/day is what is left."""
    sector, ccfg, weather = world
    df = generate_consumption(_subset(sector, {"SANKOO-01"}), ccfg, weather, 42)
    post = next(p for p in sector["posts"] if p["id"] == "SANKOO-01")
    c = ccfg["classes"]["rations"]
    sub = df[df["supply_class"] == "rations"]
    factors = (
        altitude_factor(post["altitude_m"], c["altitude_slope"], ccfg["altitude_start_m"])
        * temp_factor(sub["temp_c"].to_numpy(), c["temp_slope"], ccfg["temp_ref_c"])
        * sub["surge_mult"].to_numpy()
    )
    implied_rate = (sub["demand"] / post["troops"] / factors).mean()
    assert implied_rate == pytest.approx(2.5, rel=0.02)


def test_colder_and_higher_posts_burn_more_fuel_per_soldier(world):
    sector, ccfg, weather = world
    df = generate_consumption(_subset(sector, {"KARGIL-01", "HANLE-01"}), ccfg, weather, 42)
    df = df[(df["supply_class"] == "fuel") & (df["date"] >= "2024-10-01")]
    troops = {p["id"]: p["troops"] for p in sector["posts"]}
    per_soldier = (df["demand"] / df["post_id"].map(troops)).groupby(df["post_id"]).mean()
    assert per_soldier["HANLE-01"] > 1.5 * per_soldier["KARGIL-01"]


def test_surges_raise_demand(world):
    sector, ccfg, weather = world
    df = generate_consumption(_subset(sector, {"DRASS-01"}), ccfg, weather, 42)
    ammo = df[df["supply_class"] == "ammunition"]
    assert (ammo["surge_mult"] > 1.0).any()
    assert (
        ammo.loc[ammo["surge_mult"] > 1.0, "demand"].mean()
        > ammo.loc[ammo["surge_mult"] == 1.0, "demand"].mean()
    )


def test_deterministic_and_stable_when_other_posts_are_added(world):
    sector, ccfg, weather = world
    a = generate_consumption(_subset(sector, {"LEH-01"}), ccfg, weather, 42)
    b = generate_consumption(_subset(sector, {"LEH-01"}), ccfg, weather, 42)
    pd.testing.assert_frame_equal(a, b)
    # Adding posts (including ones that sort before LEH-01) must not change LEH-01's series.
    c = generate_consumption(
        _subset(sector, {"LEH-01", "DRASS-01", "KARGIL-01"}), ccfg, weather, 42
    )
    pd.testing.assert_frame_equal(
        a.reset_index(drop=True),
        c[c["post_id"] == "LEH-01"].reset_index(drop=True),
    )
    d = generate_consumption(_subset(sector, {"LEH-01"}), ccfg, weather, 7)
    assert not a["demand"].equals(d["demand"])
