"""Provider report (2026-09-27): router-log evidence is limited to the report period, ignores VPN
messages, confirms outages from the log instead of the lagging WAN state, and the cabling chapter
is optional with the following chapters renumbered."""

from __future__ import annotations

import csv
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import export
from src.config import AppConfig
from src.database import Database, EventRow
from src.fritzbox import _classify_log_entry


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Database(Path(tmpdir) / "test.db")


def _local_now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _event(db, event_id, local_start, minutes):
    start = local_start.astimezone(timezone.utc)
    end = start + timedelta(minutes=minutes)
    db.upsert_event(EventRow(
        event_id=event_id, event_type="ISP_FAILURE", started_at=start.isoformat(),
        ended_at=end.isoformat(), duration_seconds=minutes * 60, confidence_score=0.9,
        description="Gateway erreichbar, externe Ziele nicht", public_ipv4_before=None,
        public_ipv4_during=None, public_ipv4_after=None, public_ipv6_before=None,
        public_ipv6_during=None, public_ipv6_after=None, gateway_ip="192.168.178.1",
        hostname="test", network_interface="eth0", extra_json=None))


def _log(db, local_ts, msg):
    db.insert_fritzbox_log_entry(local_ts.strftime("%Y-%m-%dT%H:%M:%S"), msg,
                                 raw_date=local_ts.strftime("%d.%m.%y"),
                                 raw_time=local_ts.strftime("%H:%M:%S"),
                                 category=_classify_log_entry(msg)[0])


LOST = "DSL antwortet nicht (Keine DSL-Synchronisierung)."
GONE = "Internetverbindung wurde getrennt."
VPN = 'Die WireGuard-Verbindung zur Gegenstelle "Handy" (203.0.113.7:4500) wurde getrennt.'
PPPOE = "PPPoE-Fehler: Zeitüberschreitung."
CABLE = ("An der DSL-Leitung wurde eine Beeinträchtigung des Signals durch eine unzulässige Verkabelung "
         "erkannt. Die Beeinträchtigung kostet ungefähr 5100 kbit/s.")


def test_vpn_messages_are_not_line_drops():
    assert _classify_log_entry(VPN)[0] == "other"
    assert _classify_log_entry(GONE)[0] == "disconnect"


def test_router_log_only_report_period_and_no_vpn(db):
    now = _local_now()
    _log(db, now - timedelta(days=40), GONE)             # before the period
    _log(db, now - timedelta(days=2), GONE)
    _log(db, now - timedelta(days=2, seconds=4), LOST)
    _log(db, now - timedelta(days=2, seconds=-20), PPPOE)
    _log(db, now - timedelta(days=1), VPN)
    _log(db, now - timedelta(days=60), CABLE)            # before the period
    _log(db, now - timedelta(days=3), CABLE)
    log = export._router_log(db, now - timedelta(days=30), now)
    assert len(log["disconnected"]) == 1
    assert len(log["line_lost"]) == 1
    assert len(log["login_errors"]) == 1
    assert len(log["cabling"]) == 1
    assert not any("WireGuard" in r["message"] for r in log["rows"])


def test_outage_confirmed_by_router_log_within_window(db):
    now = _local_now()
    start = now - timedelta(days=1)
    _event(db, "e1", start, 6)
    _log(db, start - timedelta(seconds=4), LOST)
    _log(db, start, GONE)
    _event(db, "e2", now - timedelta(hours=5), 1)       # no router message nearby
    _log(db, now - timedelta(hours=7), GONE)           # far away: must not count for e2
    log = export._router_log(db, now - timedelta(days=30), now)
    events = {e["event_id"]: e for e in db.get_events(limit=10)}
    conf = export._router_confirmation(events["e1"], log)
    assert conf["line_lost"] == start - timedelta(seconds=4)
    assert conf["disconnected"] == start
    assert export._router_confirmation(events["e2"], log) is None


def test_chapter_numbers_without_cabling():
    with_cab, without = export._chapters(True), export._chapters(False)
    assert with_cab["cabling"] == 4 and with_cab["method"] == 8
    assert "cabling" not in without and without["line"] == 4 and without["method"] == 7


@pytest.mark.parametrize("show_cabling", [True, False])
def test_report_builds_and_counts_from_router_log(db, tmp_path, show_cabling):
    now = _local_now()
    for i in range(3):
        start = now - timedelta(days=i + 1, hours=2)
        _event(db, f"e{i}", start, 6)
        _log(db, start - timedelta(seconds=4), LOST)
        _log(db, start, GONE)
    _log(db, now - timedelta(days=1), VPN)
    _log(db, now - timedelta(days=90), GONE)
    _log(db, now - timedelta(days=2), CABLE)
    cfg = AppConfig()
    cfg.reports.provider_show_cabling = show_cabling

    a = export._analyse(db, cfg, 30)
    assert a["line_drops"] == 3 and len(a["disconnects"]) == 3 and len(a["line_lost"]) == 3
    summary = export._summarise(db, cfg, a, {"has_data": False}, export._chapters(show_cabling))
    assert any("3 von 3 Anbieter-Ausfällen" in f for f in summary["findings"])

    files = export.generate_provider_report(db, cfg, tmp_path, days=30)
    assert files["pdf"].stat().st_size > 1000
    with files["log"].open(encoding="utf-8") as fh:
        messages = [row[-1] for row in csv.reader(fh)][1:]
    assert not any("WireGuard" in m for m in messages)
    assert len(messages) == 7  # 3× LOST + 3× GONE + CABLE, nothing older than 30 days
