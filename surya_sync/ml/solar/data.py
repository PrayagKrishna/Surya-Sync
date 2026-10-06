"""Load hourly PV generation from a PVGIS ``seriescalc`` CSV.

Why this exists: the simulator's cloud factor is independent from one
20-minute slot to the next, so there is nothing for a forecaster to learn
beyond the clear-sky curve — every method would tie, for an artifact
reason (the same trap Phase 5 hit with demand). Real weather has memory,
so Phase 6 is scored on real weather instead.

What this data is, and is not: PVGIS-ERA5 is an atmospheric *reanalysis*
pushed through a PV model for a nominal array. It is not a meter on a
rooftop, so every value is stamped ``Provenance.ESTIMATED``, never
``MEASURED``. It is hourly, one location, one nominal system. Phase 13's
real inverter readings are the check on all of this.

Source: PVGIS (c) European Union, 2001-2026. Free to use with attribution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from surya_sync.config.schema import SolarConfig
from surya_sync.domain import Provenance, TimedValue

DATA_VERSION = "pvgis-era5-v5.3"


@dataclass(frozen=True, slots=True)
class PvgisMeta:
    """The system the CSV was generated for, read from its own header."""

    latitude: float
    longitude: float
    peak_power_kw: float
    database: str


def _header_number(text: str, label: str) -> float:
    found = re.search(rf"{re.escape(label)}[^\t\n]*:\t\s*([-0-9.]+)", text)
    if found is None:
        raise ValueError(f"PVGIS header has no {label!r} line")
    return float(found.group(1))


def load_pvgis_hourly(
    path: str | Path, utc_offset_hours: float = 5.5
) -> tuple[PvgisMeta, tuple[TimedValue, ...]]:
    """Read ``P`` (W) as kW, with timestamps shifted from UTC to local time.

    Local, naive timestamps because that is what ``ClearSkyProfile`` and
    ``temporal.context`` use. PVGIS-ERA5 stamps hours at ``:30`` UTC, which
    lands on the hour at the default +5:30 offset; the clear-sky curve
    correlates best with this series at zero shift (r=0.913, vs 0.896 at
    -30 min and 0.905 at +30 min) so no half-hour correction is applied.

    Timestamp meaning (PVGIS manual: reanalysis values are the *average over
    the hour*, stamped at the hour's centre): each ``TimedValue.at`` is the
    **centre** of an hourly mean. A forecast "issued at" a stamp ``t`` is
    therefore issued at the *end* of the interval centred on ``t`` — the
    moment that mean is complete — and lead ``L`` means the ``L``-th hourly
    interval after it. Read this way no feature sees the future. Reading
    ``t`` as an instantaneous issue time would leak ~30 minutes.

    Raises on a missing or malformed header, on an empty body, and on
    timestamps that are not strictly increasing — every downstream split
    assumes chronological order and does not sort.
    """
    text = Path(path).read_text(encoding="utf-8")
    meta = PvgisMeta(
        latitude=_header_number(text, "Latitude"),
        longitude=_header_number(text, "Longitude"),
        peak_power_kw=_header_number(text, "Nominal power"),
        database=(re.search(r"Radiation database:\t(\S+)", text) or [None, "unknown"])[1],
    )

    shift = timedelta(hours=utc_offset_hours)
    series: list[TimedValue] = []
    for line in text.splitlines():
        # Data rows start with a date like 20200101:0030; header/footer do not.
        if not re.match(r"^\d{8}:\d{4},", line):
            continue
        stamp, power_w = line.split(",")[:2]
        series.append(
            TimedValue(
                at=datetime.strptime(stamp, "%Y%m%d:%H%M") + shift,
                value=float(power_w) / 1000.0,
                provenance=Provenance.ESTIMATED,
            )
        )

    if not series:
        raise ValueError(f"{path} contains no PVGIS data rows")
    if any(later.at <= earlier.at for earlier, later in zip(series, series[1:])):
        raise ValueError(f"{path} timestamps are not strictly increasing")
    return meta, tuple(series)


def require_matches_config(meta: PvgisMeta, solar: SolarConfig) -> None:
    """Refuse data generated for a different site or array than the config.

    The clear-sky curve comes from ``config.solar``, so a mismatch makes
    every clear-sky index wrong without any error: a 5 kWp config against
    3 kWp data, or a different city, would train and score cleanly on
    nonsense.
    """
    problems = []
    if abs(meta.latitude - solar.latitude) > 0.01 or abs(meta.longitude - solar.longitude) > 0.01:
        problems.append(
            f"data is for ({meta.latitude}, {meta.longitude}), "
            f"config.solar is ({solar.latitude}, {solar.longitude})"
        )
    if abs(meta.peak_power_kw - solar.pv_capacity_kw) > 1e-6:
        problems.append(
            f"data is for {meta.peak_power_kw} kWp, config.solar.pv_capacity_kw is "
            f"{solar.pv_capacity_kw}"
        )
    if problems:
        raise ValueError("PVGIS data does not match config: " + "; ".join(problems))
