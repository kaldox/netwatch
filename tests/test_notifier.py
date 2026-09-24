"""Telegram filter + short message (2026-09-24): one message after a real outage, nothing for blips."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.notifier as notifier_mod
from src.classifier import EventType, NetworkEvent
from src.config import NotificationsConfig, TelegramConfig
from src.notifier import Notifier


def _event(etype=EventType.ISP_FAILURE, start="2026-09-22T20:11:00+00:00", end="2026-09-22T20:16:35+00:00",
           before="203.0.113.1", after="203.0.113.1"):
    return NetworkEvent(
        event_id="e1", event_type=etype, started_at=start, ended_at=end, confidence_score=0.9,
        description="", public_ipv4_before=before, public_ipv4_during=None, public_ipv4_after=after,
        public_ipv6_before=None, public_ipv6_during=None, public_ipv6_after=None,
        gateway_ip="192.168.178.1", hostname="pi", network_interface="eth0",
    )


def _notifier(**tg):
    base = dict(enabled=True, bot_token="t", chat_id="1", only_closed=True,
                event_types=["ISP_FAILURE", "LOCAL_NETWORK_FAILURE"], min_duration_seconds=60)
    base.update(tg)
    return Notifier(NotificationsConfig(telegram=TelegramConfig(**base)))


def test_filter():
    n = _notifier()
    assert n._telegram_wants(_event(), closed=True)
    assert not n._telegram_wants(_event(end=None), closed=False)                          # only_closed
    assert not n._telegram_wants(_event(EventType.LATENCY_DEGRADATION), closed=True)      # type filter
    assert not n._telegram_wants(_event(end="2026-09-22T20:11:30+00:00"), closed=True)    # 30 s < 60 s
    assert not _notifier(enabled=False)._telegram_wants(_event(), closed=True)
    assert _notifier(only_closed=False, event_types=[], min_duration_seconds=0)._telegram_wants(
        _event(EventType.PACKET_LOSS, end=None), closed=False)


def test_message_text(monkeypatch):
    monkeypatch.setattr(notifier_mod, "_local_hhmm", lambda iso: (iso or "?")[11:16])
    text = _notifier()._telegram_text(_event())
    assert text.splitlines()[0] == "✅ Internet wieder da – war 5 min 35 s weg"
    assert "20:11–20:16 Uhr · Ursache: Leitung/Provider" in text
    assert "Neue öffentliche IP" not in text
    text = _notifier()._telegram_text(_event(after="203.0.113.9"))
    assert "Neue öffentliche IP: 203.0.113.1 → 203.0.113.9" in text


def test_closed_event_is_sent_in_background_once(monkeypatch):
    sent = []
    n = _notifier()
    monkeypatch.setattr(n, "_send_telegram", lambda text: sent.append(text))
    started = []
    real_thread = notifier_mod.threading.Thread

    class ImmediateThread(real_thread):   # run inline so the test can check the result
        def start(self):
            started.append(1)
            self.run()
    monkeypatch.setattr(notifier_mod.threading, "Thread", ImmediateThread)
    n.notify_event_opened(_event(end=None))      # filtered: only_closed
    n.notify_event_closed(_event())
    assert started == [1] and len(sent) == 1 and sent[0].startswith("✅ Internet wieder da")


def test_send_retries_until_success(monkeypatch):
    attempts = []

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=10):
        attempts.append(1)
        if len(attempts) < 3:
            raise OSError("Temporary failure in name resolution")
        return Resp()
    monkeypatch.setattr(notifier_mod.urllib.request, "urlopen", fake_urlopen)
    assert _notifier()._send_telegram("x", attempts=5, pause=0) is True
    assert len(attempts) == 3


def test_token_from_environment(monkeypatch, tmp_path):
    from src.config import load_config
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("notifications:\n  telegram:\n    enabled: true\n", encoding="utf-8")
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("CHAT_ID", "42")
    tg = load_config(cfg_file).notifications.telegram
    assert (tg.bot_token, tg.chat_id) == ("123:abc", "42")
