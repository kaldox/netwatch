"""Unit tests for src/statistics.py"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.statistics import (
    availability_sla_label,
    compute_daily_stats,
    compute_monthly_stats,
    format_duration,
)


def _make_event(
    event_type: str = "ISP_FAILURE",
    started_at: str = "2024-01-15T10:00:00+00:00",
    ended_at: str | None = "2024-01-15T10:30:00+00:00",
    duration_seconds: float | None = 1800.0,
) -> dict:
    return {
        "event_type": event_type,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": duration_seconds,
    }


def _make_measurement(latency_ms: float = 15.0, loss: float = 0.0) -> dict:
    return {
        "timestamp": "2024-01-15T10:00:00",
        "latency_ms": latency_ms,
        "packet_loss_percent": loss,
    }


class TestFormatDuration:
    def test_seconds(self):
        assert format_duration(45) == "45s"

    def test_minutes(self):
        result = format_duration(125)
        assert "m" in result and "s" in result

    def test_hours(self):
        result = format_duration(7320)
        assert "h" in result


class TestAvailabilityLabel:
    def test_four_nines(self):
        label = availability_sla_label(99.995)
        assert "Four Nines" in label

    def test_three_nines(self):
        label = availability_sla_label(99.95)
        assert "Three Nines" in label

    def test_two_nines(self):
        label = availability_sla_label(99.5)
        assert "Two Nines" in label

    def test_below_99(self):
        label = availability_sla_label(97.0)
        assert "97.00%" in label


class TestDailyStats:
    def test_no_events(self):
        stats = compute_daily_stats([], [], date(2024, 1, 15))
        assert stats.availability_percent == 100.0
        assert stats.outage_count == 0
        assert stats.downtime_seconds == 0.0

    def test_single_isp_event(self):
        events = [_make_event("ISP_FAILURE", duration_seconds=3600)]
        stats = compute_daily_stats(events, [], date(2024, 1, 15))
        assert stats.outage_count == 1
        assert stats.isp_failure_count == 1
        assert stats.local_failure_count == 0
        assert stats.downtime_seconds == 3600.0
        assert stats.availability_percent < 100.0

    def test_latency_stats(self):
        measurements = [_make_measurement(latency_ms=m) for m in [10, 20, 30, 200]]
        stats = compute_daily_stats([], measurements, date(2024, 1, 15))
        assert stats.avg_latency_ms == 65.0
        assert stats.max_latency_ms == 200.0

    def test_max_downtime_clamp(self):
        # 30 hours of outage should be clamped to 24h
        events = [_make_event(duration_seconds=108000)]
        stats = compute_daily_stats(events, [], date(2024, 1, 15))
        assert stats.downtime_seconds <= 86400.0
        assert stats.availability_percent >= 0.0

    def test_event_type_counts(self):
        events = [
            _make_event("ISP_FAILURE"),
            _make_event("DNS_FAILURE"),
            _make_event("LOCAL_NETWORK_FAILURE"),
            _make_event("PACKET_LOSS"),
            _make_event("LATENCY_DEGRADATION"),
        ]
        stats = compute_daily_stats(events, [], date(2024, 1, 15))
        assert stats.isp_failure_count == 1
        assert stats.dns_failure_count == 1
        assert stats.local_failure_count == 1
        assert stats.packet_loss_events == 1
        assert stats.latency_events == 1


class TestMonthlyStats:
    def _make_daily(
        self,
        date_str: str,
        avail: float = 99.9,
        downtime: float = 86.4,
        outages: int = 1,
        isp: int = 1,
    ) -> dict:
        return {
            "date_str": date_str,
            "availability_percent": avail,
            "downtime_seconds": downtime,
            "outage_count": outages,
            "isp_failure_count": isp,
            "local_failure_count": 0,
            "dns_failure_count": 0,
            "packet_loss_events": 0,
            "latency_events": 0,
            "avg_latency_ms": 15.0,
            "max_latency_ms": 100.0,
            "avg_packet_loss_percent": 0.0,
            "longest_outage_seconds": downtime,
        }

    def test_empty(self):
        stats = compute_monthly_stats([], 2024, 1)
        assert stats.availability_percent == 100.0
        assert stats.outage_count == 0

    def test_aggregation(self):
        daily = [self._make_daily(f"2024-01-{i:02d}") for i in range(1, 4)]
        stats = compute_monthly_stats(daily, 2024, 1)
        assert stats.outage_count == 3
        assert stats.isp_failure_count == 3
        assert stats.days_with_outages == 3

    def test_availability_below_100(self):
        daily = [self._make_daily("2024-01-01", avail=99.0, downtime=864.0)]
        stats = compute_monthly_stats(daily, 2024, 1)
        assert stats.availability_percent < 100.0
