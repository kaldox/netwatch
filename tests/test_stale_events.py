"""Stale events (2026-10-02): an event left open by a restart is closed at startup at the last
measurement before the restart gap, and the daily statistics no longer count it until midnight."""

from __future__ import annotations

import sys
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.main as main_mod
from src.database import Database, EventRow, MeasurementRow
from src.statistics import stale_event_end

T0 = datetime(2026, 10, 1, 20, 17, 0, tzinfo=timezone.utc)


def iso(sec: float) -> str:
    return (T0 + timedelta(seconds=sec)).isoformat()


def test_end_is_last_measurement_before_gap():
    stamps = [iso(s) for s in (0, 3, 6, 9, 12)] + [iso(s) for s in (900, 903)]   # Lücke = Neustart
    assert stale_event_end(iso(5), stamps) == iso(12)


def test_end_without_measurements_is_start():
    assert stale_event_end(iso(5), []) == iso(5)
    assert stale_event_end(iso(5), [iso(600)]) == iso(5)     # erste Messung erst nach der Lücke


def test_ignores_measurements_before_start():
    stamps = [iso(s) for s in (0, 2, 4, 6, 8)]
    assert stale_event_end(iso(5), stamps) == iso(8)


def _event(event_id: str, start: str) -> EventRow:
    return EventRow(event_id=event_id, event_type="LOCAL_NETWORK_FAILURE", started_at=start, ended_at=None,
                    duration_seconds=None, confidence_score=0.9, description="test",
                    public_ipv4_before=None, public_ipv4_during=None, public_ipv4_after=None,
                    public_ipv6_before=None, public_ipv6_during=None, public_ipv6_after=None,
                    gateway_ip=None, hostname="t", network_interface="eth0", extra_json="{}",
                    cpu_percent=None, ram_percent=None, load_avg_1m=None, cpu_temp_celsius=None,
                    measurement_cycle_seconds=None)


def test_startup_closes_stale_event_and_fixes_daily_stats(tmp_path):
    db = Database(tmp_path / "t.db")
    for s in list(range(0, 240, 3)) + list(range(1200, 1260, 3)):      # 4 min Messungen, Lücke, weiter
        db.insert_measurement(MeasurementRow(timestamp=iso(s), target_name="GW", target_host="192.168.178.1",
                                             target_type="gateway", reachable=0, latency_ms=None,
                                             packet_loss_percent=100.0, jitter_ms=None, dns_resolution_ms=None,
                                             public_ipv4=None, public_ipv6=None, gateway_reachable=0,
                                             error_message=None))
    db.upsert_event(_event("e1", iso(30)))
    nw = main_mod.NetWatch.__new__(main_mod.NetWatch)
    nw.db = db
    stored = []
    nw._compute_and_store_daily_stats = lambda d: stored.append(d)
    nw._close_stale_events(T0 + timedelta(seconds=1250))

    assert db.get_open_events() == []
    ev = db.get_events(limit=5)[0]
    assert ev["ended_at"] == iso(237)
    assert abs(ev["duration_seconds"] - 207) < 0.01
    assert "closed_after_restart" in ev["extra_json"]
    assert stored[0] == date(2026, 10, 1)          # betroffene Tage werden neu berechnet

    nw._close_stale_events(T0 + timedelta(seconds=1300))   # zweiter Start: nichts mehr offen
    assert db.get_events(limit=5)[0]["ended_at"] == iso(237)
