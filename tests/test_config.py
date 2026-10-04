from __future__ import annotations

import copy

import pytest

from config.loader import (
    load_constraints,
    load_consumption,
    load_sector,
    supply_classes,
    validate_sector,
    winter_bounds,
    winters,
)


def test_sector_shape():
    sector = load_sector()
    assert len(sector["posts"]) == 42
    assert set(sector["formations"]) == {"F1", "F2", "F3"}
    assert len(sector["depots"]) == 5
    assert {p["id"] for p in sector["passes"]} == {
        "ZOJI_LA",
        "KHARDUNG_LA",
        "CHANG_LA",
        "TANGLANG_LA",
    }
    per_formation = {f: 0 for f in sector["formations"]}
    for post in sector["posts"]:
        per_formation[post["formation"]] += 1
    assert per_formation == {"F1": 14, "F2": 14, "F3": 14}


def test_every_pass_gates_at_least_one_post():
    sector = load_sector()
    gated = {p["via_pass"] for p in sector["posts"]}
    assert gated == {p["id"] for p in sector["passes"]}


def test_f3_is_the_data_poor_formation():
    sector = load_sector()
    timeline = sector["timeline"]
    assert sector["formations"]["F3"]["history_start"] == "2024-10-01"
    assert timeline["holdout_winter"] == timeline["last_winter"]
    # F3 trains on exactly one winter before the held-out one.
    assert [w for w in winters(timeline) if w >= 2024 and w < timeline["holdout_winter"]] == [2024]


def test_winter_bounds():
    start, end = winter_bounds(load_sector()["timeline"], 2021)
    assert (start.isoformat(), end.isoformat()) == ("2021-10-01", "2022-03-31")


def test_supply_classes_match_the_spec():
    assert set(supply_classes()) == {"rations", "fuel", "medical", "ammunition", "spares"}
    assert load_consumption()["classes"]["rations"]["rate"] == 2.5


def test_constraints_cover_every_class_and_mode():
    c = load_constraints()
    assert set(c["min_stock_days"]) == set(supply_classes())
    assert set(c["modes"]) == {"truck", "mule", "heli"}
    assert c["closure_rule"]["snow_3d_close_cm"] == 15.0


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda s: s["posts"][0].update(depot="NOWHERE"), "unknown depot"),
        (lambda s: s["posts"][0].update(via_pass="NOWHERE"), "unknown via_pass"),
        (lambda s: s["posts"][0].update(formation="F9"), "unknown formation"),
        (lambda s: s["posts"][0].update(access=["tank"]), "access must be"),
        (lambda s: s["posts"][0].update(lat=77.0), "bounding box"),
        (lambda s: s["posts"].append(copy.deepcopy(s["posts"][0])), "duplicate post ids"),
    ],
)
def test_validate_sector_rejects_bad_input(mutate, message):
    sector = copy.deepcopy(load_sector())
    mutate(sector)
    with pytest.raises(ValueError, match=message):
        validate_sector(sector)
