"""Chronological validation and error metrics, shared across ML targets.

Hard rule: time-series validation is always walk-forward, never shuffled.
This lives at ``ml/`` top level, not inside ``ml/demand/``, because Phase 6
(solar) needs the exact same split and the exact same metrics — duplicating
either would let the two targets drift into disagreeing definitions of
"the test set" or "the error."
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def chronological_split(
    items: Sequence[T], train_frac: float = 0.7, val_frac: float = 0.15
) -> tuple[tuple[T, ...], tuple[T, ...], tuple[T, ...]]:
    """Split a chronological sequence into train/val/test by position.

    Never shuffles: the first ``train_frac`` of ``items`` is train, the next
    ``val_frac`` is validation, and everything after that is test. ``items``
    must already be in chronological order; this does not sort.
    """
    if not 0.0 < train_frac < 1.0:
        raise ValueError(f"train_frac must be in (0, 1), got {train_frac}")
    if not 0.0 <= val_frac < 1.0:
        raise ValueError(f"val_frac must be in [0, 1), got {val_frac}")
    if train_frac + val_frac >= 1.0:
        raise ValueError(
            f"train_frac + val_frac must be < 1.0, got {train_frac + val_frac}"
        )

    n = len(items)
    train_end = int(n * train_frac)
    val_end = train_end + int(n * val_frac)
    return tuple(items[:train_end]), tuple(items[train_end:val_end]), tuple(items[val_end:])


def mean_absolute_error(y_true: Sequence[float], y_pred: Sequence[float]) -> float:
    if len(y_true) != len(y_pred):
        raise ValueError(f"{len(y_true)} true values for {len(y_pred)} predictions")
    if not y_true:
        raise ValueError("cannot score an empty set")
    return sum(abs(t - p) for t, p in zip(y_true, y_pred)) / len(y_true)


def root_mean_squared_error(y_true: Sequence[float], y_pred: Sequence[float]) -> float:
    if len(y_true) != len(y_pred):
        raise ValueError(f"{len(y_true)} true values for {len(y_pred)} predictions")
    if not y_true:
        raise ValueError("cannot score an empty set")
    return math.sqrt(sum((t - p) ** 2 for t, p in zip(y_true, y_pred)) / len(y_true))
