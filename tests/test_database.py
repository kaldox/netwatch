"""Integration tests for src/database.py"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database import (
    Database,
    DailyStatRow,
    EventRow,
    MeasurementRow,
    PublicIpRow,
    TracerouteRow,
)


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Database(Path(tmpdir) / "test.db")


def _make_measurement(target="Cloudflare DNS") -> MeasurementRow:
    return MeasurementRow(
        timestamp="2024-01-15T10:00:00+00:00",
        target_name=target,
        target_host="1.1.1.1",
        target_type="icmp",
        reachable=1,
        latency_ms=12.5,
        packet_loss_percent=0.0,
        jitter_ms=1.2,
        dns_resolution_ms=None,
        public_ipv4="203.0.113.10",
        public_ipv6=None,
        gateway_reachable=1,
        error_message=None,
    )


def _make_event(event_id="evt-001", event_type="ISP_FAILURE") -> EventRow:
    return EventRow(
        event_id=event_id,
        event_type=event_type,
        started_at="2024-01-15T10:00:00+00:00",
        ended_at=None,
        duration_seconds=None,
        confidence_score=0.90,
        description="Test event",
        public_ipv4_before="203.0.113.10",
        public_ipv4_during=None,
        public_ipv4_after=None,
        public_ipv6_before=None,
        public_ipv6_during=None,
        public_ipv6_after=None,
        gateway_ip="192.168.1.1",
        hostname="testhost",
        network_interface="eth0",
        extra_json='{"test": true}',
    )


class TestMeasurements:
    def test_insert_and_retrieve(self, db):
        m = _make_measurement()
        db.insert_measurement(m)
        rows = db.get_recent_measurements("Cloudflare DNS", limit=10)
        assert len(rows) == 1
        assert rows[0]["target_name"] == "Cloudflare DNS"
        assert rows[0]["latency_ms"] == 12.5

    def test_multiple_measurements(self, db):
        for i in range(5):
            m = _make_measurement(f"Target-{i}")
            db.insert_measurement(m)
        rows = db.get_recent_measurements("Target-0", limit=10)
        assert len(rows) == 1

    def test_range_query(self, db):
        db.insert_measurement(_make_measurement())
        rows = db.get_measurements_range(
            start="2024-01-15T09:00:00",
            end="2024-01-15T11:00:00",
        )
        assert len(rows) == 1

    def test_range_query_no_results(self, db):
        db.insert_measurement(_make_measurement())
        rows = db.get_measurements_range(
            start="2024-01-16T09:00:00",
            end="2024-01-16T11:00:00",
        )
        assert len(rows) == 0


class TestEvents:
    def test_insert_event(self, db):
        db.upsert_event(_make_event())
        events = db.get_events()
        assert len(events) == 1
        assert events[0]["event_id"] == "evt-001"
        assert events[0]["event_type"] == "ISP_FAILURE"

    def test_update_event(self, db):
        ev = _make_event()
        db.upsert_event(ev)
        ev.ended_at = "2024-01-15T10:30:00+00:00"
        ev.duration_seconds = 1800.0
        db.upsert_event(ev)
        events = db.get_events()
        assert len(events) == 1
        assert events[0]["ended_at"] is not None
        assert events[0]["duration_seconds"] == 1800.0

    def test_get_open_events(self, db):
        db.upsert_event(_make_event("open-1"))
        ev_closed = _make_event("closed-1")
        ev_closed.ended_at = "2024-01-15T10:30:00"
        db.upsert_event(ev_closed)
        open_events = db.get_open_events()
        assert len(open_events) == 1
        assert open_events[0]["event_id"] == "open-1"

    def test_filter_by_type(self, db):
        db.upsert_event(_make_event("e1", "ISP_FAILURE"))
        db.upsert_event(_make_event("e2", "DNS_FAILURE"))
        isp_only = db.get_events(event_type="ISP_FAILURE")
        assert all(e["event_type"] == "ISP_FAILURE" for e in isp_only)


class TestTraceroutes:
    def test_insert_and_retrieve(self, db):
        row = TracerouteRow(
            event_id="evt-001",
            timestamp="2024-01-15T10:00:00",
            target_host="1.1.1.1",
            tool="traceroute",
            output="traceroute output here",
            duration_seconds=2.5,
        )
        db.insert_traceroute(row)
        rows = db.get_traceroutes(event_id="evt-001")
        assert len(rows) == 1
        assert rows[0]["output"] == "traceroute output here"

    def test_retrieve_without_filter(self, db):
        for i in range(3):
            db.insert_traceroute(TracerouteRow(
                event_id=f"evt-{i}",
                timestamp="2024-01-15T10:00:00",
                target_host="1.1.1.1",
                tool="traceroute",
                output=f"output {i}",
                duration_seconds=1.0,
            ))
        rows = db.get_traceroutes()
        assert len(rows) == 3


class TestPublicIp:
    def test_insert_and_latest(self, db):
        db.insert_public_ip(PublicIpRow(
            timestamp="2024-01-15T10:00:00",
            ipv4="203.0.113.10",
            ipv6=None,
            changed=0,
        ))
        latest = db.get_latest_public_ip()
        assert latest["ipv4"] == "203.0.113.10"

    def test_history(self, db):
        for i in range(5):
            db.insert_public_ip(PublicIpRow(
                timestamp=f"2024-01-15T1{i}:00:00",
                ipv4=f"203.0.113.{i}",
                ipv6=None,
                changed=1 if i > 0 else 0,
            ))
        history = db.get_public_ip_history(limit=10)
        assert len(history) == 5


class TestDailyStats:
    def test_upsert_and_retrieve(self, db):
        row = DailyStatRow(
            date_str="2024-01-15",
            availability_percent=99.95,
            downtime_seconds=43.2,
            outage_count=1,
            isp_failure_count=1,
            local_failure_count=0,
            dns_failure_count=0,
            packet_loss_events=0,
            latency_events=0,
            avg_latency_ms=15.0,
            max_latency_ms=120.0,
            avg_packet_loss_percent=0.0,
            longest_outage_seconds=43.2,
        )
        db.upsert_daily_stat(row)
        stats = db.get_daily_stats(start="2024-01-15", end="2024-01-15")
        assert len(stats) == 1
        assert stats[0]["availability_percent"] == 99.95

    def test_idempotent_upsert(self, db):
        row = DailyStatRow(
            date_str="2024-01-15",
            availability_percent=99.0,
            downtime_seconds=864.0,
            outage_count=2,
            isp_failure_count=2,
            local_failure_count=0,
            dns_failure_count=0,
            packet_loss_events=0,
            latency_events=0,
            avg_latency_ms=20.0,
            max_latency_ms=200.0,
            avg_packet_loss_percent=1.0,
            longest_outage_seconds=500.0,
        )
        db.upsert_daily_stat(row)
        row.availability_percent = 98.0
        db.upsert_daily_stat(row)
        stats = db.get_daily_stats()
        assert len(stats) == 1
        assert stats[0]["availability_percent"] == 98.0


class TestSummaryStats:
    def test_summary_empty(self, db):
        summary = db.get_summary_stats()
        assert summary["total_events"] == 0
        assert summary["total_measurements"] == 0
        assert summary["open_events"] == 0
