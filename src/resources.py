"""
NetWatch – System resource sampling.

Captures CPU/RAM/load/temperature snapshots so that, after the fact, you
can tell whether the host itself was under load when an event fired —
distinguishing "the Pi was busy and a ping timed out" from "the network
was genuinely down". This is the key piece of evidence requested when the
ISP/local classification needs an independent sanity check.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:  # pragma: no cover - psutil should always be installed
    _HAS_PSUTIL = False
    logger.warning("psutil not installed – system resource sampling disabled")


@dataclass
class ResourceSnapshot:
    cpu_percent: Optional[float] = None
    ram_percent: Optional[float] = None
    ram_used_mb: Optional[float] = None
    load_avg_1m: Optional[float] = None
    load_avg_5m: Optional[float] = None
    load_avg_15m: Optional[float] = None
    cpu_temp_celsius: Optional[float] = None


def _read_cpu_temp() -> Optional[float]:
    """
    Read CPU temperature. Works on Raspberry Pi OS / most Linux SBCs via
    /sys/class/thermal. Falls back to psutil.sensors_temperatures() where
    available (not on Pi, but harmless to try).
    """
    thermal_path = "/sys/class/thermal/thermal_zone0/temp"
    try:
        if os.path.exists(thermal_path):
            with open(thermal_path) as fh:
                millidegrees = int(fh.read().strip())
                return round(millidegrees / 1000.0, 1)
    except Exception:
        pass

    if _HAS_PSUTIL:
        try:
            temps = psutil.sensors_temperatures()
            for entries in temps.values():
                if entries:
                    return round(entries[0].current, 1)
        except Exception:
            pass

    return None


def sample_resources(cpu_percent_interval: float = 0.0) -> ResourceSnapshot:
    """
    Take a single snapshot of current system resource usage.

    cpu_percent_interval=0.0 returns the CPU usage since the last call
    (non-blocking) rather than blocking to sample over an interval — this
    matters because we call this from the monitoring loop and must not
    add latency to every cycle.
    """
    if not _HAS_PSUTIL:
        return ResourceSnapshot()

    try:
        cpu_percent = psutil.cpu_percent(interval=cpu_percent_interval)
    except Exception:
        cpu_percent = None

    try:
        mem = psutil.virtual_memory()
        ram_percent = mem.percent
        ram_used_mb = round(mem.used / (1024 * 1024), 1)
    except Exception:
        ram_percent = None
        ram_used_mb = None

    try:
        load1, load5, load15 = os.getloadavg()
    except (OSError, AttributeError):
        load1 = load5 = load15 = None

    cpu_temp = _read_cpu_temp()

    return ResourceSnapshot(
        cpu_percent=cpu_percent,
        ram_percent=ram_percent,
        ram_used_mb=ram_used_mb,
        load_avg_1m=load1,
        load_avg_5m=load5,
        load_avg_15m=load15,
        cpu_temp_celsius=cpu_temp,
    )
