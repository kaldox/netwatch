"""
NetWatch – Notification dispatcher.
Supports Telegram and SMTP email.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from .classifier import EventType, NetworkEvent
from .config import NotificationsConfig
from .statistics import format_duration

logger = logging.getLogger(__name__)


class Notifier:
    """Send notifications via Telegram and/or email."""

    def __init__(self, cfg: NotificationsConfig) -> None:
        self.cfg = cfg

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def notify_event_opened(self, event: NetworkEvent) -> None:
        self._dispatch(self._subject_opened(event), self._body_opened(event),
                       telegram_text=self._telegram_text(event) if self._telegram_wants(event, closed=False) else None)

    def notify_event_closed(self, event: NetworkEvent) -> None:
        self._dispatch(self._subject_closed(event), self._body_closed(event),
                       telegram_text=self._telegram_text(event) if self._telegram_wants(event, closed=True) else None)

    # ------------------------------------------------------------------
    # Telegram: filter + short message
    # ------------------------------------------------------------------

    def _telegram_wants(self, event: NetworkEvent, closed: bool) -> bool:
        """Telegram filter from the config (only_closed / event_types / min_duration_seconds)."""
        tg = self.cfg.telegram
        if not tg.enabled:
            return False
        if tg.only_closed and not closed:
            return False
        if tg.event_types and event.event_type.value not in tg.event_types:
            return False
        if closed and (event.duration_seconds or 0) < tg.min_duration_seconds:
            return False
        return True

    def _telegram_text(self, event: NetworkEvent) -> str:
        """Short message in plain German, local times, e.g.
        "✅ Internet wieder da – war 5 min 35 s weg" / "22:11–22:17 Uhr · Ursache: Leitung/Provider"."""
        label = _TYPE_LABEL.get(event.event_type, event.event_type.value)
        outage = event.event_type in _OUTAGE_TYPES
        start = _local_hhmm(event.started_at)
        if event.is_open:
            head = f"{_type_emoji(event.event_type)} " + (f"Internet weg seit {start} Uhr" if outage
                                                          else f"{label} seit {start} Uhr")
            return f"{head}\nUrsache: {label}"
        dur = _dauer(event.duration_seconds or 0)
        head = f"✅ Internet wieder da – war {dur} weg" if outage else f"✅ {label} vorbei ({dur})"
        lines = [head, f"{start}–{_local_hhmm(event.ended_at)} Uhr · Ursache: {label}"]
        before, after = event.public_ipv4_before, event.public_ipv4_after
        if before and after and before != after:
            lines.append(f"Neue öffentliche IP: {before} → {after} (Verbindung wurde neu aufgebaut)")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Message construction
    # ------------------------------------------------------------------

    def _subject_opened(self, event: NetworkEvent) -> str:
        emoji = _type_emoji(event.event_type)
        return f"{emoji} NetWatch ALARM: {event.event_type.value} erkannt"

    def _subject_closed(self, event: NetworkEvent) -> str:
        dur = format_duration(event.duration_seconds or 0)
        return f"✅ NetWatch BEHOBEN: {event.event_type.value} (Dauer: {dur})"

    def _body_opened(self, event: NetworkEvent) -> str:
        lines = [
            f"🔴 NETZWERKALARM",
            f"",
            f"Typ:        {event.event_type.value}",
            f"Zeitpunkt:  {event.started_at}",
            f"Confidence: {event.confidence_score:.0%}",
            f"",
            f"Beschreibung:",
            f"  {event.description}",
            f"",
            f"Host:       {event.hostname}",
            f"Gateway:    {event.gateway_ip or 'unbekannt'}",
            f"IPv4 vorher:{event.public_ipv4_before or 'unbekannt'}",
            f"",
            f"Event ID: {event.event_id}",
        ]
        return "\n".join(lines)

    def _body_closed(self, event: NetworkEvent) -> str:
        dur = format_duration(event.duration_seconds or 0)
        lines = [
            f"✅ NETZWERK WIEDERHERGESTELLT",
            f"",
            f"Typ:        {event.event_type.value}",
            f"Beginn:     {event.started_at}",
            f"Ende:       {event.ended_at}",
            f"Dauer:      {dur}",
            f"Confidence: {event.confidence_score:.0%}",
            f"",
            f"IPv4 vorher:    {event.public_ipv4_before or 'unbekannt'}",
            f"IPv4 während:   {event.public_ipv4_during or 'unbekannt'}",
            f"IPv4 danach:    {event.public_ipv4_after or 'unbekannt'}",
            f"",
            f"Event ID: {event.event_id}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, subject: str, body: str, telegram_text: Optional[str] = None) -> None:
        if telegram_text:
            # In the background: right after an outage DNS/line often still hiccup, and the
            # measurement loop must not wait for Telegram.
            threading.Thread(target=self._send_telegram, args=(telegram_text,), daemon=True,
                             name="telegram").start()
        if self.cfg.email.enabled:
            self._send_email(subject, body)

    def _send_telegram(self, message: str, attempts: int = 5, pause: float = 20.0) -> bool:
        token = self.cfg.telegram.bot_token
        chat_id = self.cfg.telegram.chat_id
        if not token or not chat_id:
            logger.warning("Telegram config incomplete – skipping")
            return False

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode()
        for attempt in range(1, attempts + 1):
            try:
                req = urllib.request.Request(url, data=payload, method="POST")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        logger.info("Telegram notification sent")
                        return True
                    logger.warning("Telegram returned HTTP %s", resp.status)
            except Exception as exc:
                logger.warning("Telegram attempt %d/%d failed: %s", attempt, attempts,
                               str(exc).replace(token, "***"))
            if attempt < attempts:
                time.sleep(pause)
        logger.error("Telegram notification not delivered after %d attempts", attempts)
        return False

    def _send_email(self, subject: str, body: str) -> None:
        cfg = self.cfg.email
        if not cfg.smtp_host or not cfg.to_addr:
            logger.warning("Email config incomplete – skipping")
            return

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = cfg.from_addr
        msg["To"] = cfg.to_addr
        msg.attach(MIMEText(body, "plain", "utf-8"))

        try:
            if cfg.use_tls:
                context = ssl.create_default_context()
                with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15) as server:
                    server.starttls(context=context)
                    if cfg.username:
                        server.login(cfg.username, cfg.password)
                    server.sendmail(cfg.from_addr, cfg.to_addr, msg.as_string())
            else:
                with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15) as server:
                    if cfg.username:
                        server.login(cfg.username, cfg.password)
                    server.sendmail(cfg.from_addr, cfg.to_addr, msg.as_string())
            logger.info("Email notification sent to %s", cfg.to_addr)
        except Exception as exc:
            logger.error("Failed to send email notification: %s", exc)


# Plain-language cause for the short Telegram message
_TYPE_LABEL = {
    EventType.ISP_FAILURE: "Leitung/Provider",
    EventType.LOCAL_NETWORK_FAILURE: "Heimnetz (Router/Kabel)",
    EventType.ROUTING_FAILURE: "Routing beim Provider",
    EventType.DNS_FAILURE: "Namensauflösung (DNS)",
    EventType.PACKET_LOSS: "Paketverlust",
    EventType.LATENCY_DEGRADATION: "hohe Latenz",
}
# Event types where "the internet is gone" is the right wording
_OUTAGE_TYPES = {EventType.ISP_FAILURE, EventType.LOCAL_NETWORK_FAILURE, EventType.ROUTING_FAILURE}


def _dauer(seconds: float) -> str:
    """Readable German duration for the chat: "35 s", "5 min 35 s", "1 h 12 min"."""
    s = int(round(seconds))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60} s"
    return f"{s // 3600} h {(s % 3600) // 60} min"


def _local_hhmm(iso: Optional[str]) -> str:
    """ISO timestamp (UTC in the DB) → local HH:MM (container TZ, e.g. Europe/Zurich)."""
    if not iso:
        return "?"
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%H:%M")
    except ValueError:
        return iso[11:16]


def _type_emoji(event_type: EventType) -> str:
    mapping = {
        EventType.LOCAL_NETWORK_FAILURE: "🔴",
        EventType.ISP_FAILURE: "🌐",
        EventType.DNS_FAILURE: "🔍",
        EventType.LATENCY_DEGRADATION: "⏱️",
        EventType.PACKET_LOSS: "📦",
        EventType.ROUTING_FAILURE: "🔀",
        EventType.RECOVERED: "✅",
        EventType.UNKNOWN: "❓",
    }
    return mapping.get(event_type, "⚠️")
