"""
NetWatch – Bandwidth speed testing.

Measures real download/upload throughput and latency using Cloudflare's
public speed-test endpoints (the same infrastructure speed.cloudflare.com
uses in the browser). No third-party CLI tool required — just HTTP.

This is a different kind of evidence than the ICMP/DNS reachability checks
elsewhere in NetWatch: it answers "how fast is my connection actually
performing right now", which is what you need to demonstrate throttling
(bandwidth that's technically "up" but degraded) rather than outright
outages.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_CF_BASE = "https://speed.cloudflare.com"
_USER_AGENT = "NetWatch/1.0 (+https://github.com/kaldox/netwatch)"

# Payload sizes used for the test, in bytes. Small sizes first to warm up
# the connection / get a quick latency reading, larger sizes to get a
# stable throughput reading without taking too long or using too much data.
_DOWNLOAD_SIZES = [101_000, 1_001_000, 10_001_000]  # ~100KB, 1MB, 10MB
_UPLOAD_SIZE = 1_001_000  # ~1MB


@dataclass
class SpeedTestResult:
    timestamp: str
    download_mbps: Optional[float]
    upload_mbps: Optional[float]
    latency_ms: Optional[float]
    jitter_ms: Optional[float]
    server: str
    success: bool
    error: Optional[str] = None


def _measure_latency(session: requests.Session, samples: int = 5) -> tuple[Optional[float], Optional[float]]:
    """Round-trip latency to the Cloudflare edge via tiny HTTP requests."""
    timings = []
    for _ in range(samples):
        try:
            start = time.perf_counter()
            resp = session.get(f"{_CF_BASE}/__down?bytes=0", timeout=5)
            resp.raise_for_status()
            timings.append((time.perf_counter() - start) * 1000)
        except requests.RequestException:
            continue
    if not timings:
        return None, None
    avg = sum(timings) / len(timings)
    jitter = (max(timings) - min(timings)) if len(timings) > 1 else 0.0
    return round(avg, 1), round(jitter, 1)


def _measure_download(session: requests.Session) -> Optional[float]:
    """
    Download payloads of increasing size and compute Mbit/s from the
    largest successful transfer (small transfers are dominated by
    connection setup time and understate real throughput).
    """
    best_mbps: Optional[float] = None
    for size in _DOWNLOAD_SIZES:
        try:
            start = time.perf_counter()
            resp = session.get(
                f"{_CF_BASE}/__down?bytes={size}",
                timeout=15,
                stream=True,
            )
            resp.raise_for_status()
            total = 0
            for chunk in resp.iter_content(chunk_size=65536):
                total += len(chunk)
            elapsed = time.perf_counter() - start
            # Guard against truncated/error responses (e.g. a proxy or CDN
            # returning a short error page with HTTP 200) skewing the
            # result — require we got at least 90% of the requested bytes.
            if elapsed > 0 and total >= size * 0.9:
                mbps = (total * 8) / elapsed / 1_000_000
                best_mbps = round(mbps, 2)
            else:
                logger.debug(
                    "Download size mismatch: requested %d, got %d — discarding sample",
                    size, total,
                )
        except requests.RequestException as exc:
            logger.debug("Download chunk (%d bytes) failed: %s", size, exc)
            continue
    return best_mbps


def _measure_upload(session: requests.Session) -> Optional[float]:
    """Upload a fixed-size payload and compute Mbit/s."""
    payload = b"0" * _UPLOAD_SIZE
    try:
        start = time.perf_counter()
        resp = session.post(
            f"{_CF_BASE}/__up",
            data=payload,
            timeout=20,
        )
        resp.raise_for_status()
        elapsed = time.perf_counter() - start
        if elapsed > 0:
            mbps = (len(payload) * 8) / elapsed / 1_000_000
            return round(mbps, 2)
    except requests.RequestException as exc:
        logger.debug("Upload failed: %s", exc)
    return None


def run_speedtest(timeout: int = 30) -> SpeedTestResult:
    """
    Run a full download/upload/latency speed test against Cloudflare.

    Designed to be called on a slow interval (e.g. every 15-30 minutes),
    not from the main 5s monitoring loop — it consumes real bandwidth and
    takes several seconds to complete.
    """
    now = datetime.now(timezone.utc).isoformat()
    session = requests.Session()
    session.headers.update({"User-Agent": _USER_AGENT})

    try:
        latency_ms, jitter_ms = _measure_latency(session)
        download_mbps = _measure_download(session)
        upload_mbps = _measure_upload(session)

        success = download_mbps is not None or upload_mbps is not None
        return SpeedTestResult(
            timestamp=now,
            download_mbps=download_mbps,
            upload_mbps=upload_mbps,
            latency_ms=latency_ms,
            jitter_ms=jitter_ms,
            server="Cloudflare",
            success=success,
            error=None if success else "All download/upload measurements failed",
        )
    except Exception as exc:
        logger.error("Speedtest failed: %s", exc)
        return SpeedTestResult(
            timestamp=now,
            download_mbps=None,
            upload_mbps=None,
            latency_ms=None,
            jitter_ms=None,
            server="Cloudflare",
            success=False,
            error=str(exc),
        )
    finally:
        session.close()
