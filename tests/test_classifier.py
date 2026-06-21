"""Unit tests for src/classifier.py"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.classifier import Classifier, EventType
from src.monitor import MeasurementResult


def _make_result(
    name: str,
    host: str,
    ttype: str,
    reachable: bool,
    latency_ms: float | None = 10.0,
    packet_loss: float = 0.0,
    gateway_reachable: bool = True,
) -> MeasurementResult:
    return MeasurementResult(
        timestamp="2024-01-01T00:00:00+00:00",
        target_name=name,
        target_host=host,
        target_type=ttype,
        reachable=reachable,
        latency_ms=latency_ms,
        packet_loss_percent=packet_loss,
        jitter_ms=None,
        dns_resolution_ms=None,
        gateway_reachable=gateway_reachable,
        public_ipv4="1.2.3.4",
        public_ipv6=None,
    )


def _all_ok() -> list[MeasurementResult]:
    return [
        _make_result("Gateway", "192.168.1.1", "gateway", True),
        _make_result("Cloudflare DNS", "1.1.1.1", "icmp", True),
        _make_result("Google DNS", "8.8.8.8", "icmp", True),
        _make_result("Quad9 DNS", "9.9.9.9", "icmp", True),
        _make_result("Google", "google.com", "dns", True),
    ]


def _isp_failure() -> list[MeasurementResult]:
    return [
        _make_result("Gateway", "192.168.1.1", "gateway", True),
        _make_result("Cloudflare DNS", "1.1.1.1", "icmp", False, None, 100.0),
        _make_result("Google DNS", "8.8.8.8", "icmp", False, None, 100.0),
        _make_result("Quad9 DNS", "9.9.9.9", "icmp", False, None, 100.0),
        _make_result("Google", "google.com", "dns", False),
    ]


def _local_failure() -> list[MeasurementResult]:
    return [
        _make_result("Gateway", "192.168.1.1", "gateway", False, None, 100.0, gateway_reachable=False),
        _make_result("Cloudflare DNS", "1.1.1.1", "icmp", False, None, 100.0, gateway_reachable=False),
        _make_result("Google DNS", "8.8.8.8", "icmp", False, None, 100.0, gateway_reachable=False),
        _make_result("Quad9 DNS", "9.9.9.9", "icmp", False, None, 100.0, gateway_reachable=False),
        _make_result("Google", "google.com", "dns", False, gateway_reachable=False),
    ]


def _dns_failure() -> list[MeasurementResult]:
    return [
        _make_result("Gateway", "192.168.1.1", "gateway", True),
        _make_result("Cloudflare DNS", "1.1.1.1", "icmp", True),
        _make_result("Google DNS", "8.8.8.8", "icmp", True),
        _make_result("Quad9 DNS", "9.9.9.9", "icmp", True),
        _make_result("Google", "google.com", "dns", False),
        _make_result("GitHub", "github.com", "dns", False),
    ]


def _high_latency() -> list[MeasurementResult]:
    return [
        _make_result("Gateway", "192.168.1.1", "gateway", True, 5.0),
        _make_result("Cloudflare DNS", "1.1.1.1", "icmp", True, 800.0),
        _make_result("Google DNS", "8.8.8.8", "icmp", True, 750.0),
        _make_result("Quad9 DNS", "9.9.9.9", "icmp", True, 620.0),
        _make_result("Google", "google.com", "dns", True, 200.0),
    ]


class TestTargetState:
    def test_failure_threshold(self):
        from src.classifier import TargetState
        s = TargetState(failure_threshold=3, recovery_threshold=2)
        assert s.update(False) == (False, False)
        assert s.update(False) == (False, False)
        assert s.update(False) == (True, False)  # threshold hit
        assert s.is_failing

    def test_recovery_threshold(self):
        from src.classifier import TargetState
        s = TargetState(failure_threshold=1, recovery_threshold=2)
        s.update(False)  # goes failing
        assert s.is_failing
        s.update(True)
        assert s.is_failing  # not yet recovered
        s.update(True)
        assert not s.is_failing  # recovered

    def test_no_false_positive(self):
        from src.classifier import TargetState
        s = TargetState(failure_threshold=3, recovery_threshold=2)
        s.update(False)
        s.update(False)
        assert not s.is_failing  # only 2, threshold is 3


class TestClassifier:
    def _clf(self) -> Classifier:
        return Classifier(
            failure_threshold=3,
            recovery_threshold=3,
            latency_critical_ms=500.0,
            packet_loss_critical_percent=20.0,
        )

    def _feed(self, clf: Classifier, results_fn, n: int) -> list:
        events = []
        for _ in range(n):
            events.extend(clf.process(results_fn()))
        return events

    def test_ok_no_events(self):
        clf = self._clf()
        events = self._feed(clf, _all_ok, 5)
        assert not events

    def test_isp_failure_detected(self):
        clf = self._clf()
        events = self._feed(clf, _isp_failure, 3)
        types = [e.event_type for e in events]
        assert EventType.ISP_FAILURE in types

    def test_isp_failure_confidence(self):
        clf = self._clf()
        events = self._feed(clf, _isp_failure, 3)
        isp = [e for e in events if e.event_type == EventType.ISP_FAILURE]
        assert isp
        assert isp[0].confidence_score >= 0.85

    def test_local_failure_detected(self):
        clf = self._clf()
        events = self._feed(clf, _local_failure, 3)
        types = [e.event_type for e in events]
        assert EventType.LOCAL_NETWORK_FAILURE in types

    def test_dns_failure_detected(self):
        clf = self._clf()
        events = self._feed(clf, _dns_failure, 3)
        types = [e.event_type for e in events]
        assert EventType.DNS_FAILURE in types

    def test_latency_degradation_detected(self):
        clf = self._clf()
        events = self._feed(clf, _high_latency, 3)
        types = [e.event_type for e in events]
        assert EventType.LATENCY_DEGRADATION in types

    def test_recovery_after_isp_failure(self):
        clf = self._clf()
        self._feed(clf, _isp_failure, 3)
        assert EventType.ISP_FAILURE.value in clf.active_events

        recovery_events = self._feed(clf, _all_ok, 3)
        closed = [e for e in recovery_events if not e.is_open]
        assert any(e.event_type == EventType.ISP_FAILURE for e in closed)
        assert EventType.ISP_FAILURE.value not in clf.active_events

    def test_event_has_event_id(self):
        clf = self._clf()
        events = self._feed(clf, _isp_failure, 3)
        assert events
        assert all(e.event_id for e in events)

    def test_current_status_ok(self):
        clf = self._clf()
        self._feed(clf, _all_ok, 5)
        assert clf.current_status() == "OK"

    def test_current_status_problem(self):
        clf = self._clf()
        self._feed(clf, _isp_failure, 3)
        status = clf.current_status()
        assert "PROBLEM" in status


class TestPingParser:
    def test_parse_linux_output(self):
        from src.monitor import _parse_ping_output
        output = (
            "PING 1.1.1.1 (1.1.1.1) 56(84) bytes of data.\n"
            "64 bytes from 1.1.1.1: icmp_seq=1 ttl=57 time=12.3 ms\n"
            "\n"
            "--- 1.1.1.1 ping statistics ---\n"
            "3 packets transmitted, 3 received, 0% packet loss, time 2002ms\n"
            "rtt min/avg/max/mdev = 11.2/12.3/13.4/1.1 ms\n"
        )
        loss, avg, jitter = _parse_ping_output(output, 3)
        assert loss == 0.0
        assert abs(avg - 12.3) < 0.1
        assert jitter is not None
        assert abs(jitter - 1.1) < 0.1

    def test_parse_100_percent_loss(self):
        from src.monitor import _parse_ping_output
        output = (
            "PING 1.1.1.1 (1.1.1.1) 56(84) bytes of data.\n"
            "\n"
            "--- 1.1.1.1 ping statistics ---\n"
            "3 packets transmitted, 0 received, 100% packet loss, time 2002ms\n"
        )
        loss, avg, jitter = _parse_ping_output(output, 3)
        assert loss == 100.0
