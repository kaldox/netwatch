"""
NetWatch – FritzBox TR-064 line-data reader.

Reads the WAN/DSL sync rates and connection status from an AVM FritzBox
over its TR-064 / UPnP-IGD interface. These endpoints are readable without
authentication on a default FritzBox configuration (they expose only WAN
line status, no private data).

Why this matters for evidence: the FritzBox sits at the demarcation point
between the local network and the ISP. Its negotiated sync rate is the
physical line capacity the ISP agreed to deliver. If a speed test measures
far below the sync rate while the line is "Up" with no errors, the
bottleneck is provably upstream of the FritzBox — i.e. in the ISP's
network — and cannot have been caused by the Raspberry Pi or anything in
the local network. This is the independent corroboration that rules out the
Pi as the source of the problem.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 8


@dataclass
class FritzBoxStatus:
    reachable: bool
    # Negotiated line sync rates (bits per second)
    downstream_sync_bps: Optional[int] = None
    upstream_sync_bps: Optional[int] = None
    physical_link_status: Optional[str] = None      # Up / Down
    wan_access_type: Optional[str] = None           # DSL / ...
    # DSL link
    dsl_link_type: Optional[str] = None             # PPPoE / ...
    dsl_link_status: Optional[str] = None           # Up / Down
    # WAN/PPP connection
    connection_status: Optional[str] = None         # Connected / ...
    last_connection_error: Optional[str] = None     # ERROR_NONE / ...
    wan_uptime_seconds: Optional[int] = None
    # Extended DSL diagnostics (require authentication)
    dsl_downstream_curr_kbps: Optional[int] = None  # current sync, kbit/s
    dsl_upstream_curr_kbps: Optional[int] = None
    dsl_downstream_max_kbps: Optional[int] = None    # physical max attainable
    dsl_upstream_max_kbps: Optional[int] = None
    dsl_downstream_noise_margin_db: Optional[float] = None  # SNR margin
    dsl_upstream_noise_margin_db: Optional[float] = None
    dsl_downstream_attenuation_db: Optional[float] = None
    dsl_upstream_attenuation_db: Optional[float] = None
    error: Optional[str] = None

    @property
    def downstream_sync_mbps(self) -> Optional[float]:
        return round(self.downstream_sync_bps / 1_000_000, 2) if self.downstream_sync_bps else None

    @property
    def upstream_sync_mbps(self) -> Optional[float]:
        return round(self.upstream_sync_bps / 1_000_000, 2) if self.upstream_sync_bps else None

    @property
    def dsl_downstream_max_mbps(self) -> Optional[float]:
        return round(self.dsl_downstream_max_kbps / 1000, 2) if self.dsl_downstream_max_kbps else None

    @property
    def dsl_upstream_max_mbps(self) -> Optional[float]:
        return round(self.dsl_upstream_max_kbps / 1000, 2) if self.dsl_upstream_max_kbps else None


@dataclass
class FritzLogEntry:
    timestamp: str          # ISO-ish "2026-06-21T11:04:02" reconstructed from date+time
    raw_date: str           # "21.06.26"
    raw_time: str           # "11:04:02"
    group: str              # net / sys / fon / wlan / usb
    message_id: int
    message: str
    # Derived classification
    category: str           # sync_change | disconnect | cabling_issue | reconnect | other
    sync_down_kbps: Optional[int] = None
    sync_up_kbps: Optional[int] = None
    cabling_cost_kbps: Optional[int] = None


@dataclass
class FritzLogResult:
    reachable: bool
    entries: list[FritzLogEntry] = field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# SOAP helpers
# ---------------------------------------------------------------------------

_SOAP_TEMPLATE = (
    '<?xml version="1.0"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    '<s:Body><u:{action} xmlns:u="{service}"></u:{action}></s:Body>'
    '</s:Envelope>'
)


def _soap_call(
    host: str,
    control_url: str,
    service: str,
    action: str,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Optional[str]:
    """Issue a TR-064 SOAP call and return the raw XML response body."""
    url = f"http://{host}:49000{control_url}"
    headers = {
        "Content-Type": 'text/xml; charset="utf-8"',
        "SoapAction": f"{service}#{action}",
    }
    body = _SOAP_TEMPLATE.format(action=action, service=service)
    try:
        resp = requests.post(url, headers=headers, data=body, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        logger.debug("FritzBox SOAP call %s failed: %s", action, exc)
        return None


def _soap_call_auth(
    host: str,
    control_url: str,
    service: str,
    action: str,
    username: str,
    password: str,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Optional[str]:
    """
    Issue an authenticated TR-064 SOAP call using HTTP Digest auth.
    Used for endpoints that expose extended DSL diagnostics, which the
    FritzBox protects behind a login.
    """
    from requests.auth import HTTPDigestAuth

    url = f"http://{host}:49000{control_url}"
    headers = {
        "Content-Type": 'text/xml; charset="utf-8"',
        "SoapAction": f"{service}#{action}",
    }
    body = _SOAP_TEMPLATE.format(action=action, service=service)
    try:
        resp = requests.post(
            url, headers=headers, data=body, timeout=timeout,
            auth=HTTPDigestAuth(username, password),
        )
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        logger.debug("FritzBox authenticated SOAP call %s failed: %s", action, exc)
        return None


def _extract(xml: str, tag: str) -> Optional[str]:
    """Pull a single tag's text out of a SOAP response."""
    m = re.search(rf"<{tag}>(.*?)</{tag}>", xml, re.DOTALL)
    return m.group(1).strip() if m else None


def _extract_int(xml: str, tag: str) -> Optional[int]:
    val = _extract(xml, tag)
    if val is None:
        return None
    try:
        return int(val)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def read_fritzbox_status(
    host: str,
    timeout: int = _DEFAULT_TIMEOUT,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> FritzBoxStatus:
    """
    Read WAN/DSL line status from a FritzBox over TR-064.

    Returns a FritzBoxStatus with whatever could be read; reachable=False
    if the box did not respond to any query (so a missing/unreachable box
    never blocks the monitoring loop — it just records "no FritzBox data").

    If username/password are supplied, also reads the extended DSL
    diagnostics (current/max sync, SNR margin, attenuation) which the
    FritzBox protects behind authentication. These reveal *why* the line
    syncs where it does — e.g. a physical max-attainable rate below the
    contracted speed, which is hard technical evidence the line cannot
    deliver what was sold.
    """
    any_response = False

    # 1) Common link properties: negotiated sync rates + physical status
    down_bps = up_bps = None
    phys_status = wan_access = None
    xml = _soap_call(
        host,
        "/igdupnp/control/WANCommonIFC1",
        "urn:schemas-upnp-org:service:WANCommonInterfaceConfig:1",
        "GetCommonLinkProperties",
        timeout,
    )
    if xml:
        any_response = True
        down_bps = _extract_int(xml, "NewLayer1DownstreamMaxBitRate")
        up_bps = _extract_int(xml, "NewLayer1UpstreamMaxBitRate")
        phys_status = _extract(xml, "NewPhysicalLinkStatus")
        wan_access = _extract(xml, "NewWANAccessType")

    # 2) DSL link info: link type + status
    dsl_type = dsl_status = None
    xml = _soap_call(
        host,
        "/igdupnp/control/WANDSLLinkC1",
        "urn:schemas-upnp-org:service:WANDSLLinkConfig:1",
        "GetDSLLinkInfo",
        timeout,
    )
    if xml:
        any_response = True
        dsl_type = _extract(xml, "NewLinkType")
        dsl_status = _extract(xml, "NewLinkStatus")

    # 3) WAN connection status: connection state + uptime + last error
    conn_status = last_error = None
    uptime = None
    xml = _soap_call(
        host,
        "/igdupnp/control/WANIPConn1",
        "urn:schemas-upnp-org:service:WANIPConnection:1",
        "GetStatusInfo",
        timeout,
    )
    if xml:
        any_response = True
        conn_status = _extract(xml, "NewConnectionStatus")
        last_error = _extract(xml, "NewLastConnectionError")
        uptime = _extract_int(xml, "NewUptime")

    # 4) Extended DSL diagnostics (authenticated). The FritzBox reports
    #    rates in kbit/s and margins/attenuation in tenths of a dB.
    dsl_down_curr = dsl_up_curr = dsl_down_max = dsl_up_max = None
    dsl_down_snr = dsl_up_snr = dsl_down_att = dsl_up_att = None
    if username is not None and password is not None:
        xml = _soap_call_auth(
            host,
            "/upnp/control/wandslifconfig1",
            "urn:dslforum-org:service:WANDSLInterfaceConfig:1",
            "GetInfo",
            username,
            password,
            timeout,
        )
        if xml:
            any_response = True
            dsl_down_curr = _extract_int(xml, "NewDownstreamCurrRate")
            dsl_up_curr = _extract_int(xml, "NewUpstreamCurrRate")
            dsl_down_max = _extract_int(xml, "NewDownstreamMaxRate")
            dsl_up_max = _extract_int(xml, "NewUpstreamMaxRate")
            # tenths of dB -> dB
            snr_d = _extract_int(xml, "NewDownstreamNoiseMargin")
            snr_u = _extract_int(xml, "NewUpstreamNoiseMargin")
            att_d = _extract_int(xml, "NewDownstreamAttenuation")
            att_u = _extract_int(xml, "NewUpstreamAttenuation")
            dsl_down_snr = round(snr_d / 10, 1) if snr_d is not None else None
            dsl_up_snr = round(snr_u / 10, 1) if snr_u is not None else None
            dsl_down_att = round(att_d / 10, 1) if att_d is not None else None
            dsl_up_att = round(att_u / 10, 1) if att_u is not None else None

    if not any_response:
        return FritzBoxStatus(
            reachable=False,
            error="FritzBox did not respond on TR-064 (port 49000)",
        )

    return FritzBoxStatus(
        reachable=True,
        downstream_sync_bps=down_bps,
        upstream_sync_bps=up_bps,
        physical_link_status=phys_status,
        wan_access_type=wan_access,
        dsl_link_type=dsl_type,
        dsl_link_status=dsl_status,
        connection_status=conn_status,
        last_connection_error=last_error,
        wan_uptime_seconds=uptime,
        dsl_downstream_curr_kbps=dsl_down_curr,
        dsl_upstream_curr_kbps=dsl_up_curr,
        dsl_downstream_max_kbps=dsl_down_max,
        dsl_upstream_max_kbps=dsl_up_max,
        dsl_downstream_noise_margin_db=dsl_down_snr,
        dsl_upstream_noise_margin_db=dsl_up_snr,
        dsl_downstream_attenuation_db=dsl_down_att,
        dsl_upstream_attenuation_db=dsl_up_att,
    )


# ---------------------------------------------------------------------------
# Session login + event-log reading (internal data.lua interface)
# ---------------------------------------------------------------------------
#
# The FritzBox event log is NOT exposed via TR-064; it requires a session ID
# obtained through the challenge-response login used by the web UI. This is
# more fragile than TR-064 (the login flow and log format vary across FritzOS
# versions) so every step degrades gracefully: any failure returns an empty
# result rather than raising, so the monitoring loop is never blocked.


def _compute_challenge_response(challenge: str, password: str) -> str:
    """
    Classic md5 challenge-response: md5("<challenge>-<password>") over the
    UTF-16LE encoding of that string. Returns "<challenge>-<md5hex>".
    (Newer PBKDF2 logins are not handled here; if the box requires those,
    login simply fails and the log feature is skipped.)
    """
    raw = f"{challenge}-{password}".encode("utf-16-le")
    digest = hashlib.md5(raw).hexdigest()
    return f"{challenge}-{digest}"


def fritz_login(host: str, username: str, password: str, timeout: int = _DEFAULT_TIMEOUT) -> Optional[str]:
    """
    Perform a challenge-response login and return a session ID (SID), or
    None on failure. A SID of all zeros means authentication failed.
    """
    try:
        r = requests.get(f"http://{host}/login_sid.lua", timeout=timeout)
        r.raise_for_status()
        challenge = _extract(r.text, "Challenge")
        if not challenge:
            return None

        response = _compute_challenge_response(challenge, password)
        params = {"username": username, "response": response}
        r2 = requests.get(f"http://{host}/login_sid.lua", params=params, timeout=timeout)
        r2.raise_for_status()
        sid = _extract(r2.text, "SID")
        if not sid or sid == "0" * 16:
            logger.debug("FritzBox login failed (SID is null)")
            return None
        return sid
    except requests.RequestException as exc:
        logger.debug("FritzBox login error: %s", exc)
        return None


# Pre-compiled patterns for classifying log lines
_RE_SYNC = re.compile(r"DSL-Synchronisierung besteht mit\s+(\d+)/(\d+)\s*kbit", re.IGNORECASE)
_RE_SYNC2 = re.compile(r"DSL.*?(\d+)/(\d+)\s*kbit", re.IGNORECASE)
_RE_CABLING = re.compile(r"unzul.ssige Verkabelung|Abzweigung|Mehrfachverteilung", re.IGNORECASE)
_RE_CABLING_COST = re.compile(r"kostet ungef.hr\s+(\d+)\s*kbit", re.IGNORECASE)
_RE_DISCONNECT = re.compile(
    r"PPPoE-Fehler|LCP|Anmeldung beim Internetanbieter ist fehlgeschlagen|"
    r"Zeit.berschreitung bei der PPP|getrennt|unterbrochen",
    re.IGNORECASE,
)
_RE_RECONNECT = re.compile(r"Internetverbindung wurde erfolgreich hergestellt", re.IGNORECASE)


def _classify_log_entry(msg: str) -> tuple[str, Optional[int], Optional[int], Optional[int]]:
    """Return (category, sync_down_kbps, sync_up_kbps, cabling_cost_kbps)."""
    m = _RE_SYNC.search(msg) or _RE_SYNC2.search(msg)
    if "Synchronisierung besteht" in msg and m:
        return "sync_change", int(m.group(1)), int(m.group(2)), None

    if _RE_CABLING.search(msg):
        cost = None
        cm = _RE_CABLING_COST.search(msg)
        if cm:
            cost = int(cm.group(1))
        return "cabling_issue", None, None, cost

    if _RE_RECONNECT.search(msg):
        return "reconnect", None, None, None

    if _RE_DISCONNECT.search(msg):
        return "disconnect", None, None, None

    return "other", None, None, None


def _parse_fritz_date(date_str: str, time_str: str) -> str:
    """Convert FritzBox '21.06.26' + '11:04:02' to ISO-ish '2026-06-21T11:04:02'."""
    try:
        d, m, y = date_str.split(".")
        year = 2000 + int(y) if len(y) == 2 else int(y)
        return f"{year:04d}-{int(m):02d}-{int(d):02d}T{time_str}"
    except Exception:
        return f"{date_str} {time_str}"


def read_fritzbox_log(
    host: str,
    username: str,
    password: str,
    timeout: int = _DEFAULT_TIMEOUT,
) -> FritzLogResult:
    """
    Log in and fetch the 'net' event log, returning classified entries.
    Focuses on the events relevant to line-quality evidence: sync-rate
    changes, connection drops, and the FritzBox's own cabling-defect
    detection. Returns reachable=False on any failure.
    """
    sid = fritz_login(host, username, password, timeout)
    if not sid:
        return FritzLogResult(reachable=False, error="Login failed")

    import json

    try:
        r = requests.post(
            f"http://{host}/data.lua",
            data={"sid": sid, "page": "log", "filter": "net"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout,
        )
        r.raise_for_status()
        payload = json.loads(r.text)
    except (requests.RequestException, ValueError) as exc:
        logger.debug("FritzBox log fetch failed: %s", exc)
        return FritzLogResult(reachable=False, error=str(exc))

    raw_entries = []
    try:
        raw_entries = payload.get("data", {}).get("log", []) or []
    except AttributeError:
        raw_entries = []

    entries: list[FritzLogEntry] = []
    for item in raw_entries:
        # FritzOS returns either dicts (newer) or lists (older). Handle dicts.
        if isinstance(item, dict):
            date = item.get("date", "")
            time_ = item.get("time", "")
            msg = item.get("msg", "")
            group = item.get("group", "net")
            mid = item.get("id", 0)
        elif isinstance(item, list) and len(item) >= 3:
            # Older format: [date, time, msg, ...]
            date, time_, msg = item[0], item[1], item[2]
            group, mid = "net", 0
        else:
            continue

        category, sd, su, cost = _classify_log_entry(msg)
        try:
            mid_int = int(mid)
        except (ValueError, TypeError):
            mid_int = 0

        entries.append(FritzLogEntry(
            timestamp=_parse_fritz_date(date, time_),
            raw_date=date,
            raw_time=time_,
            group=group,
            message_id=mid_int,
            message=msg,
            category=category,
            sync_down_kbps=sd,
            sync_up_kbps=su,
            cabling_cost_kbps=cost,
        ))

    return FritzLogResult(reachable=True, entries=entries)
