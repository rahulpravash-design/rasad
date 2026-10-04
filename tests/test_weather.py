from __future__ import annotations

import httpx
import pandas as pd
import pytest

from config.loader import load_sector, load_weather_cfg, winter_bounds, winters
from data import weather
from data.weather import (
    COLUMNS,
    OPEN_METEO,
    SYNTHETIC,
    WeatherFetchError,
    build_locations,
    fetch_open_meteo,
    load_weather,
    synthetic_weather,
)


@pytest.fixture(scope="module")
def setup():
    sector = load_sector()
    return sector, build_locations(sector), sector["timeline"], load_weather_cfg()


def _days(timeline) -> int:
    return sum(
        (winter_bounds(timeline, w)[1] - winter_bounds(timeline, w)[0]).days + 1
        for w in winters(timeline)
    )


def test_locations_cover_posts_and_passes(setup):
    sector, locs, _, _ = setup
    assert len(locs) == 42 + 4
    assert {loc["kind"] for loc in locs} == {"post", "pass"}
    assert [loc["id"] for loc in locs] == sorted(loc["id"] for loc in locs)


def test_synthetic_is_deterministic_and_seed_sensitive(setup):
    _, locs, timeline, cfg = setup
    a = synthetic_weather(locs, timeline, cfg, seed=42)
    b = synthetic_weather(locs, timeline, cfg, seed=42)
    c = synthetic_weather(locs, timeline, cfg, seed=7)
    pd.testing.assert_frame_equal(a, b)
    assert not a["t_mean_c"].equals(c["t_mean_c"])


def test_synthetic_shape_and_stamp(setup):
    _, locs, timeline, cfg = setup
    df = synthetic_weather(locs, timeline, cfg, seed=42)
    assert list(df.columns) == COLUMNS
    assert (df["source"] == SYNTHETIC).all()
    assert len(df) == len(locs) * _days(timeline)
    assert not df.isna().any().any()
    assert (df["snowfall_cm"] >= 0).all()
    # Snow only falls when it is cold.
    assert (df.loc[df["snowfall_cm"] > 0, "t_mean_c"] < 2.0).all()


def test_synthetic_climate_is_physically_sensible(setup):
    _, locs, timeline, cfg = setup
    df = synthetic_weather(locs, timeline, cfg, seed=42)
    df["month"] = df["date"].dt.month
    mean = df.groupby(["location_id", "month"])["t_mean_c"].mean().unstack()
    # Mid-winter is colder than the start of the season, and altitude cools.
    assert (mean[1] < mean[10]).all()
    assert mean.loc["KHARDUNG_LA", 1] < mean.loc["LEH-01", 1] - 8
    # Leh in January lands near the ~-7 C the climatology is tuned to.
    assert -10 < mean.loc["LEH-01", 1] < -4


def test_posts_share_weather_with_their_pass(setup):
    _, locs, timeline, cfg = setup
    df = synthetic_weather(locs, timeline, cfg, seed=42)
    wide = df.pivot(index="date", columns="location_id", values="t_mean_c")
    # Day-to-day changes within each winter: removes the seasonal cycle every location shares, so
    # what is left is the regional weather signal.
    winter = wide.index.year.where(wide.index.month >= 10, wide.index.year - 1)
    changes = wide.groupby(winter).diff().dropna()
    near = changes["DRASS-01"].corr(changes["ZOJI_LA"])  # DRASS-01 sits behind Zoji La
    far = changes["DRASS-01"].corr(changes["CHANG_LA"])  # different region
    assert near > 0.4
    assert abs(far) < 0.15


# ------------------------------------------------------------------ Open-Meteo (mocked transport)


def _payload(batch_ids: list[str], start: str, end: str, *, gap: bool = False) -> list[dict]:
    times = [d.strftime("%Y-%m-%d") for d in pd.date_range(start, end)]
    out = []
    for i, _ in enumerate(batch_ids):
        t = [-5.0 - i] * len(times)
        if gap:
            t[3] = None
        out.append(
            {
                "daily": {
                    "time": times,
                    "temperature_2m_mean": t,
                    "temperature_2m_min": [x - 4 if x is not None else None for x in t],
                    "snowfall_sum": [None] + [1.5] * (len(times) - 1),  # null = no snowfall record
                    "precipitation_sum": [0.2] * len(times),
                }
            }
        )
    return out


def _mock_client(setup, *, gap=False, status=200, seen=None) -> httpx.Client:
    _, locs, _, _ = setup
    by_coord = {(str(loc["lat"]), str(loc["lon"])): loc["id"] for loc in locs}

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(dict(request.url.params))
        if status != 200:
            return httpx.Response(status, text="nope")
        p = request.url.params
        ids = [
            by_coord[(la, lo)]
            for la, lo in zip(p["latitude"].split(","), p["longitude"].split(","), strict=True)
        ]
        payload = _payload(ids, p["start_date"], p["end_date"], gap=gap)
        # Real Open-Meteo returns an object for one location and a list for several.
        return httpx.Response(200, json=payload[0] if len(payload) == 1 else payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(weather, "_sleep", lambda s: None)


def test_fetch_parses_open_meteo_shape_and_pins_elevation(setup):
    _, locs, timeline, cfg = setup
    seen: list[dict] = []
    df = fetch_open_meteo(locs, timeline, cfg, client=_mock_client(setup, seen=seen))
    assert list(df.columns) == COLUMNS
    assert (df["source"] == OPEN_METEO).all()
    assert len(df) == len(locs) * _days(timeline)
    assert (df["snowfall_cm"] >= 0).all()  # nulls became zero
    # Chunked, with elevation sent for each location and the daily variables requested.
    assert len(seen) == len(winters(timeline)) * -(-len(locs) // 10)
    assert seen[0]["elevation"].count(",") == 9
    assert (
        seen[0]["daily"] == "temperature_2m_mean,temperature_2m_min,snowfall_sum,precipitation_sum"
    )
    assert seen[0]["start_date"].endswith("-10-01") and seen[0]["end_date"].endswith("-03-31")


def test_fetch_handles_single_location_object(setup):
    _, locs, timeline, cfg = setup
    cfg = {**cfg, "open_meteo": {**cfg["open_meteo"], "locations_per_request": 1}}
    df = fetch_open_meteo(locs[:2], timeline, cfg, client=_mock_client(setup))
    assert df["location_id"].nunique() == 2


def test_fetch_rejects_gaps_in_temperature(setup):
    _, locs, timeline, cfg = setup
    with pytest.raises(WeatherFetchError, match="gaps"):
        fetch_open_meteo(locs, timeline, cfg, client=_mock_client(setup, gap=True))


def test_fetch_does_not_retry_a_403(setup):
    _, locs, timeline, cfg = setup
    seen: list[dict] = []
    with pytest.raises(WeatherFetchError, match="403"):
        fetch_open_meteo(locs, timeline, cfg, client=_mock_client(setup, status=403, seen=seen))
    assert len(seen) == 1


def test_fetch_does_not_retry_a_proxy_refusal(setup):
    _, locs, timeline, cfg = setup
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ProxyError("403 Forbidden")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(WeatherFetchError, match="proxy refused"):
        fetch_open_meteo(locs, timeline, cfg, client=client)
    assert len(calls) == 1


def test_fetch_retries_dropped_connections_then_gives_up(setup):
    _, locs, timeline, cfg = setup
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("connection reset")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(WeatherFetchError, match="unreachable"):
        fetch_open_meteo(locs, timeline, cfg, client=client)
    assert len(calls) == cfg["open_meteo"]["retries"] + 1


def test_fetch_retries_server_errors_then_gives_up(setup):
    _, locs, timeline, cfg = setup
    seen: list[dict] = []
    with pytest.raises(WeatherFetchError, match="unreachable"):
        fetch_open_meteo(locs, timeline, cfg, client=_mock_client(setup, status=503, seen=seen))
    assert len(seen) == cfg["open_meteo"]["retries"] + 1


# ------------------------------------------------------------------ cache + fallback


def test_auto_falls_back_to_synthetic_and_says_so(setup, tmp_path):
    sector = setup[0]
    df = load_weather(
        "auto", cache_dir=tmp_path, client=_mock_client(setup, status=403), sector=sector
    )
    assert set(df["source"]) == {SYNTHETIC}


def test_live_raises_instead_of_falling_back(setup, tmp_path):
    with pytest.raises(WeatherFetchError):
        load_weather(
            "live", cache_dir=tmp_path, client=_mock_client(setup, status=403), sector=setup[0]
        )


def test_auto_prefers_real_data_and_reuses_the_cache(setup, tmp_path):
    sector = setup[0]
    first = load_weather("auto", cache_dir=tmp_path, client=_mock_client(setup), sector=sector)
    assert set(first["source"]) == {OPEN_METEO}
    # Second call must not hit the network: a client that always fails proves the cache was used.
    second = load_weather(
        "auto", cache_dir=tmp_path, client=_mock_client(setup, status=403), sector=sector
    )
    assert set(second["source"]) == {OPEN_METEO}
    assert (tmp_path / "weather_open-meteo.json").exists()


def test_synthetic_never_touches_the_network(setup, tmp_path):
    def boom(request):
        raise AssertionError("network used")

    client = httpx.Client(transport=httpx.MockTransport(boom))
    df = load_weather("synthetic", cache_dir=tmp_path, client=client, sector=setup[0])
    assert set(df["source"]) == {SYNTHETIC}


def test_rejects_unknown_source(setup, tmp_path):
    with pytest.raises(ValueError, match="source must be"):
        load_weather("sometimes", cache_dir=tmp_path, sector=setup[0])
