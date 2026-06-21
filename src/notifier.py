"""
NetWatch – Notification dispatcher.
Supports Telegram and SMTP email.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import urllib.parse
import urllib.request
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
        subject = self._subject_opened(event)
        body = self._body_opened(event)
        self._dispatch(subject, body)

    def notify_event_closed(self, event: NetworkEvent) -> None:
        subject = self._subject_closed(event)
        body = self._body_closed(event)
        self._dispatch(subject, body)

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

    def _dispatch(self, subject: str, body: str) -> None:
        if self.cfg.telegram.enabled:
            self._send_telegram(f"{subject}\n\n{body}")
        if self.cfg.email.enabled:
            self._send_email(subject, body)

    def _send_telegram(self, message: str) -> None:
        token = self.cfg.telegram.bot_token
        chat_id = self.cfg.telegram.chat_id
        if not token or not chat_id:
            logger.warning("Telegram config incomplete – skipping")
            return

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = urllib.parse.urlencode(
            {"chat_id": chat_id, "text": message, "parse_mode": ""}
        ).encode()
        try:
            req = urllib.request.Request(url, data=payload, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    logger.info("Telegram notification sent")
                else:
                    logger.warning("Telegram returned HTTP %s", resp.status)
        except Exception as exc:
            logger.error("Failed to send Telegram notification: %s", exc)

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
