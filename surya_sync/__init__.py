"""SuryaSync — solar-aware residential flexible-load scheduling.

Learns temporal household behaviour, forecasts service demand and rooftop
solar availability, quantifies the flexibility of controllable loads, and
schedules them with uncertainty-aware MPC.

The relay is not the innovation. The decision about when the relay should
operate is the innovation.
"""

from surya_sync.version import BACKEND_VERSION

__version__ = BACKEND_VERSION
__all__ = ["__version__"]
