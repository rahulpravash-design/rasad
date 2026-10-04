"""`make train`: fit every demand model on the pre-holdout winters and save them.

    local_F1, local_F2, local_F3   one MLP per formation, trained on its own reports only
    federated                      FedAvg of the same MLP across the three formations
    central                        one MLP on all pooled reports (privacy-violating upper bound)
    lightgbm                       pooled LightGBM quantile regression (non-neural comparator)
    naive                          lagged 30-58 day mean with training-residual quantiles

Same architecture, seed and amount of training (epochs) for every MLP so the comparison is fair.

Usage:  python -m forecast.train [--db PATH]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np

from api.db import connect
from api.settings import get_settings
from config.loader import load_consumption, load_sector
from forecast import model as qm
from forecast.features import BASE_FEATURES, load_panel, training_rows
from forecast.federated import fedavg
from forecast.service import models_dir

EPOCHS = 20
ROUNDS = 10
LOCAL_EPOCHS = 2  # ROUNDS x LOCAL_EPOCHS == EPOCHS


def naive_predict(params: dict[str, float], x: np.ndarray) -> np.ndarray:
    base = x[:, BASE_FEATURES.index("mean_30_58")]
    return np.column_stack([base * params["q10"], base * params["q50"], base * params["q90"]])


def lgb_predict(boosters: list[lgb.Booster], x: np.ndarray) -> np.ndarray:
    return np.sort(np.column_stack([b.predict(x) for b in boosters]), axis=1)


def train_all(db_path: Path, seed: int = 42) -> dict[str, Any]:
    t0 = time.perf_counter()
    sector, ccfg = load_sector(), load_consumption()
    holdout = int(sector["timeline"]["holdout_winter"])
    conn = connect(db_path, readonly=True)
    try:
        panel = load_panel(conn, sector, ccfg)
    finally:
        conn.close()
    train_winters = {w for (_, _, w) in panel.y if w < holdout}
    x, y, meta = training_rows(panel, train_winters)
    n_in = x.shape[1]
    out_dir = models_dir(db_path)
    out_dir.mkdir(exist_ok=True)
    info: dict[str, Any] = {"rows": {}, "features": panel.feature_names}

    clients = {}
    for f in sorted(sector["formations"]):
        mask = (meta["formation"] == f).to_numpy()
        clients[f] = (x[mask], y[mask])
        info["rows"][f] = int(mask.sum())
        m = qm.train(qm.new_model(n_in, seed), x[mask], y[mask], epochs=EPOCHS, seed=seed)
        qm.save(out_dir / f"local_{f}.npz", qm.weights(m))

    central = qm.train(qm.new_model(n_in, seed), x, y, epochs=EPOCHS, seed=seed)
    qm.save(out_dir / "central.npz", qm.weights(central))

    fed_w, history = fedavg(clients, n_in, ROUNDS, LOCAL_EPOCHS, seed)
    qm.save(out_dir / "federated.npz", fed_w)
    info["federated_rounds"] = history

    for q, name in zip(qm.QUANTILES, ("p10", "p50", "p90"), strict=True):
        booster = lgb.train(
            {
                "objective": "quantile",
                "alpha": q,
                "learning_rate": 0.05,
                "num_leaves": 31,
                "min_data_in_leaf": 50,
                "seed": seed,
                "deterministic": True,
                "force_row_wise": True,
                "num_threads": 1,
                "verbose": -1,
            },
            lgb.Dataset(x, y),
            num_boost_round=300,
        )
        booster.save_model(str(out_dir / f"lightgbm_{name}.txt"))

    base = x[:, BASE_FEATURES.index("mean_30_58")]
    ratio = y / np.maximum(base, 1e-6)
    naive = {f"q{int(q * 100)}": float(np.quantile(ratio, q)) for q in qm.QUANTILES}
    (out_dir / "naive.json").write_text(json.dumps(naive))
    info["seconds"] = round(time.perf_counter() - t0, 1)
    (out_dir / "info.json").write_text(json.dumps(info, indent=2))
    return info


def load_models(db_path: Path) -> dict[str, Any]:
    """name -> callable(x) -> (n, 3) predictions in target units."""
    d = models_dir(db_path)
    out: dict[str, Any] = {}
    for p in sorted(d.glob("*.npz")):
        w = qm.load(p)
        out[p.stem] = lambda xx, w=w: qm.predict(w, xx)
    boosters = [lgb.Booster(model_file=str(d / f"lightgbm_{n}.txt")) for n in ("p10", "p50", "p90")]
    out["lightgbm"] = lambda xx: lgb_predict(boosters, xx)
    naive = json.loads((d / "naive.json").read_text())
    out["naive"] = lambda xx: naive_predict(naive, xx)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=get_settings().db_path)
    args = parser.parse_args()
    info = train_all(args.db, get_settings().seed)
    print(f"trained in {info['seconds']} s; rows per formation: {info['rows']}")


if __name__ == "__main__":
    main()
