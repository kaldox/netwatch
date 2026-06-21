"""
NetWatch – Failure classifier.
Analyses recent measurement results and emits structured NetworkEvent objects.
"""

from __future__ import annotations

import json
import logging
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from .monitor import MeasurementResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


class EventType(str, Enum):
    LOCAL_NETWORK_FAILURE = "LOCAL_NETWORK_FAILURE"
    ISP_FAILURE = "ISP_FAILURE"
    DNS_FAILURE = "DNS_FAILURE"
    LATENCY_DEGRADATION = "LATENCY_DEGRADATION"
    PACKET_LOSS = "PACKET_LOSS"
    ROUTING_FAILURE = "ROUTING_FAILURE"
    RECOVERED = "RECOVERED"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Network event
# ---------------------------------------------------------------------------


@dataclass
class NetworkEvent:
    event_id: str
    event_type: EventType
    started_at: str
    ended_at: Optional[str]
    confidence_score: float
    description: str
    public_ipv4_before: Optional[str]
    public_ipv4_during: Optional[str]
    public_ipv4_after: Optional[str]
    public_ipv6_before: Optional[str]
    public_ipv6_during: Optional[str]
    public_ipv6_after: Optional[str]
    gateway_ip: Optional[str]
    hostname: str
    network_interface: Optional[str]
    extra: dict = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return self.ended_at is None

    @property
    def duration_seconds(self) -> Optional[float]:
        if not self.ended_at:
            return None
        try:
            start = datetime.fromisoformat(self.started_at)
            end = datetime.fromisoformat(self.ended_at)
            return (end - start).total_seconds()
        except Exception:
            return None

    def extra_json(self) -> str:
        return json.dumps(self.extra)


# ---------------------------------------------------------------------------
# Classifier state
# ---------------------------------------------------------------------------


class TargetState:
    """Sliding window state for a single target."""

    def __init__(
        self,
        failure_threshold: int,
        recovery_threshold: int,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_threshold = recovery_threshold
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._in_failure = False

    def update(self, reachable: bool) -> tuple[bool, bool]:
        """
        Feed a new result.
        Returns (transition_to_failure, transition_to_recovery).
        """
        if reachable:
            self._consecutive_failures = 0
            self._consecutive_successes += 1
            if self._in_failure and self._consecutive_successes >= self.recovery_threshold:
                self._in_failure = False
                return False, True
        else:
            self._consecutive_successes = 0
            self._consecutive_failures += 1
            if not self._in_failure and self._consecutive_failures >= self.failure_threshold:
                self._in_failure = True
                return True, False
        return False, False

    @property
    def is_failing(self) -> bool:
        return self._in_failure


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class Classifier:
    """
    Consumes measurement results and classifies network events.

    Classification logic:
    - ALL local targets down            → LOCAL_NETWORK_FAILURE (confidence 0.95)
    - Gateway up, external IPs down     → ISP_FAILURE (confidence 0.90)
    - External IPs up, DNS fails        → DNS_FAILURE (confidence 0.90)
    - Partial external reachability     → ROUTING_FAILURE (confidence 0.70)
    - Latency above threshold           → LATENCY_DEGRADATION (confidence 0.80)
    - Packet loss above threshold       → PACKET_LOSS (confidence 0.80)
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_threshold: int = 3,
        latency_critical_ms: float = 500.0,
        packet_loss_critical_percent: float = 20.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_threshold = recovery_threshold
        self.latency_critical_ms = latency_critical_ms
        self.packet_loss_critical_percent = packet_loss_critical_percent

        # Per-target state machines
        self._target_states: dict[str, TargetState] = {}

        # Active events keyed by event_type (only one of each type at a time)
        self.active_events: dict[str, NetworkEvent] = {}

        self._last_public_ipv4: Optional[str] = None
        self._last_public_ipv6: Optional[str] = None
        self._gateway_ip: Optional[str] = None
        self._network_interface: Optional[str] = None
        self._hostname: str = socket.gethostname()

    # ------------------------------------------------------------------

    def _state(self, name: str) -> TargetState:
        if name not in self._target_states:
            self._target_states[name] = TargetState(
                self.failure_threshold, self.recovery_threshold
            )
        return self._target_states[name]

    def set_gateway(self, gw: Optional[str], iface: Optional[str]) -> None:
        self._gateway_ip = gw
        self._network_interface = iface

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _make_event(
        self,
        event_type: EventType,
        confidence: float,
        description: str,
        current_ipv4: Optional[str],
        current_ipv6: Optional[str],
        extra: Optional[dict] = None,
    ) -> NetworkEvent:
        return NetworkEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            started_at=self._now(),
            ended_at=None,
            confidence_score=confidence,
            description=description,
            public_ipv4_before=current_ipv4,
            public_ipv4_during=None,
            public_ipv4_after=None,
            public_ipv6_before=current_ipv6,
            public_ipv6_during=None,
            public_ipv6_after=None,
            gateway_ip=self._gateway_ip,
            hostname=self._hostname,
            network_interface=self._network_interface,
            extra=extra or {},
        )

    def _close_event(
        self,
        event: NetworkEvent,
        current_ipv4: Optional[str],
        current_ipv6: Optional[str],
    ) -> NetworkEvent:
        event.ended_at = self._now()
        event.public_ipv4_after = current_ipv4
        event.public_ipv6_after = current_ipv6
        return event

    # ------------------------------------------------------------------

    def process(
        self,
        results: list[MeasurementResult],
    ) -> list[NetworkEvent]:
        """
        Process a full measurement round.
        Returns a list of newly opened or closed NetworkEvent objects.
        """
        changed_events: list[NetworkEvent] = []

        if not results:
            return changed_events

        # Current public IPs from latest measurement
        current_ipv4 = results[0].public_ipv4
        current_ipv6 = results[0].public_ipv6
        self._last_public_ipv4 = current_ipv4
        self._last_public_ipv6 = current_ipv6

        # Bucket results by category
        local_results = [r for r in results if r.target_type in ("gateway",)]
        public_ip_results = [r for r in results if r.target_type == "icmp"]
        dns_results = [r for r in results if r.target_type == "dns"]

        # Update per-target state machines
        for r in results:
            self._state(r.target_name).update(r.reachable)

        gateway_reachable = results[0].gateway_reachable
        local_failing = not gateway_reachable

        public_ip_failing_count = sum(
            1 for r in public_ip_results
            if self._state(r.target_name).is_failing
        )
        public_ip_total = len(public_ip_results)

        dns_failing_count = sum(
            1 for r in dns_results
            if self._state(r.target_name).is_failing
        )
        dns_total = len(dns_results)

        all_public_ips_failing = (
            public_ip_total > 0 and public_ip_failing_count == public_ip_total
        )
        partial_public_ip_failure = (
            0 < public_ip_failing_count < public_ip_total
        )
        all_dns_failing = dns_total > 0 and dns_failing_count == dns_total

        # ---- LOCAL NETWORK FAILURE -----------------------------------
        type_key = EventType.LOCAL_NETWORK_FAILURE.value
        if local_failing and not gateway_reachable:
            if type_key not in self.active_events:
                ev = self._make_event(
                    EventType.LOCAL_NETWORK_FAILURE,
                    confidence=0.95,
                    description="Gateway nicht erreichbar – lokales Netzwerkproblem",
                    current_ipv4=current_ipv4,
                    current_ipv6=current_ipv6,
                )
                self.active_events[type_key] = ev
                changed_events.append(ev)
        else:
            if type_key in self.active_events:
                ev = self._close_event(
                    self.active_events.pop(type_key), current_ipv4, current_ipv6
                )
                changed_events.append(ev)

        # ---- ISP FAILURE ---------------------------------------------
        type_key = EventType.ISP_FAILURE.value
        if gateway_reachable and all_public_ips_failing:
            if type_key not in self.active_events:
                ev = self._make_event(
                    EventType.ISP_FAILURE,
                    confidence=0.90,
                    description=(
                        f"Gateway erreichbar, aber alle {public_ip_total} "
                        f"externen Ziele nicht erreichbar – ISP-Ausfall wahrscheinlich"
                    ),
                    current_ipv4=current_ipv4,
                    current_ipv6=current_ipv6,
                    extra={"public_ip_failing": public_ip_failing_count,
                           "public_ip_total": public_ip_total},
                )
                self.active_events[type_key] = ev
                changed_events.append(ev)
            else:
                # Update during-IP
                self.active_events[type_key].public_ipv4_during = current_ipv4
                self.active_events[type_key].public_ipv6_during = current_ipv6
        else:
            if type_key in self.active_events:
                ev = self._close_event(
                    self.active_events.pop(type_key), current_ipv4, current_ipv6
                )
                changed_events.append(ev)

        # ---- ROUTING FAILURE (partial external) ----------------------
        type_key = EventType.ROUTING_FAILURE.value
        if gateway_reachable and partial_public_ip_failure:
            if type_key not in self.active_events:
                ev = self._make_event(
                    EventType.ROUTING_FAILURE,
                    confidence=0.70,
                    description=(
                        f"{public_ip_failing_count}/{public_ip_total} "
                        f"externe Ziele nicht erreichbar – Routingproblem möglich"
                    ),
                    current_ipv4=current_ipv4,
                    current_ipv6=current_ipv6,
                    extra={"public_ip_failing": public_ip_failing_count,
                           "public_ip_total": public_ip_total},
                )
                self.active_events[type_key] = ev
                changed_events.append(ev)
        else:
            if type_key in self.active_events:
                ev = self._close_event(
                    self.active_events.pop(type_key), current_ipv4, current_ipv6
                )
                changed_events.append(ev)

        # ---- DNS FAILURE ---------------------------------------------
        type_key = EventType.DNS_FAILURE.value
        if not all_public_ips_failing and all_dns_failing:
            if type_key not in self.active_events:
                ev = self._make_event(
                    EventType.DNS_FAILURE,
                    confidence=0.90,
                    description=(
                        f"Externe IPs erreichbar, aber alle {dns_total} "
                        f"DNS-Auflösungen schlagen fehl"
                    ),
                    current_ipv4=current_ipv4,
                    current_ipv6=current_ipv6,
                )
                self.active_events[type_key] = ev
                changed_events.append(ev)
        else:
            if type_key in self.active_events:
                ev = self._close_event(
                    self.active_events.pop(type_key), current_ipv4, current_ipv6
                )
                changed_events.append(ev)

        # ---- LATENCY DEGRADATION -------------------------------------
        type_key = EventType.LATENCY_DEGRADATION.value
        high_latency_targets = [
            r for r in results
            if r.latency_ms is not None
            and r.latency_ms > self.latency_critical_ms
        ]
        if high_latency_targets:
            if type_key not in self.active_events:
                worst = max(high_latency_targets, key=lambda r: r.latency_ms or 0)
                ev = self._make_event(
                    EventType.LATENCY_DEGRADATION,
                    confidence=0.80,
                    description=(
                        f"Hohe Latenz: {worst.target_name} = "
                        f"{worst.latency_ms:.0f} ms "
                        f"(Grenzwert: {self.latency_critical_ms:.0f} ms)"
                    ),
                    current_ipv4=current_ipv4,
                    current_ipv6=current_ipv6,
                    extra={"worst_target": worst.target_name,
                           "worst_latency_ms": worst.latency_ms},
                )
                self.active_events[type_key] = ev
                changed_events.append(ev)
        else:
            if type_key in self.active_events:
                ev = self._close_event(
                    self.active_events.pop(type_key), current_ipv4, current_ipv6
                )
                changed_events.append(ev)

        # ---- PACKET LOSS --------------------------------------------
        type_key = EventType.PACKET_LOSS.value
        high_loss_targets = [
            r for r in results
            if r.packet_loss_percent > self.packet_loss_critical_percent
            and r.reachable  # some packets got through
        ]
        if high_loss_targets:
            if type_key not in self.active_events:
                worst = max(high_loss_targets, key=lambda r: r.packet_loss_percent)
                ev = self._make_event(
                    EventType.PACKET_LOSS,
                    confidence=0.80,
                    description=(
                        f"Paketverlust: {worst.target_name} = "
                        f"{worst.packet_loss_percent:.0f}% "
                        f"(Grenzwert: {self.packet_loss_critical_percent:.0f}%)"
                    ),
                    current_ipv4=current_ipv4,
                    current_ipv6=current_ipv6,
                    extra={"worst_target": worst.target_name,
                           "worst_loss_percent": worst.packet_loss_percent},
                )
                self.active_events[type_key] = ev
                changed_events.append(ev)
        else:
            if type_key in self.active_events:
                ev = self._close_event(
                    self.active_events.pop(type_key), current_ipv4, current_ipv6
                )
                changed_events.append(ev)

        return changed_events

    def get_active_events(self) -> list[NetworkEvent]:
        return list(self.active_events.values())

    def current_status(self) -> str:
        """Return human-readable current status string."""
        active = self.get_active_events()
        if not active:
            return "OK"
        types = ", ".join(e.event_type.value for e in active)
        return f"PROBLEM ({types})"
