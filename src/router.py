"""
NetWatch – Router abstraction (experimental).

NetWatch's strongest evidence comes from reading the router's own view of
the line (sync rate, line errors, connection drops) and comparing it to
measured throughput. The reference implementation targets the AVM FritzBox
over TR-064, which is well supported.

This module defines a small common interface so other routers can be added
without touching the rest of the codebase. Contributions welcome — see
CONTRIBUTING.md. Only the FritzBox provider is production-ready today;
others are stubs / community territory.

A router provider returns a RouterLineStatus, which the rest of NetWatch
treats uniformly regardless of vendor.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


@dataclass
class RouterLineStatus:
    """Vendor-neutral view of the WAN line, as the router reports it."""
    reachable: bool
    vendor: str = "unknown"
    # Negotiated sync rates (Mbit/s)
    downstream_sync_mbps: Optional[float] = None
    upstream_sync_mbps: Optional[float] = None
    # Physical max attainable (Mbit/s) — what the line could do at best
    downstream_max_mbps: Optional[float] = None
    upstream_max_mbps: Optional[float] = None
    # Line quality
    downstream_snr_db: Optional[float] = None
    downstream_attenuation_db: Optional[float] = None
    # Connection state
    physical_link_status: Optional[str] = None
    connection_status: Optional[str] = None
    last_connection_error: Optional[str] = None
    wan_uptime_seconds: Optional[int] = None
    error: Optional[str] = None


class RouterProvider(Protocol):
    """Interface every router integration must implement."""

    vendor: str

    def read_status(self) -> RouterLineStatus:
        ...


# ---------------------------------------------------------------------------
# FritzBox provider (production-ready reference implementation)
# ---------------------------------------------------------------------------

class FritzBoxProvider:
    vendor = "fritzbox"

    def __init__(self, host: str, username: str = "", password: str = "", timeout: int = 8):
        self.host = host
        self.username = username
        self.password = password
        self.timeout = timeout

    def read_status(self) -> RouterLineStatus:
        from .fritzbox import read_fritzbox_status
        fb = read_fritzbox_status(
            self.host,
            timeout=self.timeout,
            username=self.username or None,
            password=self.password or None,
        )
        return RouterLineStatus(
            reachable=fb.reachable,
            vendor="fritzbox",
            downstream_sync_mbps=fb.downstream_sync_mbps,
            upstream_sync_mbps=fb.upstream_sync_mbps,
            downstream_max_mbps=fb.dsl_downstream_max_mbps,
            upstream_max_mbps=fb.dsl_upstream_max_mbps,
            downstream_snr_db=fb.dsl_downstream_noise_margin_db,
            downstream_attenuation_db=fb.dsl_downstream_attenuation_db,
            physical_link_status=fb.physical_link_status,
            connection_status=fb.connection_status,
            last_connection_error=fb.last_connection_error,
            wan_uptime_seconds=fb.wan_uptime_seconds,
            error=fb.error,
        )


# ---------------------------------------------------------------------------
# Generic TR-064 provider (experimental — many routers speak TR-064)
# ---------------------------------------------------------------------------

class GenericTR064Provider:
    """
    Experimental: many non-AVM routers also expose TR-064 with the standard
    WANCommonInterfaceConfig service. This reads only the vendor-neutral
    sync rate + link status (no extended DSL diagnostics, which are
    vendor-specific). Untested across vendors — community contributions
    welcome to harden this.
    """
    vendor = "generic_tr064"

    def __init__(self, host: str, timeout: int = 8):
        self.host = host
        self.timeout = timeout

    def read_status(self) -> RouterLineStatus:
        from .fritzbox import _soap_call, _extract, _extract_int
        xml = _soap_call(
            self.host,
            "/igdupnp/control/WANCommonIFC1",
            "urn:schemas-upnp-org:service:WANCommonInterfaceConfig:1",
            "GetCommonLinkProperties",
            self.timeout,
        )
        if not xml:
            return RouterLineStatus(
                reachable=False, vendor="generic_tr064",
                error="No TR-064 response on port 49000",
            )
        down = _extract_int(xml, "NewLayer1DownstreamMaxBitRate")
        up = _extract_int(xml, "NewLayer1UpstreamMaxBitRate")
        phys = _extract(xml, "NewPhysicalLinkStatus")
        return RouterLineStatus(
            reachable=True,
            vendor="generic_tr064",
            downstream_sync_mbps=round(down / 1_000_000, 2) if down else None,
            upstream_sync_mbps=round(up / 1_000_000, 2) if up else None,
            physical_link_status=phys,
        )


# ---------------------------------------------------------------------------
# Null provider (no router integration — speedtest-only mode)
# ---------------------------------------------------------------------------

class NullProvider:
    """Used when no router integration is configured. NetWatch still does
    full reachability + speed monitoring; it just can't compare against the
    router's line sync."""
    vendor = "none"

    def read_status(self) -> RouterLineStatus:
        return RouterLineStatus(reachable=False, vendor="none")


def get_router_provider(cfg) -> RouterProvider:
    """
    Factory: pick a router provider based on config. Defaults to FritzBox
    for backwards compatibility. Set `fritzbox.vendor` in config to switch.
    """
    fb = cfg.fritzbox
    if not getattr(fb, "enabled", True):
        return NullProvider()

    vendor = getattr(fb, "vendor", "fritzbox").lower()
    if vendor == "fritzbox":
        return FritzBoxProvider(fb.host, fb.username, fb.password, fb.timeout_seconds)
    if vendor in ("generic", "tr064", "generic_tr064"):
        return GenericTR064Provider(fb.host, fb.timeout_seconds)
    return NullProvider()
