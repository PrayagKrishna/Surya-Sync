"""Reproducible pseudo-randomness for the synthetic profiles.

The simulator's profiles must be **pure functions of time**. The MPC will
query the same future instant many times across successive replans, and a
replayed experiment must reproduce its original trajectory exactly. A
seeded ``random.Random`` stream cannot do that: its output depends on how
many times it has been called, so querying out of order changes the answer.

``unit_noise`` is therefore an integer hash, not a stream. The same
``(seed, index)`` always yields the same value, in any order, in any
process, on any platform — it uses only fixed-width integer arithmetic.
"""

from __future__ import annotations

_MASK = 0xFFFFFFFF


def unit_noise(seed: int, index: int) -> float:
    """A deterministic value in ``[0, 1)`` derived from two integers.

    Mixing is the MurmurHash3 finalizer, which is cheap and spreads
    neighbouring indices well enough that consecutive time slots do not
    correlate.
    """
    value = ((seed & _MASK) * 0x9E3779B1 ^ (index & _MASK) * 0x85EBCA6B) & _MASK
    value ^= value >> 16
    value = (value * 0x85EBCA6B) & _MASK
    value ^= value >> 13
    value = (value * 0xC2B2AE35) & _MASK
    value ^= value >> 16
    return value / 4294967296.0


def signed_noise(seed: int, index: int) -> float:
    """A deterministic value in ``[-1, 1)``."""
    return 2.0 * unit_noise(seed, index) - 1.0
