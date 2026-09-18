"""Chronological split and error metrics — shared by every ML target."""

from __future__ import annotations

import pytest

from surya_sync.ml.evaluation import (
    chronological_split,
    mean_absolute_error,
    root_mean_squared_error,
)


def test_split_preserves_order_and_never_shuffles():
    items = list(range(100))
    train, val, test = chronological_split(items, train_frac=0.7, val_frac=0.15)
    assert train == tuple(range(0, 70))
    assert val == tuple(range(70, 85))
    assert test == tuple(range(85, 100))


def test_split_covers_every_item_exactly_once():
    items = list(range(37))
    train, val, test = chronological_split(items, train_frac=0.6, val_frac=0.2)
    assert train + val + test == tuple(items)


@pytest.mark.parametrize("train_frac,val_frac", [(0.0, 0.1), (1.0, 0.0), (0.7, 0.3 + 1e-9)])
def test_invalid_fractions_are_rejected(train_frac, val_frac):
    with pytest.raises(ValueError):
        chronological_split(list(range(10)), train_frac, val_frac)


def test_mae_is_average_absolute_error():
    assert mean_absolute_error([1.0, 2.0, 3.0], [1.0, 4.0, 3.0]) == pytest.approx(2.0 / 3.0)


def test_rmse_penalizes_large_errors_more_than_mae():
    y_true = [0.0, 0.0, 0.0, 0.0]
    y_pred = [1.0, 1.0, 1.0, 10.0]
    mae = mean_absolute_error(y_true, y_pred)
    rmse = root_mean_squared_error(y_true, y_pred)
    assert rmse > mae


def test_mismatched_lengths_raise():
    with pytest.raises(ValueError):
        mean_absolute_error([1.0, 2.0], [1.0])
    with pytest.raises(ValueError):
        root_mean_squared_error([1.0, 2.0], [1.0])


def test_empty_inputs_raise_rather_than_return_nan():
    with pytest.raises(ValueError):
        mean_absolute_error([], [])
    with pytest.raises(ValueError):
        root_mean_squared_error([], [])
