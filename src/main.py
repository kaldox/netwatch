"""
NetWatch – Main orchestrator.
Starts the monitoring loop, statistics scheduler, web dashboard, and report generator.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import signal
import socket
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Resolve project root so imports work regardless of CWD
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.classifier import Classifier, EventType
from src.config import AppConfig, TargetConfig, load_config
from src.database import Database, DailyStatRow, EventRow, MeasurementRow, PublicIpRow, SpeedTestRow, TracerouteRow
from src.fritzbox import read_fritzbox_status, read_fritzbox_log
from src.monitor import NetworkMonitor, get_public_ip
from src.notifier import Notifier
from src.reports import generate_monthly_report
from src.resources import sample_resources
from src.speedtest import run_speedtest
from src.statistics import compute_daily_stats, compute_monthly_stats
from src.storage import export_events_csv, setup_logging, write_evidence_file
from src.traceroute import run_diagnostics

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class NetWatch:
    def __init__(self, config_path: Optional[Path] = None) -> None:
        self.cfg = load_config(config_path)

        # Setup directories
        for d in ("logs", "data", "database", "reports", "data/evidence"):
            (_PROJECT_ROOT / d).mkdir(parents=True, exist_ok=True)

        # Setup logging
        setup_logging(
            _PROJECT_ROOT / "logs",
            level=self.cfg.logging.level,
            max_bytes=self.cfg.logging.max_bytes,
            backup_count=self.cfg.logging.backup_count,
        )

        self.db = Database(
            db_path=_PROJECT_ROOT / self.cfg.database.path,
            wal_mode=self.cfg.database.wal_mode,
        )
        self.monitor = NetworkMonitor(
            ping_count=self.cfg.monitoring.ping_count,
            ping_timeout=self.cfg.monitoring.ping_timeout_seconds,
            dns_timeout=float(self.cfg.monitoring.dns_timeout_seconds),
        )
        self.classifier = Classifier(
            failure_threshold=self.cfg.monitoring.failure_threshold,
            recovery_threshold=self.cfg.monitoring.recovery_threshold,
            latency_critical_ms=self.cfg.thresholds.latency_critical_ms,
            packet_loss_critical_percent=self.cfg.thresholds.packet_loss_critical_percent,
        )
        self.notifier = Notifier(self.cfg.notifications)

        self._stop_event = threading.Event()
        self._last_public_ip_check = 0.0
        self._last_daily_stats = ""   # date string of last computed stats
        self._last_traceroute_time: dict[str, float] = {}
        self._last_vacuum_date = ""

        # Persist current config snapshot
        self.db.save_config_snapshot(
            json.dumps(
                {
                    "interval_seconds": self.cfg.monitoring.interval_seconds,
                    "targets": len(self.cfg.all_targets),
                    "hostname": socket.gethostname(),
                }
            )
        )

        logger.info("NetWatch initialised – hostname=%s, targets=%d",
                    socket.gethostname(), len(self.cfg.all_targets))

    # ------------------------------------------------------------------
    # Public IP
    # ------------------------------------------------------------------

    def _refresh_public_ip(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_public_ip_check < self.cfg.public_ip.check_interval_seconds:
            return

        self._last_public_ip_check = now
        ipv4 = get_public_ip(self.cfg.public_ip.providers)
        ipv6 = get_public_ip(self.cfg.public_ip.ipv6_providers)

        latest = self.db.get_latest_public_ip()
        changed = (
            latest is None
            or latest.get("ipv4") != ipv4
            or latest.get("ipv6") != ipv6
        )

        if changed or latest is None:
            self.db.insert_public_ip(
                PublicIpRow(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    ipv4=ipv4,
                    ipv6=ipv6,
                    changed=1 if (latest and changed) else 0,
                )
            )
            if latest and changed:
                logger.warning(
                    "Public IP changed! was=%s now=%s (v6: %s→%s)",
                    latest.get("ipv4"), ipv4, latest.get("ipv6"), ipv6,
                )
            else:
                logger.debug("Public IP: %s / %s", ipv4, ipv6)

        self.monitor.set_public_ips(ipv4, ipv6)

    # ------------------------------------------------------------------
    # Measurement loop
    # ------------------------------------------------------------------

    def _build_target_list(self) -> list[tuple[str, str, str]]:
        result: list[tuple[str, str, str]] = []
        gw = self.monitor.gateway
        for t in self.cfg.all_targets:
            host = gw if t.host == "auto" and gw else t.host
            if not host:
                continue
            result.append((t.name, host, t.type))
        return result

    def _run_measurement_cycle(self) -> None:
        cycle_start = time.monotonic()

        self.monitor.refresh_gateway()
        self.classifier.set_gateway(self.monitor.gateway, self.monitor.interface)
        self._refresh_public_ip()

        targets = self._build_target_list()
        results = self.monitor.measure_all(targets)

        cycle_seconds = round(time.monotonic() - cycle_start, 2)
        if cycle_seconds > self.cfg.monitoring.interval_seconds:
            logger.warning(
                "Measurement cycle took %.2fs, longer than the configured "
                "%ss interval — host may be under load, which can produce "
                "false-positive timeouts that look like network outages.",
                cycle_seconds, self.cfg.monitoring.interval_seconds,
            )

        # Resource snapshot for this cycle (used for diagnosing whether the
        # host itself was under load when a measurement looked like a failure)
        resources = sample_resources()
        now_iso = datetime.now(timezone.utc).isoformat()
        self.db.insert_system_resource(
            timestamp=now_iso,
            cpu_percent=resources.cpu_percent,
            ram_percent=resources.ram_percent,
            ram_used_mb=resources.ram_used_mb,
            load_avg_1m=resources.load_avg_1m,
            load_avg_5m=resources.load_avg_5m,
            load_avg_15m=resources.load_avg_15m,
            cpu_temp_celsius=resources.cpu_temp_celsius,
            measurement_cycle_seconds=cycle_seconds,
        )

        # Persist measurements
        for r in results:
            self.db.insert_measurement(
                MeasurementRow(
                    timestamp=r.timestamp,
                    target_name=r.target_name,
                    target_host=r.target_host,
                    target_type=r.target_type,
                    reachable=1 if r.reachable else 0,
                    latency_ms=r.latency_ms,
                    packet_loss_percent=r.packet_loss_percent,
                    jitter_ms=r.jitter_ms,
                    dns_resolution_ms=r.dns_resolution_ms,
                    public_ipv4=r.public_ipv4,
                    public_ipv6=r.public_ipv6,
                    gateway_reachable=1 if r.gateway_reachable else 0,
                    error_message=r.error_message,
                )
            )

        # Classify
        changed_events = self.classifier.process(results)

        for ev in changed_events:
            logger.info(
                "EVENT %s %s confidence=%.0f%% %s",
                "OPENED" if ev.is_open else "CLOSED",
                ev.event_type.value,
                ev.confidence_score * 100,
                ev.description,
            )

            # Resource fields only set at event creation (the moment that
            # matters for diagnosing the cause); upsert_event ignores them
            # on the UPDATE path so closing an event won't overwrite them.
            self.db.upsert_event(
                EventRow(
                    event_id=ev.event_id,
                    event_type=ev.event_type.value,
                    started_at=ev.started_at,
                    ended_at=ev.ended_at,
                    duration_seconds=ev.duration_seconds,
                    confidence_score=ev.confidence_score,
                    description=ev.description,
                    public_ipv4_before=ev.public_ipv4_before,
                    public_ipv4_during=ev.public_ipv4_during,
                    public_ipv4_after=ev.public_ipv4_after,
                    public_ipv6_before=ev.public_ipv6_before,
                    public_ipv6_during=ev.public_ipv6_during,
                    public_ipv6_after=ev.public_ipv6_after,
                    gateway_ip=ev.gateway_ip,
                    hostname=ev.hostname,
                    network_interface=ev.network_interface,
                    extra_json=ev.extra_json(),
                    cpu_percent=resources.cpu_percent,
                    ram_percent=resources.ram_percent,
                    load_avg_1m=resources.load_avg_1m,
                    cpu_temp_celsius=resources.cpu_temp_celsius,
                    measurement_cycle_seconds=cycle_seconds,
                )
            )

            # Tag this cycle's resource sample with the event it triggered,
            # so the system_resources table is directly queryable per-event.
            if ev.is_open:
                self.db.insert_system_resource(
                    timestamp=now_iso,
                    cpu_percent=resources.cpu_percent,
                    ram_percent=resources.ram_percent,
                    ram_used_mb=resources.ram_used_mb,
                    load_avg_1m=resources.load_avg_1m,
                    load_avg_5m=resources.load_avg_5m,
                    load_avg_15m=resources.load_avg_15m,
                    cpu_temp_celsius=resources.cpu_temp_celsius,
                    measurement_cycle_seconds=cycle_seconds,
                    event_id=ev.event_id,
                )

                # Capture FritzBox line state at the moment the event fired.
                # If the FritzBox shows the WAN connection dropped (low uptime
                # / status not Connected / physical link Down), that's strong
                # independent evidence the fault is on the ISP line, not the
                # Pi. If the line is healthy but external targets are gone,
                # the fault is upstream of the FritzBox in the ISP's network.
                if self.cfg.fritzbox.enabled:
                    try:
                        fb = read_fritzbox_status(
                            self.cfg.fritzbox.host,
                            timeout=self.cfg.fritzbox.timeout_seconds,
                            username=self.cfg.fritzbox.username or None,
                            password=self.cfg.fritzbox.password or None,
                        )
                        self.db.insert_fritzbox_status(
                            timestamp=now_iso,
                            reachable=1 if fb.reachable else 0,
                            downstream_sync_mbps=fb.downstream_sync_mbps,
                            upstream_sync_mbps=fb.upstream_sync_mbps,
                            physical_link_status=fb.physical_link_status,
                            dsl_link_status=fb.dsl_link_status,
                            connection_status=fb.connection_status,
                            last_connection_error=fb.last_connection_error,
                            wan_uptime_seconds=fb.wan_uptime_seconds,
                            dsl_down_max_mbps=fb.dsl_downstream_max_mbps,
                            dsl_up_max_mbps=fb.dsl_upstream_max_mbps,
                            dsl_down_snr_db=fb.dsl_downstream_noise_margin_db,
                            dsl_up_snr_db=fb.dsl_upstream_noise_margin_db,
                            dsl_down_attenuation_db=fb.dsl_downstream_attenuation_db,
                            dsl_up_attenuation_db=fb.dsl_upstream_attenuation_db,
                            event_id=ev.event_id,
                        )
                        if fb.reachable:
                            logger.info(
                                "FritzBox @ event %s: link=%s conn=%s uptime=%ss lasterr=%s sync=%.1f/%.1f Mbit",
                                ev.event_id[:8],
                                fb.physical_link_status, fb.connection_status,
                                fb.wan_uptime_seconds, fb.last_connection_error,
                                fb.downstream_sync_mbps or 0.0,
                                fb.upstream_sync_mbps or 0.0,
                            )
                    except Exception as exc:
                        logger.debug("FritzBox read at event failed: %s", exc)

            # Notifications
            if ev.is_open:
                self.notifier.notify_event_opened(ev)
                # Launch diagnostics in background thread
                if self.cfg.monitoring.traceroute_on_failure:
                    diag_targets = [h for _, h, t in targets if t == "icmp"]
                    if diag_targets:
                        threading.Thread(
                            target=self._run_diagnostics_bg,
                            args=(ev.event_id, diag_targets[0]),
                            daemon=True,
                        ).start()
            else:
                self.notifier.notify_event_closed(ev)
                # Save evidence file
                self._save_evidence(ev.event_id)

        # Repeated traceroutes during active outage
        active = self.classifier.get_active_events()
        for ev in active:
            if ev.event_type in (EventType.ISP_FAILURE, EventType.ROUTING_FAILURE):
                last_tr = self._last_traceroute_time.get(ev.event_id, 0)
                if time.monotonic() - last_tr > self.cfg.monitoring.traceroute_repeat_interval:
                    diag_targets = [h for _, h, t in targets if t == "icmp"]
                    if diag_targets:
                        self._last_traceroute_time[ev.event_id] = time.monotonic()
                        threading.Thread(
                            target=self._run_diagnostics_bg,
                            args=(ev.event_id, diag_targets[0]),
                            daemon=True,
                        ).start()

    def _run_diagnostics_bg(self, event_id: str, host: str) -> None:
        try:
            tr_results = run_diagnostics(
                host,
                run_traceroute_flag=self.cfg.monitoring.traceroute_on_failure,
                run_mtr_flag=self.cfg.monitoring.mtr_on_failure,
            )
            for tr in tr_results:
                self.db.insert_traceroute(
                    TracerouteRow(
                        event_id=event_id,
                        timestamp=tr.timestamp,
                        target_host=tr.target_host,
                        tool=tr.tool,
                        output=tr.output,
                        duration_seconds=tr.duration_seconds,
                    )
                )
                # Also write evidence file
                write_evidence_file(
                    _PROJECT_ROOT / "data",
                    event_id,
                    tr.output,
                    suffix=f"{tr.tool}.txt",
                )
        except Exception as exc:
            logger.error("Diagnostics thread error: %s", exc)

    def _save_evidence(self, event_id: str) -> None:
        """Save a CSV snapshot of the event for the evidence archive."""
        try:
            events = self.db.get_events(limit=1)
            ev_list = [e for e in events if e.get("event_id") == event_id]
            if ev_list:
                content = json.dumps(ev_list[0], indent=2, default=str)
                write_evidence_file(
                    _PROJECT_ROOT / "data",
                    event_id,
                    content,
                    suffix="json",
                )
        except Exception as exc:
            logger.error("Evidence save error: %s", exc)

    # ------------------------------------------------------------------
    # Daily statistics
    # ------------------------------------------------------------------

    def _compute_daily_stats_if_needed(self) -> None:
        today_str = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()

        if self._last_daily_stats == yesterday:
            return
        self._last_daily_stats = yesterday

        # Compute for yesterday
        target_date = date.fromisoformat(yesterday)
        start_ts = f"{yesterday}T00:00:00"
        end_ts = f"{yesterday}T23:59:59"

        events = self.db.get_events(start=start_ts, end=end_ts, limit=1000)
        measurements = self.db.get_measurements_range(start=start_ts, end=end_ts)

        stats = compute_daily_stats(events, measurements, target_date)
        self.db.upsert_daily_stat(
            DailyStatRow(
                date_str=stats.date_str,
                availability_percent=stats.availability_percent,
                downtime_seconds=stats.downtime_seconds,
                outage_count=stats.outage_count,
                isp_failure_count=stats.isp_failure_count,
                local_failure_count=stats.local_failure_count,
                dns_failure_count=stats.dns_failure_count,
                packet_loss_events=stats.packet_loss_events,
                latency_events=stats.latency_events,
                avg_latency_ms=stats.avg_latency_ms,
                max_latency_ms=stats.max_latency_ms,
                avg_packet_loss_percent=stats.avg_packet_loss_percent,
                longest_outage_seconds=stats.longest_outage_seconds,
            )
        )
        logger.info(
            "Daily stats computed for %s: availability=%.3f%% downtime=%ss outages=%d",
            yesterday, stats.availability_percent, stats.downtime_seconds, stats.outage_count,
        )

    # ------------------------------------------------------------------
    # Vacuum
    # ------------------------------------------------------------------

    def _vacuum_if_needed(self) -> None:
        today = date.today().isoformat()
        if self._last_vacuum_date != today:
            self._last_vacuum_date = today
            try:
                self.db.vacuum()
            except Exception as exc:
                logger.error("Vacuum failed: %s", exc)
            try:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
                deleted = self.db.prune_system_resources(cutoff)
                if deleted:
                    logger.info("Pruned %d old system_resources rows (>14 days)", deleted)
            except Exception as exc:
                logger.error("system_resources pruning failed: %s", exc)
            try:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
                deleted = self.db.prune_fritzbox_status(cutoff)
                if deleted:
                    logger.info("Pruned %d old fritzbox_status rows (>90 days)", deleted)
            except Exception as exc:
                logger.error("fritzbox_status pruning failed: %s", exc)

    # ------------------------------------------------------------------
    # PDF report generation
    # ------------------------------------------------------------------

    def _generate_monthly_report(
        self, year: int, month: int
    ) -> None:
        import calendar

        start = f"{year}-{month:02d}-01"
        last_day = calendar.monthrange(year, month)[1]
        end = f"{year}-{month:02d}-{last_day:02d}"

        all_events = self.db.get_events(start=start + "T00:00:00", end=end + "T23:59:59", limit=2000)
        isp_events = [e for e in all_events if e["event_type"] == "ISP_FAILURE"]
        traceroutes = self.db.get_traceroutes(limit=20)
        daily_stats = self.db.get_daily_stats(start=start, end=end)

        monthly = compute_monthly_stats(daily_stats, year, month)

        ip_history = self.db.get_public_ip_history(limit=200)

        out_path = generate_monthly_report(
            output_dir=_PROJECT_ROOT / self.cfg.reports.output_dir,
            hostname=socket.gethostname(),
            year=year,
            month=month,
            monthly_stats=monthly,
            all_events=all_events,
            isp_events=isp_events,
            traceroutes=traceroutes,
            daily_stats=daily_stats,
            ip_history=ip_history,
        )
        logger.info("Monthly report generated: %s", out_path)

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    def _start_dashboard(self) -> threading.Thread:
        from src.dashboard import create_app

        app = create_app(self.db, self.cfg)

        def _run():
            import logging as _l
            _l.getLogger("werkzeug").setLevel(_l.WARNING)
            app.run(
                host=self.cfg.dashboard.host,
                port=self.cfg.dashboard.port,
                debug=False,
                use_reloader=False,
            )

        t = threading.Thread(target=_run, daemon=True, name="dashboard")
        t.start()
        logger.info(
            "Dashboard started: http://%s:%d",
            self.cfg.dashboard.host,
            self.cfg.dashboard.port,
        )
        return t

    # ------------------------------------------------------------------
    # Speed test (download/upload throughput, separate slow timer)
    # ------------------------------------------------------------------

    def _run_speedtest_once(self) -> None:
        try:
            result = run_speedtest(timeout=self.cfg.speedtest.timeout_seconds)

            # Read FritzBox line data at the same moment, so each speed
            # measurement carries its own independent line-capacity baseline.
            fb = None
            if self.cfg.fritzbox.enabled:
                try:
                    fb = read_fritzbox_status(
                        self.cfg.fritzbox.host,
                        timeout=self.cfg.fritzbox.timeout_seconds,
                        username=self.cfg.fritzbox.username or None,
                        password=self.cfg.fritzbox.password or None,
                    )
                except Exception as exc:
                    logger.debug("FritzBox read failed: %s", exc)

            self.db.insert_speedtest(
                SpeedTestRow(
                    timestamp=result.timestamp,
                    download_mbps=result.download_mbps,
                    upload_mbps=result.upload_mbps,
                    latency_ms=result.latency_ms,
                    jitter_ms=result.jitter_ms,
                    server=result.server,
                    success=1 if result.success else 0,
                    error_message=result.error,
                    fritz_down_sync_mbps=fb.downstream_sync_mbps if fb and fb.reachable else None,
                    fritz_up_sync_mbps=fb.upstream_sync_mbps if fb and fb.reachable else None,
                    fritz_wan_uptime_seconds=fb.wan_uptime_seconds if fb and fb.reachable else None,
                    fritz_connection_status=fb.connection_status if fb and fb.reachable else None,
                    fritz_physical_link_status=fb.physical_link_status if fb and fb.reachable else None,
                )
            )

            # Also store a standalone FritzBox status sample
            if fb is not None:
                self.db.insert_fritzbox_status(
                    timestamp=result.timestamp,
                    reachable=1 if fb.reachable else 0,
                    downstream_sync_mbps=fb.downstream_sync_mbps,
                    upstream_sync_mbps=fb.upstream_sync_mbps,
                    physical_link_status=fb.physical_link_status,
                    dsl_link_status=fb.dsl_link_status,
                    connection_status=fb.connection_status,
                    last_connection_error=fb.last_connection_error,
                    wan_uptime_seconds=fb.wan_uptime_seconds,
                    dsl_down_max_mbps=fb.dsl_downstream_max_mbps,
                    dsl_up_max_mbps=fb.dsl_upstream_max_mbps,
                    dsl_down_snr_db=fb.dsl_downstream_noise_margin_db,
                    dsl_up_snr_db=fb.dsl_upstream_noise_margin_db,
                    dsl_down_attenuation_db=fb.dsl_downstream_attenuation_db,
                    dsl_up_attenuation_db=fb.dsl_upstream_attenuation_db,
                )

            if result.success:
                # Throttling assessment: compare measured throughput against
                # the FritzBox-negotiated sync rate. This is the core piece
                # of evidence — low throughput on a healthy, high-sync line
                # points upstream of the FritzBox (i.e. the ISP).
                throttle_note = ""
                if (fb and fb.reachable and fb.downstream_sync_mbps
                        and result.download_mbps is not None):
                    ratio = result.download_mbps / fb.downstream_sync_mbps
                    pct = ratio * 100
                    if ratio < self.cfg.fritzbox.throttle_ratio_threshold:
                        throttle_note = (
                            f" — WARNUNG: nur {pct:.0f}% der Leitung "
                            f"(Sync {fb.downstream_sync_mbps:.1f} Mbit/s, "
                            f"Leitung {fb.physical_link_status}, "
                            f"WAN seit {fb.wan_uptime_seconds}s) → Drosselung im Providernetz wahrscheinlich"
                        )
                    else:
                        throttle_note = f" ({pct:.0f}% der Leitung, Sync {fb.downstream_sync_mbps:.1f} Mbit/s)"

                logger.info(
                    "Speedtest: down=%.1f Mbit/s up=%.1f Mbit/s latency=%.0fms%s",
                    result.download_mbps or 0.0,
                    result.upload_mbps or 0.0,
                    result.latency_ms or 0.0,
                    throttle_note,
                )
            else:
                logger.warning("Speedtest failed: %s", result.error)

            # Poll the FritzBox event log (sync changes, disconnects, cabling
            # defects). Requires credentials; runs on the same slow timer as
            # the speed test since the login adds overhead.
            if (self.cfg.fritzbox.enabled and self.cfg.fritzbox.password
                    and self.cfg.fritzbox.username):
                try:
                    log_result = read_fritzbox_log(
                        self.cfg.fritzbox.host,
                        self.cfg.fritzbox.username,
                        self.cfg.fritzbox.password,
                        timeout=self.cfg.fritzbox.timeout_seconds,
                    )
                    if log_result.reachable:
                        new_count = 0
                        cabling = 0
                        disconnects = 0
                        for e in log_result.entries:
                            inserted = self.db.insert_fritzbox_log_entry(
                                event_timestamp=e.timestamp,
                                message=e.message,
                                raw_date=e.raw_date,
                                raw_time=e.raw_time,
                                grp=e.group,
                                message_id=e.message_id,
                                category=e.category,
                                sync_down_kbps=e.sync_down_kbps,
                                sync_up_kbps=e.sync_up_kbps,
                                cabling_cost_kbps=e.cabling_cost_kbps,
                            )
                            if inserted:
                                new_count += 1
                                if e.category == "cabling_issue":
                                    cabling += 1
                                elif e.category == "disconnect":
                                    disconnects += 1
                        if new_count:
                            logger.info(
                                "FritzBox log: %d neue Einträge (%d Verkabelung, %d Abbrüche)",
                                new_count, cabling, disconnects,
                            )
                except Exception as exc:
                    logger.debug("FritzBox log poll failed: %s", exc)
        except Exception as exc:
            logger.error("Speedtest run error: %s", exc, exc_info=True)

    def _start_speedtest_loop(self) -> Optional[threading.Thread]:
        """
        Runs run_speedtest() on its own timer, independent of the main 5s
        monitoring loop — speed tests consume real bandwidth and take
        several seconds, so they must not block or skew the lightweight
        reachability measurements.
        """
        if not self.cfg.speedtest.enabled:
            logger.info("Speedtest disabled in config")
            return None

        interval = self.cfg.speedtest.interval_seconds

        def _loop():
            # Run one shortly after startup so the dashboard has data
            # quickly, then on the configured interval.
            self._stop_event.wait(15)
            while not self._stop_event.is_set():
                self._run_speedtest_once()
                self._stop_event.wait(interval)

        t = threading.Thread(target=_loop, daemon=True, name="speedtest")
        t.start()
        logger.info("Speedtest loop started – interval=%ds", interval)
        return t

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self) -> None:
        def _handle_signal(sig, frame):
            logger.info("Shutdown signal received (%s)", sig)
            self._stop_event.set()

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        self._start_dashboard()
        self._start_speedtest_loop()
        self._refresh_public_ip(force=True)

        logger.info(
            "Monitoring started – interval=%ds targets=%d",
            self.cfg.monitoring.interval_seconds,
            len(self.cfg.all_targets),
        )

        cycle = 0
        while not self._stop_event.is_set():
            t_start = time.monotonic()
            try:
                self._run_measurement_cycle()
                cycle += 1

                # Every ~100 cycles: maintenance tasks
                if cycle % 100 == 0:
                    self._compute_daily_stats_if_needed()

                # Once per day: vacuum
                if cycle % (86400 // max(1, self.cfg.monitoring.interval_seconds)) == 0:
                    self._vacuum_if_needed()

            except Exception as exc:
                logger.error("Measurement cycle error: %s", exc, exc_info=True)

            elapsed = time.monotonic() - t_start
            sleep_time = max(0.0, self.cfg.monitoring.interval_seconds - elapsed)
            self._stop_event.wait(sleep_time)

        logger.info("NetWatch stopped.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    nw = NetWatch()
    nw.run()


if __name__ == "__main__":
    main()
