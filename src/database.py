"""
NetWatch – Database layer.
SQLite with WAL mode, full schema, automatic migrations.
"""

from __future__ import annotations

import json
import logging
import socket
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Generator, Optional

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 9


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class MeasurementRow:
    timestamp: str
    target_name: str
    target_host: str
    target_type: str
    reachable: int           # 0/1
    latency_ms: Optional[float]
    packet_loss_percent: Optional[float]
    jitter_ms: Optional[float]
    dns_resolution_ms: Optional[float]
    public_ipv4: Optional[str]
    public_ipv6: Optional[str]
    gateway_reachable: int   # 0/1
    error_message: Optional[str]


@dataclass
class EventRow:
    event_id: str
    event_type: str          # LOCAL_NETWORK_FAILURE | ISP_FAILURE | DNS_FAILURE | …
    started_at: str
    ended_at: Optional[str]
    duration_seconds: Optional[float]
    confidence_score: float
    description: str
    public_ipv4_before: Optional[str]
    public_ipv4_during: Optional[str]
    public_ipv4_after: Optional[str]
    public_ipv6_before: Optional[str]
    public_ipv6_during: Optional[str]
    public_ipv6_after: Optional[str]
    gateway_ip: Optional[str]
    hostname: str
    network_interface: Optional[str]
    extra_json: Optional[str]
    cpu_percent: Optional[float] = None
    ram_percent: Optional[float] = None
    load_avg_1m: Optional[float] = None
    cpu_temp_celsius: Optional[float] = None
    measurement_cycle_seconds: Optional[float] = None


@dataclass
class TracerouteRow:
    event_id: Optional[str]
    timestamp: str
    target_host: str
    tool: str                # traceroute | mtr
    output: str
    duration_seconds: float


@dataclass
class PublicIpRow:
    timestamp: str
    ipv4: Optional[str]
    ipv6: Optional[str]
    changed: int             # 0/1


@dataclass
class DailyStatRow:
    date_str: str
    availability_percent: float
    downtime_seconds: float
    outage_count: int
    isp_failure_count: int
    local_failure_count: int
    dns_failure_count: int
    packet_loss_events: int
    latency_events: int
    avg_latency_ms: Optional[float]
    max_latency_ms: Optional[float]
    avg_packet_loss_percent: Optional[float]
    longest_outage_seconds: float


@dataclass
class SpeedTestRow:
    timestamp: str
    download_mbps: Optional[float]
    upload_mbps: Optional[float]
    latency_ms: Optional[float]
    jitter_ms: Optional[float]
    server: str
    success: int  # 0/1
    error_message: Optional[str] = None
    # FritzBox line data captured at the same moment (for throttling proof)
    fritz_down_sync_mbps: Optional[float] = None
    fritz_up_sync_mbps: Optional[float] = None
    fritz_wan_uptime_seconds: Optional[int] = None
    fritz_connection_status: Optional[str] = None
    fritz_physical_link_status: Optional[str] = None


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


class Database:
    """Thread-safe SQLite wrapper with WAL mode and automatic schema migration."""

    def __init__(self, db_path: Path, wal_mode: bool = True) -> None:
        self._path = db_path
        self._wal = wal_mode
        self._lock = threading.Lock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self._path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            if self._wal:
                conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        with self._lock, self._conn() as conn:
            version = conn.execute(
                "PRAGMA user_version"
            ).fetchone()[0]

            if version < 1:
                self._create_tables(conn)
            if version < 2:
                self._migrate_v2(conn)
            if version < 3:
                self._migrate_v3(conn)
            if version < 4:
                self._migrate_v4(conn)
            if version < 5:
                self._migrate_v5(conn)
            if version < 6:
                self._migrate_v6(conn)
            if version < 7:
                self._migrate_v7(conn)
            if version < 8:
                self._migrate_v8(conn)
            if version < 9:
                self._migrate_v9(conn)

            conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")

    def _create_tables(self, conn: sqlite3.Connection) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS measurements (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp            TEXT    NOT NULL,
                target_name          TEXT    NOT NULL,
                target_host          TEXT    NOT NULL,
                target_type          TEXT    NOT NULL,
                reachable            INTEGER NOT NULL DEFAULT 0,
                latency_ms           REAL,
                packet_loss_percent  REAL,
                jitter_ms            REAL,
                dns_resolution_ms    REAL,
                public_ipv4          TEXT,
                public_ipv6          TEXT,
                gateway_reachable    INTEGER NOT NULL DEFAULT 0,
                error_message        TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_measurements_timestamp
                ON measurements(timestamp);
            CREATE INDEX IF NOT EXISTS idx_measurements_target
                ON measurements(target_name, timestamp);

            CREATE TABLE IF NOT EXISTS events (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id             TEXT    NOT NULL UNIQUE,
                event_type           TEXT    NOT NULL,
                started_at           TEXT    NOT NULL,
                ended_at             TEXT,
                duration_seconds     REAL,
                confidence_score     REAL    NOT NULL DEFAULT 0.0,
                description          TEXT    NOT NULL DEFAULT '',
                public_ipv4_before   TEXT,
                public_ipv4_during   TEXT,
                public_ipv4_after    TEXT,
                public_ipv6_before   TEXT,
                public_ipv6_during   TEXT,
                public_ipv6_after    TEXT,
                gateway_ip           TEXT,
                hostname             TEXT    NOT NULL DEFAULT '',
                network_interface    TEXT,
                extra_json           TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_events_started
                ON events(started_at);
            CREATE INDEX IF NOT EXISTS idx_events_type
                ON events(event_type, started_at);

            CREATE TABLE IF NOT EXISTS traceroutes (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id          TEXT,
                timestamp         TEXT NOT NULL,
                target_host       TEXT NOT NULL,
                tool              TEXT NOT NULL,
                output            TEXT NOT NULL,
                duration_seconds  REAL NOT NULL DEFAULT 0.0
            );

            CREATE INDEX IF NOT EXISTS idx_traceroutes_event
                ON traceroutes(event_id);
            CREATE INDEX IF NOT EXISTS idx_traceroutes_timestamp
                ON traceroutes(timestamp);

            CREATE TABLE IF NOT EXISTS public_ip_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp  TEXT    NOT NULL,
                ipv4       TEXT,
                ipv6       TEXT,
                changed    INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_pubip_timestamp
                ON public_ip_history(timestamp);

            CREATE TABLE IF NOT EXISTS daily_statistics (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                date_str                 TEXT    NOT NULL UNIQUE,
                availability_percent     REAL    NOT NULL DEFAULT 100.0,
                downtime_seconds         REAL    NOT NULL DEFAULT 0.0,
                outage_count             INTEGER NOT NULL DEFAULT 0,
                isp_failure_count        INTEGER NOT NULL DEFAULT 0,
                local_failure_count      INTEGER NOT NULL DEFAULT 0,
                dns_failure_count        INTEGER NOT NULL DEFAULT 0,
                packet_loss_events       INTEGER NOT NULL DEFAULT 0,
                latency_events           INTEGER NOT NULL DEFAULT 0,
                avg_latency_ms           REAL,
                max_latency_ms           REAL,
                avg_packet_loss_percent  REAL,
                longest_outage_seconds   REAL    NOT NULL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS configuration_snapshots (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp  TEXT    NOT NULL,
                config_json TEXT   NOT NULL
            );

            CREATE TABLE IF NOT EXISTS system_resources (
                id                        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp                 TEXT    NOT NULL,
                cpu_percent               REAL,
                ram_percent               REAL,
                ram_used_mb               REAL,
                load_avg_1m               REAL,
                load_avg_5m               REAL,
                load_avg_15m              REAL,
                cpu_temp_celsius          REAL,
                measurement_cycle_seconds REAL,
                event_id                  TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_sysres_timestamp
                ON system_resources(timestamp);
            CREATE INDEX IF NOT EXISTS idx_sysres_event
                ON system_resources(event_id);

            CREATE TABLE IF NOT EXISTS speedtests (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT    NOT NULL,
                download_mbps   REAL,
                upload_mbps     REAL,
                latency_ms      REAL,
                jitter_ms       REAL,
                server          TEXT,
                success         INTEGER NOT NULL DEFAULT 0,
                error_message   TEXT,
                fritz_down_sync_mbps        REAL,
                fritz_up_sync_mbps          REAL,
                fritz_wan_uptime_seconds    INTEGER,
                fritz_connection_status     TEXT,
                fritz_physical_link_status  TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_speedtests_timestamp
                ON speedtests(timestamp);

            CREATE TABLE IF NOT EXISTS fritzbox_status (
                id                        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp                 TEXT    NOT NULL,
                reachable                 INTEGER NOT NULL DEFAULT 0,
                downstream_sync_mbps      REAL,
                upstream_sync_mbps        REAL,
                physical_link_status      TEXT,
                dsl_link_status           TEXT,
                connection_status         TEXT,
                last_connection_error     TEXT,
                wan_uptime_seconds        INTEGER,
                dsl_down_max_mbps         REAL,
                dsl_up_max_mbps           REAL,
                dsl_down_snr_db           REAL,
                dsl_up_snr_db             REAL,
                dsl_down_attenuation_db   REAL,
                dsl_up_attenuation_db     REAL,
                event_id                  TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_fritzbox_timestamp
                ON fritzbox_status(timestamp);
            CREATE INDEX IF NOT EXISTS idx_fritzbox_event
                ON fritzbox_status(event_id);

            CREATE TABLE IF NOT EXISTS fritzbox_log (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                event_timestamp   TEXT    NOT NULL,
                raw_date          TEXT,
                raw_time          TEXT,
                grp               TEXT,
                message_id        INTEGER,
                message           TEXT    NOT NULL,
                category          TEXT,
                sync_down_kbps    INTEGER,
                sync_up_kbps      INTEGER,
                cabling_cost_kbps INTEGER,
                UNIQUE(event_timestamp, message)
            );

            CREATE INDEX IF NOT EXISTS idx_fritzlog_timestamp
                ON fritzbox_log(event_timestamp);
            CREATE INDEX IF NOT EXISTS idx_fritzlog_category
                ON fritzbox_log(category, event_timestamp);
        """)
        logger.info("Database schema v1 created")

    def _migrate_v2(self, conn: sqlite3.Connection) -> None:
        """Add description column to events if missing (safe migration)."""
        try:
            conn.execute("ALTER TABLE events ADD COLUMN description TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        logger.info("Database migration v2 applied")

    def _migrate_v3(self, conn: sqlite3.Connection) -> None:
        """Add hostname column."""
        try:
            conn.execute("ALTER TABLE events ADD COLUMN hostname TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        logger.info("Database migration v3 applied")

    def _migrate_v4(self, conn: sqlite3.Connection) -> None:
        """Add network_interface column."""
        try:
            conn.execute("ALTER TABLE events ADD COLUMN network_interface TEXT")
        except sqlite3.OperationalError:
            pass
        logger.info("Database migration v4 applied")

    def _migrate_v5(self, conn: sqlite3.Connection) -> None:
        """
        Add system-resource snapshot columns to events, and create the
        system_resources table for continuous CPU/RAM/load/temp sampling.

        This lets you distinguish "the Pi itself was under load when this
        event fired" from "the network really was down" after the fact.
        """
        for col, coltype in (
            ("cpu_percent", "REAL"),
            ("ram_percent", "REAL"),
            ("load_avg_1m", "REAL"),
            ("cpu_temp_celsius", "REAL"),
            ("measurement_cycle_seconds", "REAL"),
        ):
            try:
                conn.execute(f"ALTER TABLE events ADD COLUMN {col} {coltype}")
            except sqlite3.OperationalError:
                pass  # column already exists

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS system_resources (
                id                        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp                 TEXT    NOT NULL,
                cpu_percent               REAL,
                ram_percent               REAL,
                ram_used_mb               REAL,
                load_avg_1m               REAL,
                load_avg_5m               REAL,
                load_avg_15m              REAL,
                cpu_temp_celsius          REAL,
                measurement_cycle_seconds REAL,
                event_id                  TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_sysres_timestamp
                ON system_resources(timestamp);
            CREATE INDEX IF NOT EXISTS idx_sysres_event
                ON system_resources(event_id);
        """)
        logger.info("Database migration v5 applied")

    def _migrate_v6(self, conn: sqlite3.Connection) -> None:
        """Add speedtests table for periodic download/upload throughput tests."""
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS speedtests (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT    NOT NULL,
                download_mbps   REAL,
                upload_mbps     REAL,
                latency_ms      REAL,
                jitter_ms       REAL,
                server          TEXT,
                success         INTEGER NOT NULL DEFAULT 0,
                error_message   TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_speedtests_timestamp
                ON speedtests(timestamp);
        """)
        logger.info("Database migration v6 applied")

    def _migrate_v7(self, conn: sqlite3.Connection) -> None:
        """
        Add FritzBox line-data columns to speedtests, and a fritzbox_status
        table for independent ISP-line corroboration (sync rate vs. measured
        throughput, WAN uptime / connection drops). This is the evidence
        that rules out the Pi as the cause of poor performance.
        """
        for col, coltype in (
            ("fritz_down_sync_mbps", "REAL"),
            ("fritz_up_sync_mbps", "REAL"),
            ("fritz_wan_uptime_seconds", "INTEGER"),
            ("fritz_connection_status", "TEXT"),
            ("fritz_physical_link_status", "TEXT"),
        ):
            try:
                conn.execute(f"ALTER TABLE speedtests ADD COLUMN {col} {coltype}")
            except sqlite3.OperationalError:
                pass

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS fritzbox_status (
                id                        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp                 TEXT    NOT NULL,
                reachable                 INTEGER NOT NULL DEFAULT 0,
                downstream_sync_mbps      REAL,
                upstream_sync_mbps        REAL,
                physical_link_status      TEXT,
                dsl_link_status           TEXT,
                connection_status         TEXT,
                last_connection_error     TEXT,
                wan_uptime_seconds        INTEGER,
                event_id                  TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_fritzbox_timestamp
                ON fritzbox_status(timestamp);
            CREATE INDEX IF NOT EXISTS idx_fritzbox_event
                ON fritzbox_status(event_id);
        """)
        logger.info("Database migration v7 applied")

    def _migrate_v8(self, conn: sqlite3.Connection) -> None:
        """
        Add extended DSL diagnostic columns to fritzbox_status: physical
        max-attainable rate, SNR margin, attenuation. These reveal whether
        the line *physically can't* reach the contracted speed (max rate
        below contract) versus being throttled at a healthy line.
        """
        for col, coltype in (
            ("dsl_down_max_mbps", "REAL"),
            ("dsl_up_max_mbps", "REAL"),
            ("dsl_down_snr_db", "REAL"),
            ("dsl_up_snr_db", "REAL"),
            ("dsl_down_attenuation_db", "REAL"),
            ("dsl_up_attenuation_db", "REAL"),
        ):
            try:
                conn.execute(f"ALTER TABLE fritzbox_status ADD COLUMN {col} {coltype}")
            except sqlite3.OperationalError:
                pass
        logger.info("Database migration v8 applied")

    def _migrate_v9(self, conn: sqlite3.Connection) -> None:
        """
        Add fritzbox_log table storing classified FritzBox event-log entries
        (sync changes, disconnects, and the box's own cabling-defect
        detection). The UNIQUE constraint makes re-imports idempotent since
        the same log lines reappear on every poll.
        """
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS fritzbox_log (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                event_timestamp   TEXT    NOT NULL,
                raw_date          TEXT,
                raw_time          TEXT,
                grp               TEXT,
                message_id        INTEGER,
                message           TEXT    NOT NULL,
                category          TEXT,
                sync_down_kbps    INTEGER,
                sync_up_kbps      INTEGER,
                cabling_cost_kbps INTEGER,
                UNIQUE(event_timestamp, message)
            );

            CREATE INDEX IF NOT EXISTS idx_fritzlog_timestamp
                ON fritzbox_log(event_timestamp);
            CREATE INDEX IF NOT EXISTS idx_fritzlog_category
                ON fritzbox_log(category, event_timestamp);
        """)
        logger.info("Database migration v9 applied")

    # ------------------------------------------------------------------
    # Measurements
    # ------------------------------------------------------------------

    def insert_measurement(self, row: MeasurementRow) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO measurements
                    (timestamp, target_name, target_host, target_type,
                     reachable, latency_ms, packet_loss_percent, jitter_ms,
                     dns_resolution_ms, public_ipv4, public_ipv6,
                     gateway_reachable, error_message)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    row.timestamp, row.target_name, row.target_host,
                    row.target_type, row.reachable, row.latency_ms,
                    row.packet_loss_percent, row.jitter_ms,
                    row.dns_resolution_ms, row.public_ipv4, row.public_ipv6,
                    row.gateway_reachable, row.error_message,
                ),
            )

    def get_recent_measurements(
        self,
        target_name: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM measurements
                WHERE target_name = ?
                ORDER BY timestamp DESC LIMIT ?
                """,
                (target_name, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_measurements_range(
        self,
        start: str,
        end: str,
        target_name: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            if target_name:
                rows = conn.execute(
                    """
                    SELECT * FROM measurements
                    WHERE timestamp BETWEEN ? AND ? AND target_name = ?
                    ORDER BY timestamp
                    """,
                    (start, end, target_name),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM measurements
                    WHERE timestamp BETWEEN ? AND ?
                    ORDER BY timestamp
                    """,
                    (start, end),
                ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def upsert_event(self, row: EventRow) -> None:
        with self._lock, self._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM events WHERE event_id = ?",
                (row.event_id,),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE events SET
                        ended_at=?, duration_seconds=?, confidence_score=?,
                        description=?, public_ipv4_during=?, public_ipv4_after=?,
                        public_ipv6_during=?, public_ipv6_after=?, extra_json=?
                    WHERE event_id=?
                    """,
                    (
                        row.ended_at, row.duration_seconds, row.confidence_score,
                        row.description, row.public_ipv4_during, row.public_ipv4_after,
                        row.public_ipv6_during, row.public_ipv6_after,
                        row.extra_json, row.event_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO events
                        (event_id, event_type, started_at, ended_at,
                         duration_seconds, confidence_score, description,
                         public_ipv4_before, public_ipv4_during, public_ipv4_after,
                         public_ipv6_before, public_ipv6_during, public_ipv6_after,
                         gateway_ip, hostname, network_interface, extra_json,
                         cpu_percent, ram_percent, load_avg_1m,
                         cpu_temp_celsius, measurement_cycle_seconds)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        row.event_id, row.event_type, row.started_at, row.ended_at,
                        row.duration_seconds, row.confidence_score, row.description,
                        row.public_ipv4_before, row.public_ipv4_during, row.public_ipv4_after,
                        row.public_ipv6_before, row.public_ipv6_during, row.public_ipv6_after,
                        row.gateway_ip, row.hostname, row.network_interface,
                        row.extra_json,
                        row.cpu_percent, row.ram_percent, row.load_avg_1m,
                        row.cpu_temp_celsius, row.measurement_cycle_seconds,
                    ),
                )

    def get_events(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        parts = ["SELECT * FROM events WHERE 1=1"]
        params: list[Any] = []
        if start:
            parts.append("AND started_at >= ?")
            params.append(start)
        if end:
            parts.append("AND started_at <= ?")
            params.append(end)
        if event_type:
            parts.append("AND event_type = ?")
            params.append(event_type)
        parts.append("ORDER BY started_at DESC LIMIT ?")
        params.append(limit)
        sql = " ".join(parts)
        with self._lock, self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_open_events(self) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE ended_at IS NULL ORDER BY started_at"
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # System resources (CPU/RAM/load/temp samples)
    # ------------------------------------------------------------------

    def insert_system_resource(
        self,
        timestamp: str,
        cpu_percent: Optional[float] = None,
        ram_percent: Optional[float] = None,
        ram_used_mb: Optional[float] = None,
        load_avg_1m: Optional[float] = None,
        load_avg_5m: Optional[float] = None,
        load_avg_15m: Optional[float] = None,
        cpu_temp_celsius: Optional[float] = None,
        measurement_cycle_seconds: Optional[float] = None,
        event_id: Optional[str] = None,
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO system_resources
                    (timestamp, cpu_percent, ram_percent, ram_used_mb,
                     load_avg_1m, load_avg_5m, load_avg_15m,
                     cpu_temp_celsius, measurement_cycle_seconds, event_id)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    timestamp, cpu_percent, ram_percent, ram_used_mb,
                    load_avg_1m, load_avg_5m, load_avg_15m,
                    cpu_temp_celsius, measurement_cycle_seconds, event_id,
                ),
            )

    def get_system_resources(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        event_id: Optional[str] = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        parts = ["SELECT * FROM system_resources WHERE 1=1"]
        params: list[Any] = []
        if start:
            parts.append("AND timestamp >= ?")
            params.append(start)
        if end:
            parts.append("AND timestamp <= ?")
            params.append(end)
        if event_id:
            parts.append("AND event_id = ?")
            params.append(event_id)
        parts.append("ORDER BY timestamp DESC LIMIT ?")
        params.append(limit)
        sql = " ".join(parts)
        with self._lock, self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def prune_system_resources(self, before_timestamp: str) -> int:
        """Delete resource samples older than the given ISO timestamp.
        Returns number of rows deleted. Keeps the table from growing
        unbounded since this is sampled every monitoring cycle."""
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM system_resources WHERE timestamp < ?",
                (before_timestamp,),
            )
            return cur.rowcount

    # ------------------------------------------------------------------
    # Speed tests (download/upload throughput)
    # ------------------------------------------------------------------

    def insert_speedtest(self, row: SpeedTestRow) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO speedtests
                    (timestamp, download_mbps, upload_mbps, latency_ms,
                     jitter_ms, server, success, error_message,
                     fritz_down_sync_mbps, fritz_up_sync_mbps,
                     fritz_wan_uptime_seconds, fritz_connection_status,
                     fritz_physical_link_status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    row.timestamp, row.download_mbps, row.upload_mbps,
                    row.latency_ms, row.jitter_ms, row.server,
                    row.success, row.error_message,
                    row.fritz_down_sync_mbps, row.fritz_up_sync_mbps,
                    row.fritz_wan_uptime_seconds, row.fritz_connection_status,
                    row.fritz_physical_link_status,
                ),
            )

    def get_speedtests(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        parts = ["SELECT * FROM speedtests WHERE 1=1"]
        params: list[Any] = []
        if start:
            parts.append("AND timestamp >= ?")
            params.append(start)
        if end:
            parts.append("AND timestamp <= ?")
            params.append(end)
        parts.append("ORDER BY timestamp DESC LIMIT ?")
        params.append(limit)
        sql = " ".join(parts)
        with self._lock, self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_latest_speedtest(self) -> Optional[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM speedtests WHERE success=1 ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # FritzBox status (independent ISP-line corroboration)
    # ------------------------------------------------------------------

    def insert_fritzbox_status(
        self,
        timestamp: str,
        reachable: int,
        downstream_sync_mbps: Optional[float] = None,
        upstream_sync_mbps: Optional[float] = None,
        physical_link_status: Optional[str] = None,
        dsl_link_status: Optional[str] = None,
        connection_status: Optional[str] = None,
        last_connection_error: Optional[str] = None,
        wan_uptime_seconds: Optional[int] = None,
        dsl_down_max_mbps: Optional[float] = None,
        dsl_up_max_mbps: Optional[float] = None,
        dsl_down_snr_db: Optional[float] = None,
        dsl_up_snr_db: Optional[float] = None,
        dsl_down_attenuation_db: Optional[float] = None,
        dsl_up_attenuation_db: Optional[float] = None,
        event_id: Optional[str] = None,
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO fritzbox_status
                    (timestamp, reachable, downstream_sync_mbps, upstream_sync_mbps,
                     physical_link_status, dsl_link_status, connection_status,
                     last_connection_error, wan_uptime_seconds,
                     dsl_down_max_mbps, dsl_up_max_mbps,
                     dsl_down_snr_db, dsl_up_snr_db,
                     dsl_down_attenuation_db, dsl_up_attenuation_db, event_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    timestamp, reachable, downstream_sync_mbps, upstream_sync_mbps,
                    physical_link_status, dsl_link_status, connection_status,
                    last_connection_error, wan_uptime_seconds,
                    dsl_down_max_mbps, dsl_up_max_mbps,
                    dsl_down_snr_db, dsl_up_snr_db,
                    dsl_down_attenuation_db, dsl_up_attenuation_db, event_id,
                ),
            )

    def get_fritzbox_status(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        event_id: Optional[str] = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        parts = ["SELECT * FROM fritzbox_status WHERE 1=1"]
        params: list[Any] = []
        if start:
            parts.append("AND timestamp >= ?")
            params.append(start)
        if end:
            parts.append("AND timestamp <= ?")
            params.append(end)
        if event_id:
            parts.append("AND event_id = ?")
            params.append(event_id)
        parts.append("ORDER BY timestamp DESC LIMIT ?")
        params.append(limit)
        sql = " ".join(parts)
        with self._lock, self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_latest_fritzbox_status(self) -> Optional[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM fritzbox_status WHERE reachable=1 ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def prune_fritzbox_status(self, before_timestamp: str) -> int:
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM fritzbox_status WHERE timestamp < ?",
                (before_timestamp,),
            )
            return cur.rowcount

    # ------------------------------------------------------------------
    # FritzBox event log
    # ------------------------------------------------------------------

    def insert_fritzbox_log_entry(
        self,
        event_timestamp: str,
        message: str,
        raw_date: Optional[str] = None,
        raw_time: Optional[str] = None,
        grp: Optional[str] = None,
        message_id: Optional[int] = None,
        category: Optional[str] = None,
        sync_down_kbps: Optional[int] = None,
        sync_up_kbps: Optional[int] = None,
        cabling_cost_kbps: Optional[int] = None,
    ) -> bool:
        """Insert a log entry. Returns True if newly inserted, False if it
        was a duplicate (same timestamp+message already present)."""
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO fritzbox_log
                    (event_timestamp, raw_date, raw_time, grp, message_id,
                     message, category, sync_down_kbps, sync_up_kbps, cabling_cost_kbps)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_timestamp, raw_date, raw_time, grp, message_id,
                    message, category, sync_down_kbps, sync_up_kbps, cabling_cost_kbps,
                ),
            )
            return cur.rowcount > 0

    def get_fritzbox_log(
        self,
        category: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        parts = ["SELECT * FROM fritzbox_log WHERE 1=1"]
        params: list[Any] = []
        if category:
            parts.append("AND category = ?")
            params.append(category)
        if start:
            parts.append("AND event_timestamp >= ?")
            params.append(start)
        if end:
            parts.append("AND event_timestamp <= ?")
            params.append(end)
        parts.append("ORDER BY event_timestamp DESC LIMIT ?")
        params.append(limit)
        sql = " ".join(parts)
        with self._lock, self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Traceroutes
    # ------------------------------------------------------------------

    def insert_traceroute(self, row: TracerouteRow) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO traceroutes
                    (event_id, timestamp, target_host, tool, output, duration_seconds)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    row.event_id, row.timestamp, row.target_host,
                    row.tool, row.output, row.duration_seconds,
                ),
            )

    def get_traceroutes(
        self,
        event_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            if event_id:
                rows = conn.execute(
                    "SELECT * FROM traceroutes WHERE event_id=? ORDER BY timestamp DESC LIMIT ?",
                    (event_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM traceroutes ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Public IP
    # ------------------------------------------------------------------

    def insert_public_ip(self, row: PublicIpRow) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO public_ip_history (timestamp, ipv4, ipv6, changed)
                VALUES (?,?,?,?)
                """,
                (row.timestamp, row.ipv4, row.ipv6, row.changed),
            )

    def get_latest_public_ip(self) -> Optional[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM public_ip_history ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def get_public_ip_history(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM public_ip_history ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Daily statistics
    # ------------------------------------------------------------------

    def upsert_daily_stat(self, row: DailyStatRow) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_statistics
                    (date_str, availability_percent, downtime_seconds, outage_count,
                     isp_failure_count, local_failure_count, dns_failure_count,
                     packet_loss_events, latency_events, avg_latency_ms, max_latency_ms,
                     avg_packet_loss_percent, longest_outage_seconds)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    row.date_str, row.availability_percent, row.downtime_seconds,
                    row.outage_count, row.isp_failure_count, row.local_failure_count,
                    row.dns_failure_count, row.packet_loss_events, row.latency_events,
                    row.avg_latency_ms, row.max_latency_ms, row.avg_packet_loss_percent,
                    row.longest_outage_seconds,
                ),
            )

    def get_daily_stats(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        parts = ["SELECT * FROM daily_statistics WHERE 1=1"]
        params: list[Any] = []
        if start:
            parts.append("AND date_str >= ?")
            params.append(start)
        if end:
            parts.append("AND date_str <= ?")
            params.append(end)
        parts.append("ORDER BY date_str DESC")
        sql = " ".join(parts)
        with self._lock, self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Configuration snapshots
    # ------------------------------------------------------------------

    def save_config_snapshot(self, config_json: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO configuration_snapshots (timestamp, config_json) VALUES (?,?)",
                (datetime.now(timezone.utc).isoformat(), config_json),
            )

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def vacuum(self) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("VACUUM")
        logger.info("Database VACUUM completed")

    def get_summary_stats(self) -> dict[str, Any]:
        """Quick summary for the dashboard overview."""
        with self._lock, self._conn() as conn:
            total_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            open_events = conn.execute(
                "SELECT COUNT(*) FROM events WHERE ended_at IS NULL"
            ).fetchone()[0]
            total_measurements = conn.execute(
                "SELECT COUNT(*) FROM measurements"
            ).fetchone()[0]
            last_event = conn.execute(
                "SELECT * FROM events ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            latest_ip = conn.execute(
                "SELECT * FROM public_ip_history ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        return {
            "total_events": total_events,
            "open_events": open_events,
            "total_measurements": total_measurements,
            "last_event": dict(last_event) if last_event else None,
            "latest_ip": dict(latest_ip) if latest_ip else None,
        }
