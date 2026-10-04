from __future__ import annotations

import sqlite3
from datetime import date

import numpy as np
import pytest
import torch

from config.loader import load_consumption, load_sector
from forecast import model as qm
from forecast.features import forecast_rows, load_panel, training_rows
from forecast.federated import fedavg


@pytest.fixture(scope="module")
def panel(built):
    conn = sqlite3.connect(f"file:{built['db']}?mode=ro", uri=True)
    try:
        return load_panel(conn, load_sector(), load_consumption())
    finally:
        conn.close()


def test_numpy_inference_matches_torch():
    rng = np.random.default_rng(0)
    x = rng.normal(size=(50, 15))
    m = qm.new_model(15, seed=1)
    with torch.no_grad():
        want = m(torch.tensor(x, dtype=torch.float32)).numpy()
    assert np.allclose(qm.predict(qm.weights(m), x), want, atol=1e-5)


def test_quantiles_never_cross():
    x = np.random.default_rng(1).normal(size=(500, 15)) * 5
    p = qm.predict(qm.weights(qm.new_model(15, seed=2)), x)
    assert (p[:, 0] <= p[:, 1]).all() and (p[:, 1] <= p[:, 2]).all()


def test_training_reduces_pinball_and_is_deterministic():
    rng = np.random.default_rng(3)
    x = rng.normal(size=(800, 4))
    y = 1 + 0.5 * x[:, 0] + rng.normal(scale=0.1, size=800)
    before = qm.pinball_np(qm.predict(qm.weights(qm.new_model(4, 0)), x), y).mean()
    a = qm.weights(qm.train(qm.new_model(4, 0), x, y, epochs=10, seed=0))
    b = qm.weights(qm.train(qm.new_model(4, 0), x, y, epochs=10, seed=0))
    assert qm.pinball_np(qm.predict(a, x), y).mean() < before / 2
    assert all(np.array_equal(a[k], b[k]) for k in a)


def test_fedavg_weights_clients_by_size():
    rng = np.random.default_rng(4)
    clients = {
        "big": (rng.normal(size=(300, 3)), rng.normal(size=300)),
        "small": (rng.normal(size=(30, 3)), rng.normal(size=30)),
    }
    w, history = fedavg(clients, n_in=3, rounds=2, local_epochs=1, seed=0)
    assert len(history) == 2 and set(history[0]) == {"big", "small"}
    assert set(w) == set(qm.weights(qm.new_model(3, 0)))


def test_forecast_features_never_look_past_the_origin(panel):
    """Corrupt every day after the origin; the features must not change."""
    post, cls, origin = "HANLE-03", "fuel", date(2025, 11, 8)
    x1, days, _ = forecast_rows(panel, post, cls, origin)
    key = (post, cls, 2025)
    saved = panel.y[key].copy()
    idx0 = (origin - panel.season(2025)[0]).days
    panel.y[key][idx0 + 1 :] = 999.0
    try:
        x2, _, _ = forecast_rows(panel, post, cls, origin)
    finally:
        panel.y[key] = saved
    assert len(days) == 30 and np.array_equal(x1, x2)


def test_f3_has_one_training_winter(panel):
    _, _, meta = training_rows(panel, {2021, 2022, 2023, 2024})
    winters = meta.groupby("formation")["winter"].nunique().to_dict()
    assert winters == {"F1": 4, "F2": 4, "F3": 1}
