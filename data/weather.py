"""Daily weather per post and per pass, as Parquet.

Two sources, and every row is stamped with the one it came from:

* ``open-meteo``: Open-Meteo's archive API, one request per chunk of locations per winter.
  This is the real-weather path. It needs outbound internet, so it runs at `make data` time only;
  the running app is offline.
* ``synthetic-climatology``: a deterministic stand-in (seeded, regionally correlated storms, an
  altitude lapse rate) used when Open-Meteo cannot be reached or when asked for explicitly. It is
  an assumption-driven climatology, NOT observed weather, and the build says so loudly.

Only winter days (1 Oct - 31 Mar of each season in config/sector.yaml) are fetched or generated.

Usage:  python -m data.weather [--source auto|live|synthetic] [--refresh]
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import zlib
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd

from config.loader import ROOT, load_sector, load_weather_cfg, winter_bounds, winters

log = logging.getLogger("rasad.weather")

CACHE_DIR = ROOT / "data" / "cache"
OPEN_METEO = "open-meteo"
SYNTHETIC = "synthetic-climatology"
COLUMNS = ["location_id", "date", "t_mean_c", "t_min_c", "snowfall_cm", "precip_mm", "source"]

_sleep = time.sleep  # swapped out in tests


class WeatherFetchError(RuntimeError):
    """Open-Meteo could not be reached, refused the request, or returned incomplete data."""


# --------------------------------------------------------------------------- locations


def build_locations(sector: dict[str, Any]) -> list[dict[str, Any]]:
    """One weather location per post and per pass. `region` is the gating pass, used to correlate
    synthetic weather between a pass and the posts behind it."""
    locs = [
        {
            "id": p["id"],
            "kind": "post",
            "lat": p["lat"],
            "lon": p["lon"],
            "altitude_m": p["altitude_m"],
            "region": p["via_pass"],
        }
        for p in sector["posts"]
    ]
    locs += [
        {
            "id": p["id"],
            "kind": "pass",
            "lat": p["lat"],
            "lon": p["lon"],
            "altitude_m": p["altitude_m"],
            "region": p["id"],
        }
        for p in sector["passes"]
    ]
    return sorted(locs, key=lambda loc: loc["id"])


# --------------------------------------------------------------------------- Open-Meteo


def _request(client: httpx.Client, url: str, params: dict[str, str], cfg: dict[str, Any]) -> Any:
    last: Exception | None = None
    for attempt in range(int(cfg["retries"]) + 1):
        try:
            resp = client.get(url, params=params, timeout=float(cfg["timeout_s"]))
        except httpx.ProxyError as exc:
            # An egress proxy refusing the tunnel (e.g. 403 on CONNECT) is a policy decision.
            raise WeatherFetchError(f"proxy refused the connection: {exc}") from exc
        except httpx.HTTPError as exc:  # connection errors, timeouts: worth retrying
            last = exc
        else:
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code != 429 and resp.status_code < 500:
                # 4xx other than rate limiting will not get better on retry (e.g. a proxy 403).
                raise WeatherFetchError(
                    f"Open-Meteo returned HTTP {resp.status_code}: {resp.text[:200]}"
                )
            last = WeatherFetchError(f"Open-Meteo returned HTTP {resp.status_code}")
        if attempt < int(cfg["retries"]):
            _sleep(float(cfg["backoff_s"]) * 2**attempt)
    raise WeatherFetchError(f"Open-Meteo unreachable after retries: {last}")


def _parse_daily(loc_id: str, daily: dict[str, Any]) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "location_id": loc_id,
            "date": pd.to_datetime(daily["time"]),
            "t_mean_c": daily["temperature_2m_mean"],
            "t_min_c": daily["temperature_2m_min"],
            "snowfall_cm": daily["snowfall_sum"],
            "precip_mm": daily["precipitation_sum"],
        }
    )
    for col in ("t_mean_c", "t_min_c"):
        if frame[col].isna().any():
            raise WeatherFetchError(f"{loc_id}: Open-Meteo returned gaps in {col}")
    # Snowfall/precipitation come back null for days without data; treat only those as zero.
    frame[["snowfall_cm", "precip_mm"]] = frame[["snowfall_cm", "precip_mm"]].fillna(0.0)
    frame[["t_mean_c", "t_min_c", "snowfall_cm", "precip_mm"]] = frame[
        ["t_mean_c", "t_min_c", "snowfall_cm", "precip_mm"]
    ].astype("float64")
    return frame


def fetch_open_meteo(
    locs: list[dict[str, Any]],
    timeline: dict[str, Any],
    cfg: dict[str, Any],
    client: httpx.Client | None = None,
) -> pd.DataFrame:
    """Fetch every winter for every location. Raises WeatherFetchError on any failure so the
    caller can fall back as a whole; a partial real/synthetic mix is never returned."""
    om = cfg["open_meteo"]
    own_client = client is None
    client = client or httpx.Client()
    chunk = int(om["locations_per_request"])
    frames: list[pd.DataFrame] = []
    try:
        for winter in winters(timeline):
            start, end = winter_bounds(timeline, winter)
            for i in range(0, len(locs), chunk):
                batch = locs[i : i + chunk]
                params = {
                    "latitude": ",".join(str(loc["lat"]) for loc in batch),
                    "longitude": ",".join(str(loc["lon"]) for loc in batch),
                    # Pin the model's temperature downscaling to the post altitude.
                    "elevation": ",".join(str(loc["altitude_m"]) for loc in batch),
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "daily": ",".join(om["daily"]),
                    "timezone": "UTC",
                }
                payload = _request(client, om["url"], params, om)
                # One location returns an object; several return a list in request order.
                results = payload if isinstance(payload, list) else [payload]
                if len(results) != len(batch):
                    raise WeatherFetchError(
                        f"asked for {len(batch)} locations, got {len(results)} results"
                    )
                for loc, result in zip(batch, results, strict=True):
                    frames.append(_parse_daily(loc["id"], result["daily"]))
                _sleep(float(om["pause_between_requests_s"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherFetchError(f"unexpected Open-Meteo response shape: {exc!r}") from exc
    finally:
        if own_client:
            client.close()
    out = pd.concat(frames, ignore_index=True)
    out["source"] = OPEN_METEO
    return out[COLUMNS]


# --------------------------------------------------------------------------- synthetic


def _key(name: str) -> int:
    """Stable integer for seeding; unlike a list position it survives adding entries."""
    return zlib.crc32(name.encode())


def _ar1(rng: np.random.Generator, n: int, phi: float) -> np.ndarray:
    """Unit-variance AR(1) series."""
    eps = rng.standard_normal(n)
    out = np.empty(n)
    out[0] = eps[0]
    scale = np.sqrt(1.0 - phi**2)
    for t in range(1, n):
        out[t] = phi * out[t - 1] + scale * eps[t]
    return out


def synthetic_weather(
    locs: list[dict[str, Any]], timeline: dict[str, Any], cfg: dict[str, Any], seed: int
) -> pd.DataFrame:
    """Deterministic synthetic climatology: same seed and config give identical output."""
    sc = cfg["synthetic"]
    regions = sorted({loc["region"] for loc in locs})
    share = float(sc["regional_share"])
    frames: list[pd.DataFrame] = []

    for winter in winters(timeline):
        start, end = winter_bounds(timeline, winter)
        dates = pd.date_range(start, end, freq="D")
        n = len(dates)
        doy = dates.dayofyear.to_numpy()
        seasonal = float(sc["seasonal_amplitude_c"]) * np.cos(
            2 * np.pi * (doy - int(sc["peak_doy"])) / 365.0
        )
        # Storms peak in mid-winter and are shared across a region.
        storm_weight = 0.3 + 0.7 * (0.5 + 0.5 * np.cos(2 * np.pi * (doy - 15) / 365.0))
        storm_prob = float(sc["storm"]["base_prob"]) * storm_weight

        regional: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for region in regions:
            rng = np.random.default_rng([seed, 100, winter, _key(region)])
            temp_dev = _ar1(rng, n, float(sc["ar1"]))
            storms = rng.random(n) < storm_prob
            regional[region] = (temp_dev, storms)

        for loc in locs:
            rng = np.random.default_rng([seed, 200, winter, _key(loc["id"])])
            reg_temp, reg_storm = regional[loc["region"]]
            local_temp = _ar1(rng, n, float(sc["ar1"]))
            dev = np.sqrt(share) * reg_temp + np.sqrt(1.0 - share) * local_temp
            alt_km = (loc["altitude_m"] - 3500.0) / 1000.0
            t_mean = (
                float(sc["mean_temp_at_3500m_c"])
                - float(sc["lapse_c_per_km"]) * alt_km
                + seasonal
                + float(sc["daily_sd_c"]) * dev
            )

            # Most storms reach every location in the region; a few are purely local.
            storm_day = (reg_storm & (rng.random(n) < 0.85)) | (rng.random(n) < 0.02)
            alt_term = max(0.3, 1.0 + alt_km / float(sc["storm"]["altitude_scale_km"]))
            mean_cm = (
                float(sc["storm"]["mean_cm"])
                * float(sc["region_snow_multiplier"][loc["region"]])
                * alt_term
            )
            amount = np.where(storm_day, rng.exponential(mean_cm, n), 0.0)
            t_mean = t_mean.round(2)  # decide snow on the value that is stored
            snowing = t_mean < 2.0
            frames.append(
                pd.DataFrame(
                    {
                        "location_id": loc["id"],
                        "date": dates,
                        "t_mean_c": t_mean,
                        "t_min_c": (t_mean - float(sc["tmin_offset_c"])).round(2),
                        "snowfall_cm": np.where(snowing, amount, 0.0).round(2),
                        # ~1 mm water equivalent per cm of snow (10:1); rain falls as mm directly.
                        "precip_mm": amount.round(2),
                    }
                )
            )

    out = pd.concat(frames, ignore_index=True)
    out["source"] = SYNTHETIC
    return out[COLUMNS]


# --------------------------------------------------------------------------- cache + entry point


def _cache_path(cache_dir: Path, source: str) -> Path:
    return cache_dir / f"weather_{source}.parquet"


def _matches(df: pd.DataFrame, locs: list[dict[str, Any]], timeline: dict[str, Any]) -> bool:
    """A cached file is reusable only if it covers exactly today's locations and winters."""
    if set(df["location_id"]) != {loc["id"] for loc in locs}:
        return False
    expected_days = sum(
        (winter_bounds(timeline, w)[1] - winter_bounds(timeline, w)[0]).days + 1
        for w in winters(timeline)
    )
    return bool((df.groupby("location_id").size() == expected_days).all())


def load_weather(
    source: str = "auto",
    seed: int = 42,
    cache_dir: Path = CACHE_DIR,
    refresh: bool = False,
    client: httpx.Client | None = None,
    sector: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Return the weather table; the `source` column says what each row really is.

    auto       cached Open-Meteo data if present, else fetch it, else fall back to synthetic.
    live       cached or freshly fetched Open-Meteo data; raise WeatherFetchError if unavailable.
    synthetic  never touch the network (CI, determinism checks, offline laptops).
    """
    if source not in {"auto", "live", "synthetic"}:
        raise ValueError(f"source must be auto, live or synthetic, not {source!r}")
    sector = sector or load_sector()
    cfg = load_weather_cfg()
    locs = build_locations(sector)
    timeline = sector["timeline"]
    cache_dir.mkdir(parents=True, exist_ok=True)

    if source in {"auto", "live"}:
        real_path = _cache_path(cache_dir, OPEN_METEO)
        if real_path.exists() and not refresh:
            cached = pd.read_parquet(real_path)
            if _matches(cached, locs, timeline):
                log.info("weather: using cached Open-Meteo data (%s)", real_path)
                return cached
            log.warning("weather: cached Open-Meteo data no longer matches config; refetching")
        try:
            df = fetch_open_meteo(locs, timeline, cfg, client=client)
        except WeatherFetchError as exc:
            if source == "live":
                raise
            log.warning(
                "weather: Open-Meteo unavailable (%s). FALLING BACK TO SYNTHETIC CLIMATOLOGY: "
                "temperatures and snowfall in this build are simulated, not observed.",
                exc,
            )
        else:
            df.to_parquet(real_path, index=False)
            real_path.with_suffix(".json").write_text(
                json.dumps(
                    {
                        "source": OPEN_METEO,
                        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    },
                    indent=2,
                )
            )
            return df

    df = synthetic_weather(locs, timeline, cfg, seed)
    df.to_parquet(_cache_path(cache_dir, SYNTHETIC), index=False)
    return df


def season_days(timeline: dict[str, Any]) -> dict[int, tuple[date, date]]:
    return {w: winter_bounds(timeline, w) for w in winters(timeline)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", choices=["auto", "live", "synthetic"], default="auto")
    parser.add_argument("--refresh", action="store_true", help="ignore cached Open-Meteo data")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    df = load_weather(args.source, seed=args.seed, refresh=args.refresh)
    print(
        f"{len(df):,} rows, {df['location_id'].nunique()} locations, source={df['source'].iloc[0]}"
    )


if __name__ == "__main__":
    main()
