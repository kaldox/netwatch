"""Dashboard shows local time (Europe/Zurich), not cut UTC strings (2026-11-05).

The ISP event stored as 2026-11-05T19:32:51+00:00 appeared as "2026-11-05 19:32" although it
happened at 20:32 local time. The API keeps delivering UTC ISO timestamps (with offset); the
browser converts them. These tests guard both sides."""

from __future__ import annotations

import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import AppConfig
from src.dashboard import create_app
from src.database import Database, MeasurementRow

TEMPLATE = Path(__file__).parent.parent / "dashboard" / "templates" / "index.html"


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Database(Path(tmpdir) / "test.db")


def test_latency_chart_labels_are_full_utc_timestamps(db):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(microsecond=0).isoformat()
    db.insert_measurement(MeasurementRow(
        ts, "Cloudflare DNS", "1.1.1.1", "public_ip", 1, 12.3, 0.0, 1.0, None,
        None, None, 1, None))
    data = create_app(db, AppConfig()).test_client().get("/api/latency_chart?hours=1").get_json()
    # Not cut to "YYYY-MM-DDTHH:MM" any more – the offset must survive so the browser can convert.
    assert data["labels"] == [ts]
    assert data["labels"][0].endswith("+00:00")


def test_template_converts_instead_of_cutting():
    html = TEMPLATE.read_text(encoding="utf-8")
    assert "timeZone: TZ" in html and "const TZ = 'Europe/Zurich'" in html
    # The old pattern: ts.substring(0,16).replace('T', ' ') – shows UTC as if it were local time.
    assert not re.search(r"substring\(0,\s*16\)", html)
    assert "replace('T'" not in html
    # Hour-of-day buckets must not depend on the viewer's browser time zone.
    assert "getHours()" not in html
    # Chart axes go through the converter.
    assert "data.labels," not in html and "labels: data.labels\n" not in html


def test_daily_stats_label_says_utc_days():
    html = TEMPLATE.read_text(encoding="utf-8")
    assert "Tag (UTC)" in html
    assert "nach UTC" in html
