"""Score every demand model on the held-out winter, per formation.

Metrics on consumption in "base-rate soldier-days" (quantity / class base rate), so the five classes
add up meaningfully:

    WAPE      sum |actual - P50| / sum actual
    pinball   mean pinball loss over P10, P50, P90 (per row, same units)
    coverage  share of actuals inside [P10, P90]  (80% would be perfectly calibrated)

Two temperature settings: 'observed' stands for horizons 1-16 days (a weather forecast; ours is
the observed value, so optimistic) and 'climatology' for horizons 17-30.

Usage:  python -m forecast.eval_forecast [--db PATH]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from api.db import connect
from api.settings import get_settings
from config.loader import ROOT, load_consumption, load_sector
from eval.report import write_section
from forecast.features import evaluation_rows, load_panel
from forecast.model import pinball_np
from forecast.train import load_models

RESULTS_JSON = ROOT / "eval" / "forecast_results.json"
ORDER = ("local", "federated", "central", "lightgbm", "naive")
LABEL = {
    "local": "Local only (own formation)",
    "federated": "Federated (FedAvg)",
    "central": "Central (pooled data)",
    "lightgbm": "Central LightGBM",
    "naive": "Naive lagged mean",
}


def metrics(pred: np.ndarray, y: np.ndarray, scale: np.ndarray) -> dict[str, float]:
    p, a = pred * scale[:, None], y * scale
    return {
        "wape": float(np.abs(a - p[:, 1]).sum() / a.sum()),
        "pinball": float(pinball_np(p, a).mean()),
        "coverage": float(((a >= p[:, 0]) & (a <= p[:, 2])).mean()),
        "n": int(len(a)),
    }


def evaluate(db_path: Path) -> dict[str, Any]:
    sector, ccfg = load_sector(), load_consumption()
    holdout = int(sector["timeline"]["holdout_winter"])
    conn = connect(db_path, readonly=True)
    try:
        panel = load_panel(conn, sector, ccfg)
    finally:
        conn.close()
    models = load_models(db_path)
    out: dict[str, Any] = {"holdout_winter": f"{holdout}-{str(holdout + 1)[2:]}", "results": {}}
    for setting in ("observed", "climatology"):
        x, y, meta = evaluation_rows(panel, holdout, setting)
        scale = meta["scale"].to_numpy(dtype=float)
        res: dict[str, dict[str, Any]] = {}
        for f in sorted(sector["formations"]):
            mask = (meta["formation"] == f).to_numpy()
            row = {}
            for name in ORDER:
                fn = models[f"local_{f}"] if name == "local" else models[name]
                row[name] = metrics(fn(x[mask]), y[mask], scale[mask])
            res[f] = row
        out["results"][setting] = res
    return out


def pct(v: float) -> str:
    return f"{100 * v:.1f}%"


def markdown(res: dict[str, Any]) -> str:
    obs, clim = res["results"]["observed"], res["results"]["climatology"]
    lines = [
        f"## Demand forecasting (held-out winter {res['holdout_winter']})",
        "",
        "Trained on earlier winters only. F3 is newly inducted and has one training winter; F1 and"
        " F2 have four. WAPE of the P50 forecast (lower is better), days 1-16 / days 17-30 ahead.",
        "",
        "| Model | F1 | F2 | F3 |",
        "|---|---:|---:|---:|",
    ]
    for name in ORDER:
        cells = [f"{pct(obs[f][name]['wape'])} / {pct(clim[f][name]['wape'])}" for f in sorted(obs)]
        lines.append(f"| {LABEL[name]} | {' | '.join(cells)} |")
    lines += [
        "",
        "Pinball loss (lower is better) and P10-P90 coverage (80% is calibrated), days 17-30:",
        "",
        "| Model | F1 pinball | F2 pinball | F3 pinball | F1 cov. | F2 cov. | F3 cov. |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ORDER:
        pin = [f"{clim[f][name]['pinball']:.3f}" for f in sorted(clim)]
        cov = [pct(clim[f][name]["coverage"]) for f in sorted(clim)]
        lines.append(f"| {LABEL[name]} | {' | '.join(pin)} | {' | '.join(cov)} |")
    f3 = clim["F3"]
    lines += [
        "",
        f"**F3 (data-poor), days 17-30:** local {pct(f3['local']['wape'])} -> federated "
        f"{pct(f3['federated']['wape'])} WAPE; central {pct(f3['central']['wape'])}. Federated "
        "training moved model weights only; no report left its formation.",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=get_settings().db_path)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    res = evaluate(args.db)
    text = markdown(res)
    print(text)
    if not args.no_write:
        RESULTS_JSON.write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
        write_section("forecast", text)


if __name__ == "__main__":
    main()
