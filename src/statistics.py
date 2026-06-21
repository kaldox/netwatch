"""
NetWatch – Statistics engine.
Computes daily and monthly availability and performance statistics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from statistics import mean, stdev
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class DailyStats:
    date_str: str
    availability_percent: float
    downtime_seconds: float
    outage_count: int
    isp_failure_count: int
    local_failure_count: int
    dns_failure_count: int
    packet_loss_events: int
    latency_events: int
    avg_latency_ms: Optional[float]
    max_latency_ms: Optional[float]
    avg_packet_loss_percent: Optional[float]
    longest_outage_seconds: float


@dataclass
class MonthlyStats:
    year: int
    month: int
    availability_percent: float
    total_downtime_seconds: float
    outage_count: int
    isp_failure_count: int
    local_failure_count: int
    dns_failure_count: int
    avg_latency_ms: Optional[float]
    max_latency_ms: Optional[float]
    longest_outage_seconds: float
    days_with_outages: int


# ---------------------------------------------------------------------------
# Computation helpers
# ---------------------------------------------------------------------------


SECONDS_PER_DAY = 86_400.0


def compute_daily_stats(
    events: list[dict[str, Any]],
    measurements: list[dict[str, Any]],
    target_date: date,
) -> DailyStats:
    """
    Compute statistics for a single calendar day.

    :param events: All events that started within the target day.
    :param measurements: All measurements taken during the target day.
    :param target_date: The date to compute stats for.
    """
    date_str = target_date.isoformat()

    # ---- Downtime / availability ----
    outage_types = {
        "LOCAL_NETWORK_FAILURE",
        "ISP_FAILURE",
        "ROUTING_FAILURE",
        "DNS_FAILURE",
    }
    outage_events = [e for e in events if e["event_type"] in outage_types]
    outage_count = len(outage_events)

    total_downtime = 0.0
    longest_outage = 0.0

    for ev in outage_events:
        duration = ev.get("duration_seconds") or 0.0
        # If event is still open, count up to end of day
        if ev.get("ended_at") is None:
            try:
                started = datetime.fromisoformat(ev["started_at"])
                end_of_day = datetime(
                    target_date.year, target_date.month, target_date.day,
                    23, 59, 59, tzinfo=timezone.utc,
                )
                duration = (end_of_day - started).total_seconds()
                duration = max(0.0, duration)
            except Exception:
                duration = 0.0

        total_downtime += duration
        longest_outage = max(longest_outage, duration)

    # Clamp to 24 hours
    total_downtime = min(total_downtime, SECONDS_PER_DAY)
    availability_percent = (
        (SECONDS_PER_DAY - total_downtime) / SECONDS_PER_DAY * 100.0
    )

    # ---- Event type counts ----
    isp_failure_count = sum(1 for e in events if e["event_type"] == "ISP_FAILURE")
    local_failure_count = sum(1 for e in events if e["event_type"] == "LOCAL_NETWORK_FAILURE")
    dns_failure_count = sum(1 for e in events if e["event_type"] == "DNS_FAILURE")
    packet_loss_events = sum(1 for e in events if e["event_type"] == "PACKET_LOSS")
    latency_events = sum(1 for e in events if e["event_type"] == "LATENCY_DEGRADATION")

    # ---- Latency stats ----
    latencies = [
        m["latency_ms"]
        for m in measurements
        if m.get("latency_ms") is not None and m["latency_ms"] > 0
    ]
    avg_latency = mean(latencies) if latencies else None
    max_latency = max(latencies) if latencies else None

    # ---- Packet loss stats ----
    losses = [
        m["packet_loss_percent"]
        for m in measurements
        if m.get("packet_loss_percent") is not None
    ]
    avg_loss = mean(losses) if losses else None

    return DailyStats(
        date_str=date_str,
        availability_percent=round(availability_percent, 4),
        downtime_seconds=round(total_downtime, 1),
        outage_count=outage_count,
        isp_failure_count=isp_failure_count,
        local_failure_count=local_failure_count,
        dns_failure_count=dns_failure_count,
        packet_loss_events=packet_loss_events,
        latency_events=latency_events,
        avg_latency_ms=round(avg_latency, 2) if avg_latency is not None else None,
        max_latency_ms=round(max_latency, 2) if max_latency is not None else None,
        avg_packet_loss_percent=round(avg_loss, 2) if avg_loss is not None else None,
        longest_outage_seconds=round(longest_outage, 1),
    )


def compute_monthly_stats(
    daily_stats: list[dict[str, Any]],
    year: int,
    month: int,
) -> MonthlyStats:
    """
    Aggregate daily statistics into a monthly summary.
    """
    if not daily_stats:
        return MonthlyStats(
            year=year, month=month,
            availability_percent=100.0,
            total_downtime_seconds=0.0,
            outage_count=0, isp_failure_count=0, local_failure_count=0,
            dns_failure_count=0,
            avg_latency_ms=None, max_latency_ms=None,
            longest_outage_seconds=0.0,
            days_with_outages=0,
        )

    days_in_month = len(daily_stats)
    total_downtime = sum(d["downtime_seconds"] for d in daily_stats)
    total_seconds = days_in_month * SECONDS_PER_DAY
    availability = (total_seconds - total_downtime) / total_seconds * 100.0

    outage_count = sum(d["outage_count"] for d in daily_stats)
    isp_count = sum(d["isp_failure_count"] for d in daily_stats)
    local_count = sum(d["local_failure_count"] for d in daily_stats)
    dns_count = sum(d["dns_failure_count"] for d in daily_stats)

    latencies = [
        d["avg_latency_ms"] for d in daily_stats if d.get("avg_latency_ms") is not None
    ]
    max_latencies = [
        d["max_latency_ms"] for d in daily_stats if d.get("max_latency_ms") is not None
    ]
    avg_latency = mean(latencies) if latencies else None
    max_latency = max(max_latencies) if max_latencies else None

    longest_outage = max(
        (d["longest_outage_seconds"] for d in daily_stats), default=0.0
    )

    days_with_outages = sum(1 for d in daily_stats if d["outage_count"] > 0)

    return MonthlyStats(
        year=year,
        month=month,
        availability_percent=round(availability, 4),
        total_downtime_seconds=round(total_downtime, 1),
        outage_count=outage_count,
        isp_failure_count=isp_count,
        local_failure_count=local_count,
        dns_failure_count=dns_count,
        avg_latency_ms=round(avg_latency, 2) if avg_latency is not None else None,
        max_latency_ms=round(max_latency, 2) if max_latency is not None else None,
        longest_outage_seconds=round(longest_outage, 1),
        days_with_outages=days_with_outages,
    )


def format_duration(seconds: float) -> str:
    """Format seconds into a human-readable duration string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}m {s}s"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m}m"


def availability_sla_label(availability_percent: float) -> str:
    """Return SLA label for a given availability percentage."""
    if availability_percent >= 99.99:
        return "Four Nines (≥99.99%)"
    elif availability_percent >= 99.9:
        return "Three Nines (≥99.9%)"
    elif availability_percent >= 99.0:
        return "Two Nines (≥99%)"
    elif availability_percent >= 95.0:
        return f"{availability_percent:.2f}% (≥95%)"
    else:
        return f"{availability_percent:.2f}%"
