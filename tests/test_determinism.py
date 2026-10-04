from __future__ import annotations

from data.build import build


def test_same_seed_gives_identical_database_contents(built, tmp_path):
    again = build(tmp_path / "again.db", seed=42, source="synthetic", cache_dir=tmp_path / "cache")
    assert again["digest"] == built["summary"]["digest"]


def test_different_seed_changes_the_data(built, tmp_path):
    other = build(tmp_path / "other.db", seed=7, source="synthetic", cache_dir=tmp_path / "cache")
    assert other["digest"] != built["summary"]["digest"]
