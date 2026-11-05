"""
NetWatch – Router-Störungen von Provider-Ausfällen unterscheiden.

Ein Provider-Ausfall (ISP_FAILURE) heisst: Router erreichbar, Internet nicht.
Genau das sieht NetWatch aber auch, wenn der Router selbst gestört ist – jemand
stösst an Kabel oder Stecker, der Router wird neu gestartet, die DSL-Leitung
synchronisiert danach neu. Bei einem echten Provider-Ausfall bleibt der Router
im Heimnetz durchgehend erreichbar. Fällt dagegen der Router selbst (das
Gateway) kurz vor dem Internet-Ausfall aus, liegt die Ursache zu Hause.

Regel: Ein ISP-Ausfall gilt als lokale Router-Störung, wenn
  * das Gateway in den ROUTER_FAULT_WINDOW_S Sekunden vor dem Beginn mindestens
    einmal nicht erreichbar war (Messungen) oder ein lokaler Ausfall in diesem
    Fenster endete (Ereignisse), und
  * der Ausfall höchstens ROUTER_FAULT_MAX_S Sekunden dauert (Neustart +
    DSL-Training + Einwahl dauern 1–3 Minuten). Länger bleibt es ein
    Provider-Ausfall – z. B. wenn man den Router während eines echten
    Ausfalls neu startet und das Internet danach immer noch fehlt.

Die Echtzeit-Erkennung steckt im Classifier; dieses Modul korrigiert
zusätzlich alte Ereignisse aus der Datenbank (beim Start von NetWatch).
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

ROUTER_FAULT_WINDOW_S = 60.0
ROUTER_FAULT_MAX_S = 300.0

ROUTER_FAULT_DESCRIPTION = (
    "Störung am Router: kurz vor dem Internet-Ausfall war der Router selbst nicht "
    "erreichbar (Neustart, Stecker, Kabel) – kein Provider-Ausfall"
)


def router_fault_reason(
    started_at: str,
    duration_seconds: Optional[float],
    gateway_failures: list[str],
    local_event_ends: list[str],
    window_s: float = ROUTER_FAULT_WINDOW_S,
    max_s: float = ROUTER_FAULT_MAX_S,
) -> Optional[str]:
    """Reason text if this ISP outage was caused by the router, else None.

    gateway_failures: timestamps of measurements with the gateway unreachable
    local_event_ends: end times of LOCAL_NETWORK_FAILURE events
    (both may contain values outside the window; they are filtered here)."""
    if duration_seconds is None or duration_seconds > max_s:
        return None
    start = datetime.fromisoformat(started_at)
    lo = start - timedelta(seconds=window_s)

    def _in_window(ts: str) -> bool:
        try:
            t = datetime.fromisoformat(ts)
        except (TypeError, ValueError):
            return False
        return lo <= t <= start + timedelta(seconds=1)

    ends = [t for t in local_event_ends if t and _in_window(t)]
    if ends:
        return f"lokaler Ausfall endete {max(ends)[11:19]} UTC, direkt vor dem Internet-Ausfall"
    fails = [t for t in gateway_failures if _in_window(t)]
    if fails:
        return f"Router {len(fails)}× nicht erreichbar zwischen {min(fails)[11:19]} und {max(fails)[11:19]} UTC"
    return None


def reclassify_history(db, window_s: float = ROUTER_FAULT_WINDOW_S,
                       max_s: float = ROUTER_FAULT_MAX_S) -> set[date]:
    """Re-check all closed ISP_FAILURE events and turn router faults into
    LOCAL_NETWORK_FAILURE. Returns the (UTC) days whose statistics changed."""
    days: set[date] = set()
    for ev in db.get_events(event_type="ISP_FAILURE", limit=100000):
        if not ev.get("ended_at"):
            continue
        try:
            start = datetime.fromisoformat(ev["started_at"])
            lo = (start - timedelta(seconds=window_s)).isoformat()
            hi = (start + timedelta(seconds=1)).isoformat()
            reason = router_fault_reason(
                ev["started_at"], ev.get("duration_seconds"),
                db.gateway_failures_between(lo, hi),
                db.local_event_ends_between(lo, hi),
                window_s, max_s,
            )
            if not reason:
                continue
            db.reclassify_event(ev["event_id"], "LOCAL_NETWORK_FAILURE",
                                ROUTER_FAULT_DESCRIPTION, _extra(ev, reason, "nachträglich"))
            logger.warning("ISP_FAILURE %s (%s, %.0fs) nachträglich als Router-Störung eingestuft: %s",
                           ev["event_id"][:8], ev["started_at"], ev.get("duration_seconds") or 0, reason)
            days.add(start.astimezone(timezone.utc).date())
        except Exception as exc:
            logger.error("Router-Prüfung für %s fehlgeschlagen: %s", ev.get("event_id"), exc)
    return days


def _extra(ev: dict[str, Any], reason: str, how: str) -> str:
    try:
        extra = json.loads(ev.get("extra_json") or "{}")
    except (TypeError, ValueError):
        extra = {}
    extra["router_fault"] = reason
    extra["router_fault_detected"] = how
    extra["original_event_type"] = ev.get("event_type")
    extra["original_description"] = ev.get("description")
    return json.dumps(extra, ensure_ascii=False)
