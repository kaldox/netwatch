"""
NetWatch – Traceroute and MTR runner.
Executes system traceroute/mtr, captures full output, persists results.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TracerouteResult:
    tool: str          # "traceroute" | "mtr"
    target_host: str
    timestamp: str
    output: str
    duration_seconds: float
    success: bool
    error: Optional[str] = None


def run_traceroute(host: str, timeout: int = 30) -> TracerouteResult:
    """
    Run traceroute to the given host.
    Uses 'traceroute' on Linux/macOS.
    """
    system = platform.system().lower()
    now = datetime.now(timezone.utc).isoformat()

    if system == "linux":
        cmd = ["traceroute", "-n", "-w", "2", "-q", "1", host]
    elif system == "darwin":
        cmd = ["traceroute", "-n", "-w", "2", host]
    else:
        cmd = ["tracert", host]

    if not shutil.which(cmd[0]):
        return TracerouteResult(
            tool="traceroute",
            target_host=host,
            timestamp=now,
            output="",
            duration_seconds=0.0,
            success=False,
            error=f"'{cmd[0]}' not found in PATH",
        )

    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration = time.monotonic() - start
        output = result.stdout + (result.stderr if result.stderr else "")
        return TracerouteResult(
            tool="traceroute",
            target_host=host,
            timestamp=now,
            output=output.strip(),
            duration_seconds=duration,
            success=result.returncode == 0,
        )
    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        return TracerouteResult(
            tool="traceroute",
            target_host=host,
            timestamp=now,
            output="",
            duration_seconds=duration,
            success=False,
            error="traceroute timed out",
        )
    except Exception as exc:
        duration = time.monotonic() - start
        return TracerouteResult(
            tool="traceroute",
            target_host=host,
            timestamp=now,
            output="",
            duration_seconds=duration,
            success=False,
            error=str(exc),
        )


def run_mtr(host: str, count: int = 10, timeout: int = 60) -> TracerouteResult:
    """
    Run mtr (My TraceRoute) to the given host in report mode.
    mtr must be installed separately: sudo apt install mtr-tiny
    """
    now = datetime.now(timezone.utc).isoformat()

    if not shutil.which("mtr"):
        return TracerouteResult(
            tool="mtr",
            target_host=host,
            timestamp=now,
            output="",
            duration_seconds=0.0,
            success=False,
            error="'mtr' not found in PATH (install with: sudo apt install mtr-tiny)",
        )

    cmd = ["mtr", "--report", "--report-cycles", str(count), "--no-dns", host]

    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration = time.monotonic() - start
        output = result.stdout + (result.stderr if result.stderr else "")
        return TracerouteResult(
            tool="mtr",
            target_host=host,
            timestamp=now,
            output=output.strip(),
            duration_seconds=duration,
            success=result.returncode == 0,
        )
    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        return TracerouteResult(
            tool="mtr",
            target_host=host,
            timestamp=now,
            output="",
            duration_seconds=duration,
            success=False,
            error="mtr timed out",
        )
    except Exception as exc:
        duration = time.monotonic() - start
        return TracerouteResult(
            tool="mtr",
            target_host=host,
            timestamp=now,
            output="",
            duration_seconds=duration,
            success=False,
            error=str(exc),
        )


def run_diagnostics(
    host: str,
    run_traceroute_flag: bool = True,
    run_mtr_flag: bool = True,
) -> list[TracerouteResult]:
    """
    Run all requested diagnostic tools for a host.
    Returns list of results (may be empty if none enabled).
    """
    results: list[TracerouteResult] = []

    if run_traceroute_flag:
        logger.info("Running traceroute to %s", host)
        tr = run_traceroute(host)
        results.append(tr)
        if tr.success:
            logger.info("traceroute to %s completed in %.1fs", host, tr.duration_seconds)
        else:
            logger.warning("traceroute to %s failed: %s", host, tr.error)

    if run_mtr_flag:
        logger.info("Running mtr to %s", host)
        mtr = run_mtr(host)
        results.append(mtr)
        if mtr.success:
            logger.info("mtr to %s completed in %.1fs", host, mtr.duration_seconds)
        else:
            logger.warning("mtr to %s failed: %s", host, mtr.error)

    return results
