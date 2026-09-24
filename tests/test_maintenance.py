"""Maintenance (2026-09-24): VACUUM honours vacuum_interval_days; migration v11 drops the target index;
traceroute failures carry a reason."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.main as main_mod
from src.database import Database
from src.traceroute import _exit_error


def _watch(interval):
    nw = main_mod.NetWatch.__new__(main_mod.NetWatch)
    nw.cfg = types.SimpleNamespace(database=types.SimpleNamespace(vacuum_interval_days=interval))
    return nw


def test_vacuum_interval(monkeypatch, tmp_path):
    marker = tmp_path / ".last_vacuum"
    monkeypatch.setattr(main_mod, "_VACUUM_MARKER", marker)
    nw = _watch(7)
    assert nw._vacuum_due("2026-09-24")          # never vacuumed → once now
    marker.write_text("2026-09-20")
    assert not nw._vacuum_due("2026-09-24")      # 4 days < 7
    assert nw._vacuum_due("2026-09-27")          # 7 days
    assert not _watch(0)._vacuum_due("2026-12-31")   # 0 = never


def test_migration_v11_drops_target_index():
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        with db._conn() as conn:
            names = {r[0] for r in conn.execute("select name from sqlite_master where type='index'")}
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert "idx_measurements_target" not in names
        assert "idx_measurements_timestamp" in names
        assert version == 11


def test_traceroute_error_has_reason():
    r = subprocess.CompletedProcess(["traceroute"], 1, stdout="", stderr="connect: Network is unreachable\n")
    assert _exit_error(r) == "exit code 1: connect: Network is unreachable"
    assert _exit_error(subprocess.CompletedProcess(["x"], 0, "", "")) is None
