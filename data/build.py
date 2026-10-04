"""`make data`: weather -> consumption -> stock/reports -> SQLite.

Deterministic for a fixed seed and weather source. With WEATHER_SOURCE=auto the result also depends
on whether Open-Meteo was reachable, which is why the source is stamped into the database.

Usage:  python -m data.build [--seed 42] [--source auto|live|synthetic] [--out PATH] [--digest]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from api.db import connect, digest
from api.settings import get_settings
from closure.labels import closure_labels
from config.loader import load_constraints, load_consumption, load_sector
from data.generate import generate_consumption
from data.load import load_database
from data.stock import simulate_stock
from data.weather import CACHE_DIR, SYNTHETIC, load_weather
from gate import batch
from gate.anomaly import model_path
from gate.pipeline import Gate

log = logging.getLogger("rasad.build")


def build(
    db_path: Path,
    seed: int = 42,
    source: str = "auto",
    cache_dir: Path = CACHE_DIR,
    refresh_weather: bool = False,
) -> dict[str, object]:
    t0 = time.perf_counter()
    sector = load_sector()
    ccfg = load_consumption()
    constraints = load_constraints()

    weather = load_weather(
        source, seed=seed, cache_dir=cache_dir, refresh=refresh_weather, sector=sector
    )
    sources = sorted(weather["source"].unique())
    if len(sources) != 1:
        raise RuntimeError(f"weather mixes sources {sources}; refusing to build")
    weather_source = sources[0]

    pass_ids = [p["id"] for p in sector["passes"]]
    closure = closure_labels(
        weather[weather["location_id"].isin(pass_ids)], constraints["closure_rule"]
    )

    consumption = generate_consumption(sector, ccfg, weather, seed)
    reports, deliveries, stats = simulate_stock(sector, ccfg, consumption, closure, seed)

    # Sign every report with its post's simulated key, then run the history through the gate.
    keys = batch.public_keys([p["id"] for p in sector["posts"]], seed)
    reports = batch.sign_reports(reports, seed)
    post_ids = {p["id"] for p in sector["posts"]}
    temps = {
        (r.location_id, r.date.strftime("%Y-%m-%d")): float(r.t_mean_c)
        for r in weather.itertuples()
        if r.location_id in post_ids
    }
    classes = list(ccfg["classes"])
    scorer, deferred, rows_used = batch.train_scorer(
        reports,
        deliveries,
        keys,
        sector,
        temps,
        constraints["gate"],
        float(ccfg["temp_ref_c"]),
        classes,
        seed,
    )
    scorer.save(model_path(db_path))
    verdicts = batch.run_history(
        reports, deliveries, keys, Gate(set(classes), scorer=deferred), batch.post_infos(sector)
    )
    stats["verdicts"] = {
        k: int(n) for k, n in verdicts["verdict"].value_counts().sort_index().items()
    }

    meta = {
        "seed": str(seed),
        "weather_source": weather_source,
        "consumption_source": "synthetic",
        "report_rows": str(stats["report_rows"]),
        "delivery_rows": str(stats["delivery_rows"]),
        "stockout_days": str(stats["stockout_days"]),
        **{f"gate_{k.lower()}": str(n) for k, n in stats["verdicts"].items()},
    }
    load_database(
        db_path,
        sector,
        constraints["closure_rule"],
        weather,
        closure,
        reports,
        deliveries,
        verdicts,
        keys,
        meta,
    )

    conn = connect(db_path, readonly=True)
    try:
        db_digest = digest(conn)
    finally:
        conn.close()

    summary = {
        **stats,
        "weather_source": weather_source,
        "digest": db_digest,
        "seconds": round(time.perf_counter() - t0, 1),
    }
    if weather_source == SYNTHETIC:
        log.warning(
            "WEATHER IS SYNTHETIC in this build (Open-Meteo not used). Consumption is synthetic "
            "either way; see docs/real-vs-mocked.md."
        )
    return summary


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=settings.seed)
    parser.add_argument(
        "--source", choices=["auto", "live", "synthetic"], default=settings.weather_source
    )
    parser.add_argument(
        "--out", type=Path, default=settings.db_path, help="SQLite database to write"
    )
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--refresh-weather", action="store_true", help="refetch Open-Meteo data")
    parser.add_argument("--digest", action="store_true", help="print only the content digest")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    summary = build(args.out, args.seed, args.source, args.cache_dir, args.refresh_weather)
    if args.digest:
        print(summary["digest"])
    else:
        print(f"wrote {args.out}")
        for key, value in summary.items():
            print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
