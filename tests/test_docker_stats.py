"""Unit tests for src/docker_stats.py"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.docker_stats import read_docker_stats_snapshot, SNAPSHOT_FILENAME


def test_missing_file_returns_none():
    with tempfile.TemporaryDirectory() as tmpdir:
        assert read_docker_stats_snapshot(Path(tmpdir)) is None


def test_fresh_file_is_returned():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        content = "netwatch\t1.2%\t50MiB / 1GiB\t5.0%\n"
        (data_dir / SNAPSHOT_FILENAME).write_text(content, encoding="utf-8")
        assert read_docker_stats_snapshot(data_dir) == content


def test_stale_file_returns_none():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        path = data_dir / SNAPSHOT_FILENAME
        path.write_text("stale", encoding="utf-8")
        # Back-date the file well past MAX_AGE_SECONDS (60s)
        old = time.time() - 120
        os.utime(path, (old, old))
        assert read_docker_stats_snapshot(data_dir) is None
