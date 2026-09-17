"""Rolling / lag feature construction over observation history.

Two rules bind everything in this module:

1. **Time-based, not index-based.** A lag of "one slot" means "the sample
   nearest ``now - lag``, found by timestamp" — never ``series[-index]``.
   With a gap in the stream (a dropped sensor reading, a cold start), an
   index-based lag silently returns a value from the wrong instant, which
   is the error that looks like nothing until a model trained on it is
   wrong in production.
2. **Strictly before ``now``.** A sample stamped at ``now`` describes the
   interval just *beginning* (see ``observed_demand_series``'s
   interval-start convention), not what already happened. Including it in
   a lag or rolling window meant to describe the past is target leakage —
   the feature would carry information the scheduler cannot actually have
   at decision time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from surya_sync.domain import TimedValue


def value_at_lag(
    series: tuple[TimedValue, ...],
    now: datetime,
    lag_minutes: float,
    tolerance_minutes: float,
) -> float | None:
    """The value nearest ``now - lag_minutes``, within ``tolerance_minutes``.

    ``None`` if nothing in ``series`` falls that close — reporting a
    distant sample as if it were on-time would be worse than reporting
    unknown. Excludes anything at or after ``now``: ``series`` is expected
    to carry interval-*start*-stamped values (see
    ``models.tank.observed_demand_series``), so a sample at ``now``
    describes an interval only just beginning, not something already
    known.
    """
    if lag_minutes < 0.0:
        raise ValueError("lag_minutes must be >= 0")
    return _nearest(series, now - timedelta(minutes=lag_minutes), tolerance_minutes, before=now)


def value_at(
    series: tuple[TimedValue, ...],
    now: datetime,
    tolerance_minutes: float,
) -> float | None:
    """The value nearest ``now``, within ``tolerance_minutes``, from
    samples at or before ``now``.

    For state *snapshots* (e.g. ``service_level``), not interval-start
    values — the reading taken at ``now`` is legitimately known at
    decision time, unlike an interval-start demand sample, so it is
    included rather than excluded. Use ``value_at_lag`` for the latter.
    """
    return _nearest(series, now, tolerance_minutes, before=now + timedelta(microseconds=1))


def _nearest(
    series: tuple[TimedValue, ...],
    target: datetime,
    tolerance_minutes: float,
    before: datetime,
) -> float | None:
    if tolerance_minutes < 0.0:
        raise ValueError("tolerance_minutes must be >= 0")

    window = timedelta(minutes=tolerance_minutes)
    lower, upper = target - window, target + window

    best: TimedValue | None = None
    best_gap: timedelta | None = None
    for sample in series:
        if sample.at >= before:
            continue
        if not (lower <= sample.at <= upper):
            continue
        gap = abs(sample.at - target)
        if best_gap is None or gap < best_gap:
            best, best_gap = sample, gap

    return None if best is None else best.value


@dataclass(frozen=True, slots=True)
class RollingStats:
    mean: float
    max: float
    count: int
    window_minutes: float


def rolling_stats(
    series: tuple[TimedValue, ...], now: datetime, window_minutes: float
) -> RollingStats | None:
    """Mean/max over ``[now - window_minutes, now)`` — inclusive of the far
    edge, exclusive of ``now`` itself.

    Regression: an earlier version excluded both edges, so on the ordinary
    fixed-cadence control loop (samples every ``step_minutes``, a window
    that is a whole multiple of it) the oldest sample landed exactly on
    ``now - window_minutes`` and was silently dropped on *every* call —
    a "1h rolling mean" reporting 3 samples instead of 4 at a 15-minute
    step, not a rare edge case but the normal one.
    ``test_rolling_window_includes_the_sample_exactly_at_its_far_edge``
    pins this.

    ``None`` when the window contains no samples — an empty window
    averaged as zero would look like measured evidence of no demand,
    rather than the absence of any measurement at all.
    """
    if window_minutes <= 0.0:
        raise ValueError("window_minutes must be > 0")

    start = now - timedelta(minutes=window_minutes)
    values = [s.value for s in series if start <= s.at < now]
    if not values:
        return None

    return RollingStats(
        mean=sum(values) / len(values),
        max=max(values),
        count=len(values),
        window_minutes=window_minutes,
    )
