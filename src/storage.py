"""
NetWatch – Storage helpers.
CSV export, rotating logs setup, evidence archive.
"""

from __future__ import annotations

import csv
import logging
import logging.handlers
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def setup_logging(
    logs_dir: Path,
    level: str = "INFO",
    max_bytes: int = 10_485_760,
    backup_count: int = 10,
) -> None:
    """
    Configure rotating file handlers for events, debug, and errors.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(numeric_level)
    ch.setFormatter(formatter)
    root.addHandler(ch)

    # events.log – INFO and above
    events_handler = logging.handlers.RotatingFileHandler(
        logs_dir / "events.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    events_handler.setLevel(logging.INFO)
    events_handler.setFormatter(formatter)
    root.addHandler(events_handler)

    # debug.log – DEBUG and above
    debug_handler = logging.handlers.RotatingFileHandler(
        logs_dir / "debug.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(formatter)
    root.addHandler(debug_handler)

    # errors.log – ERROR and above
    error_handler = logging.handlers.RotatingFileHandler(
        logs_dir / "errors.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    root.addHandler(error_handler)


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

_MEASUREMENT_HEADERS = [
    "timestamp", "target_name", "target_host", "target_type",
    "reachable", "latency_ms", "packet_loss_percent", "jitter_ms",
    "dns_resolution_ms", "public_ipv4", "public_ipv6",
    "gateway_reachable", "error_message",
]

_EVENT_HEADERS = [
    "event_id", "event_type", "started_at", "ended_at",
    "duration_seconds", "confidence_score", "description",
    "public_ipv4_before", "public_ipv4_during", "public_ipv4_after",
    "gateway_ip", "hostname",
]


def export_measurements_csv(
    rows: list[dict[str, Any]],
    output_path: Path,
) -> None:
    """Write measurement rows to a CSV file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=_MEASUREMENT_HEADERS,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def export_events_csv(
    rows: list[dict[str, Any]],
    output_path: Path,
) -> None:
    """Write event rows to a CSV file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=_EVENT_HEADERS,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def export_daily_csv(
    rows: list[dict[str, Any]],
    output_path: Path,
) -> None:
    """Write daily statistics to CSV."""
    if not rows:
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Evidence archive
# ---------------------------------------------------------------------------


def write_evidence_file(
    data_dir: Path,
    event_id: str,
    content: str,
    suffix: str = "txt",
) -> Path:
    """
    Write an evidence text file for a specific event.
    Files are never overwritten – timestamp is included in the name.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence_dir = data_dir / "evidence" / event_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / f"{ts}.{suffix}"
    path.write_text(content, encoding="utf-8")
    return path
