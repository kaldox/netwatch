"""Public-IP handling: failed lookups are not IP changes (2026-09-24).

Before, every failed lookup (None, e.g. during an outage) was stored as a "change" and the recovery as a
second one – the monthly report showed ~169 IP changes where 15 real ones happened. Also covers the
report/dashboard counting, which must stay correct for the rows older versions already stored."""

from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.main as main_mod
from src.database import Database, PublicIpRow
from src.reports import real_ip_changes


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Database(Path(tmpdir) / "test.db")


def _watch(db, lookups):
    """NetWatch object without the heavy __init__: only what _check_public_ip needs."""
    nw = main_mod.NetWatch.__new__(main_mod.NetWatch)
    nw.db = db
    nw.cfg = types.SimpleNamespace(public_ip=types.SimpleNamespace(providers=["v4"], ipv6_providers=["v6"]))
    nw.monitor = types.SimpleNamespace(ips=None)
    nw.monitor.set_public_ips = lambda v4, v6: setattr(nw.monitor, "ips", (v4, v6))
    seq = iter(lookups)          # one (ipv4, ipv6) pair per check
    pending = []

    def fake_get_public_ip(providers, timeout=10):
        if not pending:
            pending.extend(next(seq))
        return pending.pop(0)
    return nw, fake_get_public_ip


def test_failed_lookup_is_not_a_change(db, monkeypatch):
    nw, fake = _watch(db, [("203.0.113.1", None), (None, None), ("203.0.113.1", None)])
    monkeypatch.setattr(main_mod, "get_public_ip", fake)
    for _ in range(3):
        nw._check_public_ip()
    rows = db.get_public_ip_history()
    assert len(rows) == 1, rows                     # only the first known address, no None row
    assert rows[0]["changed"] == 0
    assert nw.monitor.ips == ("203.0.113.1", None)  # monitor keeps the last known address


def test_real_change_is_recorded(db, monkeypatch):
    nw, fake = _watch(db, [("203.0.113.1", None), (None, None), ("203.0.113.9", None)])
    monkeypatch.setattr(main_mod, "get_public_ip", fake)
    for _ in range(3):
        nw._check_public_ip()
    rows = db.get_public_ip_history()               # newest first
    assert [r["ipv4"] for r in rows] == ["203.0.113.9", "203.0.113.1"]
    assert rows[0]["changed"] == 1


def test_latest_ip_skips_old_failed_lookup_rows(db):
    # rows as older versions stored them: known → None → same known again
    db.insert_public_ip(PublicIpRow("2026-09-22T10:00:00+00:00", "203.0.113.1", None, 0))
    db.insert_public_ip(PublicIpRow("2026-09-22T10:05:00+00:00", None, None, 1))
    assert db.get_latest_public_ip()["ipv4"] == "203.0.113.1"
    assert db.get_summary_stats()["latest_ip"]["ipv4"] == "203.0.113.1"


def test_report_counts_only_real_changes_within_period():
    history = [  # newest first, like get_public_ip_history
        {"timestamp": "2026-09-22T10:10:00+00:00", "ipv4": "203.0.113.9", "changed": 1},
        {"timestamp": "2026-09-22T10:05:00+00:00", "ipv4": None, "changed": 1},        # failed lookup
        {"timestamp": "2026-09-12T15:05:00+00:00", "ipv4": "203.0.113.1", "changed": 1},  # back again
        {"timestamp": "2026-09-12T14:55:00+00:00", "ipv4": None, "changed": 1},        # failed lookup
        {"timestamp": "2026-08-30T08:00:00+00:00", "ipv4": "203.0.113.1", "changed": 1},  # before period
        {"timestamp": "2026-08-20T08:00:00+00:00", "ipv4": "203.0.113.5", "changed": 0},
    ]
    changes = real_ip_changes(history, since="2026-09-01")
    assert changes == [{"timestamp": "2026-09-22T10:10:00+00:00", "before": "203.0.113.1", "after": "203.0.113.9"}]
    assert len(real_ip_changes(history)) == 2        # without period: also the August change


def test_history_until(db):
    db.insert_public_ip(PublicIpRow("2026-08-31T10:00:00+00:00", "203.0.113.1", None, 0))
    db.insert_public_ip(PublicIpRow("2026-10-01T10:00:00+00:00", "203.0.113.2", None, 1))
    rows = db.get_public_ip_history(until="2026-09-30T23:59:59")
    assert [r["ipv4"] for r in rows] == ["203.0.113.1"]


def test_dashboard_ip_table_hides_failed_lookups(db):
    from src.config import AppConfig
    from src.dashboard import create_app
    for ts, ip, ch in [("2026-09-12T14:50:00+00:00", "203.0.113.1", 0),
                       ("2026-09-12T14:55:00+00:00", None, 1),            # failed lookup (old version)
                       ("2026-09-12T15:05:00+00:00", "203.0.113.1", 1),   # "changed back" (old version)
                       ("2026-09-22T10:10:00+00:00", "203.0.113.9", 1)]:  # real change
        db.insert_public_ip(PublicIpRow(ts, ip, None, ch))
    rows = create_app(db, AppConfig()).test_client().get("/api/public_ip").get_json()
    assert [(r["ipv4"], r["changed"]) for r in rows] == [("203.0.113.9", 1), ("203.0.113.1", 0)]


def test_refresh_does_not_block_the_loop(db, monkeypatch):
    """The lookup runs in a background thread; a second call while it runs does not start another one."""
    import threading
    release = threading.Event()
    calls = []

    def slow_check():
        calls.append(1)
        release.wait(5)

    nw = main_mod.NetWatch.__new__(main_mod.NetWatch)
    nw.cfg = types.SimpleNamespace(public_ip=types.SimpleNamespace(check_interval_seconds=0))
    nw._last_public_ip_check = 0.0
    nw._ip_thread = None
    nw._check_public_ip = slow_check
    nw._refresh_public_ip()          # returns immediately although the check is still "running"
    nw._refresh_public_ip()          # still running → no second thread
    release.set()
    nw._ip_thread.join(2)
    assert calls == [1]
