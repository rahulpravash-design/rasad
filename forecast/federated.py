"""Federated averaging (FedAvg) across formations.

Each formation is a client. In every round each client starts from the global weights, trains on
its own reports for a few epochs, and sends back only its weights; the server averages them,
weighted by how many rows each client trained on. Raw reports never leave a client.

Hand-written instead of Flower (the plan's stated fallback): it is the same algorithm, run as a
simulation in one process.
"""

from __future__ import annotations

import numpy as np

from forecast import model as qm


def fedavg(
    clients: dict[str, tuple[np.ndarray, np.ndarray]],
    n_in: int,
    rounds: int,
    local_epochs: int,
    seed: int,
) -> tuple[dict[str, np.ndarray], list[dict[str, float]]]:
    """Return the global weights and per-round training loss per client."""
    global_w = qm.weights(qm.new_model(n_in, seed))
    history = []
    names = sorted(clients)
    for r in range(rounds):
        updates, sizes, losses = [], [], {}
        for i, name in enumerate(names):
            x, y = clients[name]
            local = qm.load_weights(qm.new_model(n_in, seed), global_w)
            qm.train(local, x, y, epochs=local_epochs, seed=seed + 1000 * r + i)
            w = qm.weights(local)
            updates.append(w)
            sizes.append(len(x))
            losses[name] = float(np.mean(qm.pinball_np(qm.predict(w, x), y)))
        total = float(sum(sizes))
        global_w = {
            k: sum(u[k] * (n / total) for u, n in zip(updates, sizes, strict=True))
            for k in global_w
        }
        history.append(losses)
    return global_w, history
