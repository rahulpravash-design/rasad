"""Quantile MLP: P10, P50 and P90 of next-month demand, trained with pinball loss.

Training uses PyTorch; inference is plain NumPy (`predict`), so the API serves forecasts without
importing torch. The head cannot cross quantiles: it predicts P50 and two non-negative gaps.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import numpy as np
import torch
from torch import nn

QUANTILES = (0.1, 0.5, 0.9)
HIDDEN = (64, 64)

torch.set_num_threads(1)  # one thread keeps CPU training bit-for-bit reproducible


class QuantileMLP(nn.Module):
    def __init__(self, n_in: int, hidden: tuple[int, ...] = HIDDEN) -> None:
        super().__init__()
        layers: list[nn.Module] = []
        width = n_in
        for h in hidden:
            layers += [nn.Linear(width, h), nn.ReLU()]
            width = h
        self.body = nn.Sequential(*layers)
        self.head = nn.Linear(width, 3)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.head(self.body(x))
        p50 = out[:, 1]
        return torch.stack(
            [p50 - nn.functional.softplus(out[:, 0]), p50, p50 + nn.functional.softplus(out[:, 2])],
            dim=1,
        )


def pinball(pred: torch.Tensor, y: torch.Tensor) -> torch.Tensor:
    q = torch.tensor(QUANTILES, dtype=pred.dtype)
    diff = y.unsqueeze(1) - pred
    return torch.maximum(q * diff, (q - 1) * diff).mean()


def new_model(n_in: int, seed: int) -> QuantileMLP:
    torch.manual_seed(seed)
    return QuantileMLP(n_in)


def train(
    model: QuantileMLP,
    x: np.ndarray,
    y: np.ndarray,
    epochs: int,
    seed: int,
    lr: float = 1e-3,
    batch: int = 256,
) -> QuantileMLP:
    """Mini-batch Adam on pinball loss, in place. Deterministic for a given seed."""
    xt = torch.tensor(x, dtype=torch.float32)
    yt = torch.tensor(y, dtype=torch.float32)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    gen = torch.Generator().manual_seed(seed)
    model.train()
    for _ in range(epochs):
        order = torch.randperm(len(xt), generator=gen)
        for start in range(0, len(xt), batch):
            sel = order[start : start + batch]
            opt.zero_grad()
            loss = pinball(model(xt[sel]), yt[sel])
            loss.backward()
            opt.step()
    model.eval()
    return model


def weights(model: QuantileMLP) -> dict[str, np.ndarray]:
    return {k: v.detach().cpu().numpy().copy() for k, v in model.state_dict().items()}


def load_weights(model: QuantileMLP, w: Mapping[str, np.ndarray]) -> QuantileMLP:
    model.load_state_dict({k: torch.tensor(v) for k, v in w.items()})
    return model


def _softplus(z: np.ndarray) -> np.ndarray:
    return np.logaddexp(0.0, z)


def predict(w: Mapping[str, np.ndarray], x: np.ndarray) -> np.ndarray:
    """NumPy forward pass: (n, 3) array of P10, P50, P90 in target units."""
    h = x.astype(np.float64)
    layer = 0
    while f"body.{layer}.weight" in w:
        h = np.maximum(0.0, h @ w[f"body.{layer}.weight"].T + w[f"body.{layer}.bias"])
        layer += 2  # Linear, ReLU
    out = h @ w["head.weight"].T + w["head.bias"]
    p50 = out[:, 1]
    return np.column_stack([p50 - _softplus(out[:, 0]), p50, p50 + _softplus(out[:, 2])])


def save(path: Path, w: Mapping[str, np.ndarray]) -> None:
    np.savez(path, **w)


def load(path: Path) -> dict[str, np.ndarray]:
    with np.load(path) as data:
        return {k: data[k] for k in data.files}


def pinball_np(pred: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Per-row mean pinball loss over the three quantiles."""
    q = np.array(QUANTILES)
    diff = y[:, None] - pred
    return np.maximum(q * diff, (q - 1) * diff).mean(axis=1)
