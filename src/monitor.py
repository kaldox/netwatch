"""
NetWatch – Core measurement engine.
Performs ICMP pings, DNS resolutions, and gateway detection.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import platform
import re
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import dns.resolver
import dns.exception

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class PingResult:
    host: str
    reachable: bool
    latency_ms: Optional[float]      # round-trip average
    packet_loss_percent: float
    jitter_ms: Optional[float]
    error: Optional[str] = None


@dataclass
class DnsResult:
    host: str
    resolved: bool
    resolution_ms: Optional[float]
    resolved_ips: list[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class MeasurementResult:
    timestamp: str
    target_name: str
    target_host: str
    target_type: str
    reachable: bool
    latency_ms: Optional[float]
    packet_loss_percent: float
    jitter_ms: Optional[float]
    dns_resolution_ms: Optional[float]
    gateway_reachable: bool
    public_ipv4: Optional[str]
    public_ipv6: Optional[str]
    error_message: Optional[str] = None


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------


def detect_gateway() -> Optional[str]:
    """
    Detect the default gateway IP on Linux.
    Falls back to parsing /proc/net/route.
    """
    system = platform.system().lower()

    if system == "linux":
        try:
            result = subprocess.run(
                ["ip", "route", "show", "default"],
                capture_output=True, text=True, timeout=5,
            )
            # Output: "default via 192.168.1.1 dev eth0 ..."
            m = re.search(r"default via ([\d.]+)", result.stdout)
            if m:
                return m.group(1)
        except Exception:
            pass

        # Fallback: /proc/net/route
        try:
            with open("/proc/net/route") as fh:
                for line in fh:
                    parts = line.strip().split()
                    if len(parts) >= 3 and parts[1] == "00000000":
                        # Gateway stored as little-endian hex
                        gw_hex = parts[2]
                        gw_int = int(gw_hex, 16)
                        gw_bytes = gw_int.to_bytes(4, "little")
                        return str(ipaddress.IPv4Address(gw_bytes))
        except Exception:
            pass

    elif system == "darwin":
        try:
            result = subprocess.run(
                ["netstat", "-rn"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if line.startswith("default"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return parts[1]
        except Exception:
            pass

    return None


def detect_network_interface() -> Optional[str]:
    """Return the name of the primary network interface."""
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=5,
        )
        m = re.search(r"dev (\S+)", result.stdout)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Ping
# ---------------------------------------------------------------------------


def _parse_ping_output(output: str, count: int) -> tuple[float, float, Optional[float]]:
    """
    Parse Linux/macOS ping output.
    Returns (loss_percent, avg_ms, jitter_ms).
    """
    loss_percent = 100.0
    avg_ms: Optional[float] = None
    jitter_ms: Optional[float] = None

    # Packet loss
    m = re.search(r"(\d+(?:\.\d+)?)%\s+packet loss", output)
    if m:
        loss_percent = float(m.group(1))

    # Linux: rtt min/avg/max/mdev = 1.234/2.345/3.456/0.500 ms
    m = re.search(r"rtt\s+min/avg/max/mdev\s*=\s*([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)", output)
    if m:
        avg_ms = float(m.group(2))
        jitter_ms = float(m.group(4))
    else:
        # macOS: round-trip min/avg/max/stddev = 1.234/2.345/3.456/0.500 ms
        m = re.search(
            r"round-trip\s+min/avg/max/stddev\s*=\s*([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)",
            output,
        )
        if m:
            avg_ms = float(m.group(2))
            jitter_ms = float(m.group(4))

    return loss_percent, avg_ms or 0.0, jitter_ms


def ping(host: str, count: int = 3, timeout: int = 5) -> PingResult:
    """
    Execute system ping and return structured results.
    Uses -W for timeout on Linux, -t on macOS.
    """
    system = platform.system().lower()

    if system == "linux":
        cmd = ["ping", "-c", str(count), "-W", str(timeout), host]
    elif system == "darwin":
        cmd = ["ping", "-c", str(count), "-t", str(timeout), host]
    else:
        cmd = ["ping", "-n", str(count), host]

    try:
        start = time.monotonic()
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout * count + 5,
        )
        elapsed = time.monotonic() - start
        output = result.stdout + result.stderr

        loss_percent, avg_ms, jitter_ms = _parse_ping_output(output, count)

        reachable = loss_percent < 100.0
        return PingResult(
            host=host,
            reachable=reachable,
            latency_ms=avg_ms if reachable else None,
            packet_loss_percent=loss_percent,
            jitter_ms=jitter_ms,
        )
    except subprocess.TimeoutExpired:
        return PingResult(
            host=host,
            reachable=False,
            latency_ms=None,
            packet_loss_percent=100.0,
            jitter_ms=None,
            error="ping timed out",
        )
    except FileNotFoundError:
        return PingResult(
            host=host,
            reachable=False,
            latency_ms=None,
            packet_loss_percent=100.0,
            jitter_ms=None,
            error="ping command not found",
        )
    except Exception as exc:
        return PingResult(
            host=host,
            reachable=False,
            latency_ms=None,
            packet_loss_percent=100.0,
            jitter_ms=None,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# DNS resolution
# ---------------------------------------------------------------------------


def resolve_dns(
    hostname: str,
    nameserver: Optional[str] = None,
    timeout: float = 5.0,
) -> DnsResult:
    """
    Resolve a hostname using dnspython.
    Optionally use a specific nameserver.
    """
    resolver = dns.resolver.Resolver()
    resolver.lifetime = timeout
    resolver.timeout = timeout

    if nameserver:
        resolver.nameservers = [nameserver]

    start = time.monotonic()
    try:
        answer = resolver.resolve(hostname, "A")
        elapsed_ms = (time.monotonic() - start) * 1000
        ips = [rdata.address for rdata in answer]
        return DnsResult(
            host=hostname,
            resolved=True,
            resolution_ms=elapsed_ms,
            resolved_ips=ips,
        )
    except dns.exception.Timeout:
        return DnsResult(
            host=hostname,
            resolved=False,
            resolution_ms=None,
            error="DNS timeout",
        )
    except dns.resolver.NXDOMAIN:
        return DnsResult(
            host=hostname,
            resolved=False,
            resolution_ms=None,
            error="NXDOMAIN",
        )
    except dns.resolver.NoAnswer:
        return DnsResult(
            host=hostname,
            resolved=False,
            resolution_ms=None,
            error="No answer",
        )
    except Exception as exc:
        return DnsResult(
            host=hostname,
            resolved=False,
            resolution_ms=None,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Public IP detection
# ---------------------------------------------------------------------------


def get_public_ip(
    providers: list[str],
    timeout: int = 10,
) -> Optional[str]:
    """Try each provider in turn and return the first successful result."""
    import urllib.request
    import json

    for url in providers:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
                ip = data.get("ip")
                if ip:
                    return ip
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------


class NetworkMonitor:
    """
    Executes a full measurement round for all configured targets.
    """

    def __init__(
        self,
        ping_count: int = 3,
        ping_timeout: int = 5,
        dns_timeout: float = 5.0,
    ) -> None:
        self.ping_count = ping_count
        self.ping_timeout = ping_timeout
        self.dns_timeout = dns_timeout
        self._gateway: Optional[str] = None
        self._interface: Optional[str] = None
        self._public_ipv4: Optional[str] = None
        self._public_ipv6: Optional[str] = None

    # ------------------------------------------------------------------

    def refresh_gateway(self) -> None:
        gw = detect_gateway()
        if gw:
            self._gateway = gw
        iface = detect_network_interface()
        if iface:
            self._interface = iface

    @property
    def gateway(self) -> Optional[str]:
        if not self._gateway:
            self.refresh_gateway()
        return self._gateway

    @property
    def interface(self) -> Optional[str]:
        if not self._interface:
            self.refresh_gateway()
        return self._interface

    def set_public_ips(
        self,
        ipv4: Optional[str],
        ipv6: Optional[str],
    ) -> None:
        self._public_ipv4 = ipv4
        self._public_ipv6 = ipv6

    # ------------------------------------------------------------------

    def measure_target(
        self,
        target_name: str,
        target_host: str,
        target_type: str,
        gw_reachable: Optional[bool] = None,
    ) -> MeasurementResult:
        """
        Run a single measurement for one target.
        Returns a MeasurementResult with all available metrics.

        gw_reachable: pre-computed gateway reachability for this measurement
        cycle (measured once via measure_all(), not per-target). If None,
        falls back to pinging the gateway here (used by callers that measure
        a single target in isolation).
        """
        now = datetime.now(timezone.utc).isoformat()
        gw = self.gateway

        # Resolve "auto" gateway target
        host = gw if target_host == "auto" and gw else target_host

        if not host:
            return MeasurementResult(
                timestamp=now,
                target_name=target_name,
                target_host=target_host,
                target_type=target_type,
                reachable=False,
                latency_ms=None,
                packet_loss_percent=100.0,
                jitter_ms=None,
                dns_resolution_ms=None,
                gateway_reachable=False,
                public_ipv4=self._public_ipv4,
                public_ipv6=self._public_ipv6,
                error_message="Could not resolve host address",
            )

        # Gateway reachability — reuse pre-computed value if supplied,
        # otherwise measure it ourselves (legacy / single-target path).
        if gw_reachable is None:
            gw_reachable = False
            if gw:
                gw_ping = ping(gw, count=1, timeout=self.ping_timeout)
                gw_reachable = gw_ping.reachable

        if target_type == "dns":
            # DNS measurement: resolve the hostname
            dns_result = resolve_dns(host, timeout=self.dns_timeout)
            # Also ping if it's a public domain (to get latency)
            ping_result = None
            if target_type == "dns" and "." in host:
                # Try to get latency via first resolved IP
                if dns_result.resolved and dns_result.resolved_ips:
                    ping_result = ping(
                        dns_result.resolved_ips[0],
                        count=self.ping_count,
                        timeout=self.ping_timeout,
                    )

            return MeasurementResult(
                timestamp=now,
                target_name=target_name,
                target_host=host,
                target_type=target_type,
                reachable=dns_result.resolved,
                latency_ms=ping_result.latency_ms if ping_result else None,
                packet_loss_percent=ping_result.packet_loss_percent if ping_result else 0.0,
                jitter_ms=ping_result.jitter_ms if ping_result else None,
                dns_resolution_ms=dns_result.resolution_ms,
                gateway_reachable=gw_reachable,
                public_ipv4=self._public_ipv4,
                public_ipv6=self._public_ipv6,
                error_message=dns_result.error,
            )
        else:
            # ICMP / gateway ping
            ping_result = ping(host, count=self.ping_count, timeout=self.ping_timeout)
            return MeasurementResult(
                timestamp=now,
                target_name=target_name,
                target_host=host,
                target_type=target_type,
                reachable=ping_result.reachable,
                latency_ms=ping_result.latency_ms,
                packet_loss_percent=ping_result.packet_loss_percent,
                jitter_ms=ping_result.jitter_ms,
                dns_resolution_ms=None,
                gateway_reachable=gw_reachable,
                public_ipv4=self._public_ipv4,
                public_ipv6=self._public_ipv6,
                error_message=ping_result.error,
            )

    def measure_all(
        self,
        targets: list[tuple[str, str, str]],  # (name, host, type)
        max_workers: int = 8,
    ) -> list[MeasurementResult]:
        """
        Measure all targets in parallel.

        The gateway is pinged exactly once per cycle (not once per target —
        previously this caused N redundant gateway pings per 5s cycle, which
        under Pi CPU/IO load could push the whole cycle well past the
        configured interval and produce false-positive timeouts that looked
        like an ISP outage but were actually local resource contention).

        Targets are measured concurrently via a thread pool, since each
        measurement mostly waits on a ping/dns subprocess (I/O-bound), so
        threading is sufficient without rewriting this as asyncio.
        """
        gw = self.gateway
        gw_reachable = False
        if gw:
            gw_ping = ping(gw, count=1, timeout=self.ping_timeout)
            gw_reachable = gw_ping.reachable

        results: list[MeasurementResult] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_map = {
                pool.submit(
                    self.measure_target, name, host, ttype, gw_reachable
                ): (name, host, ttype)
                for name, host, ttype in targets
            }
            for future in as_completed(future_map):
                name, host, ttype = future_map[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    logger.error("Unexpected error measuring %s: %s", name, exc)
                    now = datetime.now(timezone.utc).isoformat()
                    results.append(
                        MeasurementResult(
                            timestamp=now,
                            target_name=name,
                            target_host=host,
                            target_type=ttype,
                            reachable=False,
                            latency_ms=None,
                            packet_loss_percent=100.0,
                            jitter_ms=None,
                            dns_resolution_ms=None,
                            gateway_reachable=gw_reachable,
                            public_ipv4=self._public_ipv4,
                            public_ipv6=self._public_ipv6,
                            error_message=str(exc),
                        )
                    )

        # Preserve original target order for downstream consumers/tests.
        # Keyed by target_name only — target_host may have been resolved
        # from "auto" to a concrete gateway IP inside measure_target().
        order = {name: i for i, (name, _, _) in enumerate(targets)}
        results.sort(key=lambda r: order.get(r.target_name, 0))
        return results
