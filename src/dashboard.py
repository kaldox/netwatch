"""
NetWatch – Flask web dashboard.
Serves the HTML/JS dashboard and JSON API endpoints.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request, send_from_directory

from .config import AppConfig
from .database import Database
from .statistics import (
    compute_daily_stats,
    compute_monthly_stats,
    format_duration,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def create_app(db: Database, cfg: AppConfig) -> Flask:
    """Create and configure the Flask application."""

    template_dir = _PROJECT_ROOT / "dashboard" / "templates"
    static_dir = _PROJECT_ROOT / "dashboard" / "static"

    app = Flask(
        __name__,
        template_folder=str(template_dir),
        static_folder=str(static_dir),
    )
    app.config["JSON_SORT_KEYS"] = False

    # ------------------------------------------------------------------
    # HTML pages
    # ------------------------------------------------------------------

    @app.route("/")
    def index():
        summary = db.get_summary_stats()
        latest_ip = db.get_latest_public_ip()
        open_events = db.get_open_events()
        return render_template(
            "index.html",
            summary=summary,
            latest_ip=latest_ip,
            open_events=open_events,
            hostname=socket.gethostname(),
        )

    # ------------------------------------------------------------------
    # JSON API
    # ------------------------------------------------------------------

    @app.route("/api/status")
    def api_status():
        summary = db.get_summary_stats()
        open_events = db.get_open_events()
        latest_ip = db.get_latest_public_ip()
        return jsonify(
            {
                "status": "OK" if not open_events else "PROBLEM",
                "open_events": len(open_events),
                "total_events": summary["total_events"],
                "total_measurements": summary["total_measurements"],
                "latest_ipv4": latest_ip["ipv4"] if latest_ip else None,
                "latest_ipv6": latest_ip["ipv6"] if latest_ip else None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "hostname": socket.gethostname(),
            }
        )

    @app.route("/api/events")
    def api_events():
        start = request.args.get("start")
        end = request.args.get("end")
        event_type = request.args.get("type")
        limit = int(request.args.get("limit", 200))
        events = db.get_events(start=start, end=end, event_type=event_type, limit=limit)
        for ev in events:
            if ev.get("duration_seconds") is not None:
                ev["duration_human"] = format_duration(ev["duration_seconds"])
            else:
                ev["duration_human"] = "offen"
        return jsonify(events)

    @app.route("/api/measurements")
    def api_measurements():
        target = request.args.get("target")
        limit = int(request.args.get("limit", 200))
        results = db.get_recent_measurements(target or "Cloudflare DNS", limit=limit)
        return jsonify(results)

    @app.route("/api/measurements/range")
    def api_measurements_range():
        start = request.args.get("start", "")
        end = request.args.get("end", "")
        target = request.args.get("target")
        if not start or not end:
            return jsonify({"error": "start and end required"}), 400
        rows = db.get_measurements_range(start=start, end=end, target_name=target)
        return jsonify(rows)

    @app.route("/api/traceroutes")
    def api_traceroutes():
        event_id = request.args.get("event_id")
        limit = int(request.args.get("limit", 30))
        rows = db.get_traceroutes(event_id=event_id, limit=limit)
        return jsonify(rows)

    @app.route("/api/public_ip")
    def api_public_ip():
        limit = int(request.args.get("limit", 100))
        rows = db.get_public_ip_history(limit=limit)
        return jsonify(rows)

    @app.route("/api/daily_stats")
    def api_daily_stats():
        start = request.args.get("start")
        end = request.args.get("end")
        rows = db.get_daily_stats(start=start, end=end)
        return jsonify(rows)

    @app.route("/api/summary")
    def api_summary():
        return jsonify(db.get_summary_stats())

    @app.route("/api/monthly_stats")
    def api_monthly_stats():
        year = int(request.args.get("year", date.today().year))
        month = int(request.args.get("month", date.today().month))
        start = f"{year}-{month:02d}-01"
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        end = f"{year}-{month:02d}-{last_day:02d}"

        daily_rows = db.get_daily_stats(start=start, end=end)
        monthly = compute_monthly_stats(daily_rows, year, month)
        return jsonify(
            {
                "year": monthly.year,
                "month": monthly.month,
                "availability_percent": monthly.availability_percent,
                "total_downtime_seconds": monthly.total_downtime_seconds,
                "total_downtime_human": format_duration(monthly.total_downtime_seconds),
                "outage_count": monthly.outage_count,
                "isp_failure_count": monthly.isp_failure_count,
                "local_failure_count": monthly.local_failure_count,
                "dns_failure_count": monthly.dns_failure_count,
                "avg_latency_ms": monthly.avg_latency_ms,
                "max_latency_ms": monthly.max_latency_ms,
                "longest_outage_seconds": monthly.longest_outage_seconds,
                "longest_outage_human": format_duration(monthly.longest_outage_seconds),
                "days_with_outages": monthly.days_with_outages,
            }
        )

    @app.route("/api/latency_chart")
    def api_latency_chart():
        """Return last 24h latency data for Chart.js."""
        hours = int(request.args.get("hours", 24))
        target = request.args.get("target", "Cloudflare DNS")
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        rows = db.get_measurements_range(start=since, end=now, target_name=target)
        labels = [r["timestamp"][:16] for r in rows]
        latencies = [r["latency_ms"] for r in rows]
        packet_loss = [r["packet_loss_percent"] for r in rows]
        return jsonify(
            {
                "labels": labels,
                "latency_ms": latencies,
                "packet_loss_percent": packet_loss,
                "target": target,
            }
        )

    @app.route("/api/event_timeline")
    def api_event_timeline():
        """Return events as timeline data."""
        days = int(request.args.get("days", 30))
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        events = db.get_events(start=since, limit=500)
        timeline = []
        for ev in events:
            timeline.append(
                {
                    "id": ev["event_id"],
                    "type": ev["event_type"],
                    "start": ev["started_at"],
                    "end": ev.get("ended_at"),
                    "duration_seconds": ev.get("duration_seconds"),
                    "confidence": ev.get("confidence_score"),
                    "description": ev.get("description"),
                }
            )
        return jsonify(timeline)

    @app.route("/api/system_resources")
    def api_system_resources():
        """
        Resource samples (CPU/RAM/load/temp/cycle-time). Pass event_id to
        get the snapshot taken when that specific event fired — the key
        evidence for telling apart 'Pi was under load' from 'network was
        genuinely down'. Otherwise returns recent samples for a time window.
        """
        event_id = request.args.get("event_id")
        hours = int(request.args.get("hours", 24))
        limit = int(request.args.get("limit", 500))

        if event_id:
            rows = db.get_system_resources(event_id=event_id, limit=10)
            return jsonify(rows)

        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        rows = db.get_system_resources(start=since, end=now, limit=limit)
        return jsonify(rows)

    @app.route("/api/speedtests")
    def api_speedtests():
        hours = int(request.args.get("hours", 48))
        limit = int(request.args.get("limit", 500))
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        rows = db.get_speedtests(start=since, end=now, limit=limit)
        return jsonify(rows)

    @app.route("/api/speedtests/latest")
    def api_speedtests_latest():
        row = db.get_latest_speedtest()
        return jsonify(row or {})

    @app.route("/api/fritzbox")
    def api_fritzbox():
        hours = int(request.args.get("hours", 48))
        limit = int(request.args.get("limit", 500))
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        rows = db.get_fritzbox_status(start=since, end=now, limit=limit)
        return jsonify(rows)

    @app.route("/api/fritzbox/latest")
    def api_fritzbox_latest():
        row = db.get_latest_fritzbox_status() or {}
        # Attach contract reference so the dashboard can show sync-vs-contract
        row["contract_download_mbps"] = cfg.fritzbox.contract_download_mbps
        row["contract_upload_mbps"] = cfg.fritzbox.contract_upload_mbps
        return jsonify(row)

    @app.route("/api/fritzbox/log")
    def api_fritzbox_log():
        category = request.args.get("category")
        limit = int(request.args.get("limit", 200))
        rows = db.get_fritzbox_log(category=category, limit=limit)
        return jsonify(rows)

    @app.route("/api/export/provider")
    def api_export_provider():
        """Generate the provider evidence package and return file paths."""
        days = int(request.args.get("days", 14))
        try:
            from .export import generate_provider_report
            output_dir = _PROJECT_ROOT / cfg.reports.output_dir
            files = generate_provider_report(db, cfg, output_dir, days=days)
            return jsonify({
                "status": "ok",
                "files": {k: v.name for k, v in files.items()},
                "directory": str(output_dir),
            })
        except Exception as exc:
            logger.error("Provider export failed: %s", exc, exc_info=True)
            return jsonify({"status": "error", "error": str(exc)}), 500

    @app.route("/reports/<path:filename>")
    def serve_report(filename):
        """Serve a generated report file for download."""
        reports_dir = _PROJECT_ROOT / cfg.reports.output_dir
        return send_from_directory(str(reports_dir), filename, as_attachment=True)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    return app
