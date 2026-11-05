"""Router-Störungen (2026-11-05): Wer an den Router stösst oder ihn neu startet, verursacht
keinen Provider-Ausfall. Echte Folge vom 05.11.2026 (UTC):
  19:32:31 Internet weg, 19:32:38–44 Router kurz nicht erreichbar (Kabel/Stecker berührt)
  19:33:45–19:34:48 Router aus (Netzstecker gezogen), 19:34:53 Router wieder da,
  19:36:08 Internet wieder da (DSL-Training + Einwahl nach dem Neustart).
Vorher stand das als zwei ISP-Ausfälle in der Statistik."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.classifier import Classifier, EventType
from src.database import Database, EventRow, MeasurementRow
from src.monitor import MeasurementResult
from src.router_fault import ROUTER_FAULT_DESCRIPTION, reclassify_history, router_fault_reason

T0 = datetime(2026, 11, 5, 19, 32, 0, tzinfo=timezone.utc)


def iso(sec: float) -> str:
    return (T0 + timedelta(seconds=sec)).isoformat()


# ---------------------------------------------------------------------------
# Classifier (Echtzeit)
# ---------------------------------------------------------------------------

def _round(gateway: bool, external: bool) -> list[MeasurementResult]:
    def r(name, ttype, ok):
        return MeasurementResult(
            timestamp="x", target_name=name, target_host=name, target_type=ttype,
            reachable=ok, latency_ms=10.0 if ok else None,
            packet_loss_percent=0.0 if ok else 100.0, jitter_ms=None,
            dns_resolution_ms=None, gateway_reachable=gateway,
            public_ipv4="1.2.3.4", public_ipv6=None)
    return [r("Gateway", "gateway", gateway),
            r("Cloudflare DNS", "icmp", gateway and external),
            r("Google DNS", "icmp", gateway and external),
            r("Quad9 DNS", "icmp", gateway and external),
            r("Google", "dns", gateway and external)]


class Clock:
    def __init__(self):
        self.sec = 0.0

    def __call__(self):
        return T0 + timedelta(seconds=self.sec)


def _run(seq):
    """seq: list of (gateway_ok, external_ok, repeat). 5 s per cycle. Returns all changed events."""
    c = Classifier(failure_threshold=3, recovery_threshold=3)
    clock = Clock()
    c._clock = clock
    out = []
    for gw, ext, n in seq:
        for _ in range(n):
            out += c.process(_round(gw, ext))
            clock.sec += 5
    return out


def _closed(events):
    # opened and closed events are the same (mutated) object → one entry per event
    seen = {}
    for e in events:
        seen[e.event_id] = e
    assert all(not e.is_open for e in seen.values())
    return list(seen.values())


def test_plain_isp_outage_stays_isp():
    evs = _closed(_run([(True, True, 5), (True, False, 60), (True, True, 5)]))
    assert [e.event_type for e in evs] == [EventType.ISP_FAILURE]
    assert "router_fault" not in evs[0].extra


def test_router_bumped_then_internet_gone_is_local():
    # Router 2 Zyklen weg (unter der Schwelle für "lokal"), danach nur noch Internet weg
    evs = _closed(_run([(True, True, 5), (False, False, 2), (True, False, 12), (True, True, 5)]))
    assert [e.event_type for e in evs] == [EventType.LOCAL_NETWORK_FAILURE]
    assert evs[0].description == ROUTER_FAULT_DESCRIPTION
    assert evs[0].extra["original_event_type"] == "ISP_FAILURE"
    assert evs[0].extra["router_fault_detected"] == "in Echtzeit"


def test_router_restart_is_local_twice():
    # Neustart: Router 13 Zyklen weg, danach noch 15 Zyklen ohne Internet (DSL-Training)
    evs = _closed(_run([(True, True, 5), (False, False, 13), (True, False, 15), (True, True, 5)]))
    assert [e.event_type for e in evs] == [EventType.LOCAL_NETWORK_FAILURE] * 2
    assert "router_fault" in evs[1].extra


def test_long_outage_after_restart_remains_isp():
    # Router während eines echten Ausfalls neu gestartet, Internet danach noch 10 min weg
    evs = _closed(_run([(True, True, 5), (False, False, 13), (True, False, 120), (True, True, 5)]))
    assert evs[-1].event_type == EventType.ISP_FAILURE
    assert "router_fault_rejected" in evs[-1].extra
    assert evs[-1].description.startswith("Gateway erreichbar")


def test_no_ghost_isp_event_after_short_local_outage():
    # Bisher: nach jedem lokalen Ausfall ein ~10-s-"ISP-Ausfall", weil die Ziele erst nach
    # 3 erfolgreichen Messungen als erholt galten, obwohl das Internet sofort wieder da war.
    evs = _closed(_run([(True, True, 5), (False, False, 5), (True, True, 10)]))
    assert [e.event_type for e in evs] == [EventType.LOCAL_NETWORK_FAILURE]


def test_old_gateway_glitch_does_not_count():
    # Router-Aussetzer liegt mehr als 60 s vor dem Internet-Ausfall
    evs = _closed(_run([(True, True, 5), (False, False, 1), (True, True, 20),
                        (True, False, 20), (True, True, 5)]))
    assert [e.event_type for e in evs] == [EventType.ISP_FAILURE]


# ---------------------------------------------------------------------------
# Regel + nachträgliche Korrektur aus der Datenbank
# ---------------------------------------------------------------------------

def test_rule_window_and_duration():
    assert router_fault_reason(iso(51), 58, [iso(38), iso(44)], [])
    assert router_fault_reason(iso(176), 84, [], [iso(176)])
    assert router_fault_reason(iso(51), 58, [iso(-30)], []) is None        # zu früh
    assert router_fault_reason(iso(51), 58, [iso(60)], []) is None         # erst danach
    assert router_fault_reason(iso(51), 900, [iso(44)], []) is None        # zu lang
    assert router_fault_reason(iso(51), None, [iso(44)], []) is None       # noch offen


def _event(eid, etype, start, end, desc="Gateway erreichbar, aber …"):
    s, e = iso(start), iso(end)
    return EventRow(event_id=eid, event_type=etype, started_at=s, ended_at=e,
                    duration_seconds=end - start, confidence_score=0.9, description=desc,
                    public_ipv4_before=None, public_ipv4_during=None, public_ipv4_after=None,
                    public_ipv6_before=None, public_ipv6_during=None, public_ipv6_after=None,
                    gateway_ip="192.168.178.1", hostname="t", network_interface="eth0",
                    extra_json='{"public_ip_failing": 3, "public_ip_total": 3}')


def _gw(db, sec, ok):
    db.insert_measurement(MeasurementRow(
        timestamp=iso(sec), target_name="Gateway", target_host="192.168.178.1",
        target_type="gateway", reachable=ok, latency_ms=None, packet_loss_percent=0.0,
        jitter_ms=None, dns_resolution_ms=None, public_ipv4=None, public_ipv6=None,
        gateway_reachable=ok, error_message=None))


def test_history_of_2026_11_05_is_corrected(tmp_path):
    db = Database(tmp_path / "t.db")
    for s in range(0, 300, 5):
        _gw(db, s, 0 if s in (35, 40) or 105 <= s <= 170 else 1)
    db.upsert_event(_event("bump", "ISP_FAILURE", 51, 109))
    db.upsert_event(_event("plug", "LOCAL_NETWORK_FAILURE", 123, 176, "Gateway nicht erreichbar"))
    db.upsert_event(_event("boot", "ISP_FAILURE", 176, 260))
    # echter Provider-Ausfall am Abend: Router durchgehend erreichbar
    for s in range(3600, 4200, 5):
        _gw(db, s, 1)
    db.upsert_event(_event("real", "ISP_FAILURE", 3700, 4040))

    days = reclassify_history(db)
    assert days == {date(2026, 11, 5)}
    by_id = {e["event_id"]: e for e in db.get_events(limit=10)}
    assert by_id["bump"]["event_type"] == "LOCAL_NETWORK_FAILURE"
    assert by_id["boot"]["event_type"] == "LOCAL_NETWORK_FAILURE"
    assert by_id["real"]["event_type"] == "ISP_FAILURE"
    extra = json.loads(by_id["bump"]["extra_json"])
    assert extra["original_event_type"] == "ISP_FAILURE"
    assert extra["router_fault_detected"] == "nachträglich"
    assert extra["public_ip_total"] == 3                       # alte Zusatzdaten bleiben
    assert reclassify_history(db) == set()                     # zweiter Lauf: nichts mehr zu tun


def test_upsert_updates_event_type(tmp_path):
    db = Database(tmp_path / "t.db")
    row = _event("x", "LOCAL_NETWORK_FAILURE", 0, 50)
    row.ended_at = None
    db.upsert_event(row)
    row.event_type, row.ended_at = "ISP_FAILURE", iso(600)
    db.upsert_event(row)
    assert db.get_events(limit=1)[0]["event_type"] == "ISP_FAILURE"
