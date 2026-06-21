"""
NetWatch – Configuration loader.
Reads config/config.yaml and exposes typed dataclasses throughout the project.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Project root
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _project_path(relative: str) -> Path:
    return PROJECT_ROOT / relative


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class MonitoringConfig:
    interval_seconds: int = 5
    failure_threshold: int = 3
    recovery_threshold: int = 3
    ping_count: int = 3
    ping_timeout_seconds: int = 5
    dns_timeout_seconds: int = 5
    traceroute_on_failure: bool = True
    mtr_on_failure: bool = True
    traceroute_repeat_interval: int = 60


@dataclass
class TargetConfig:
    name: str
    host: str
    type: str  # gateway | icmp | dns


@dataclass
class ThresholdConfig:
    latency_warning_ms: float = 100.0
    latency_critical_ms: float = 500.0
    packet_loss_warning_percent: float = 5.0
    packet_loss_critical_percent: float = 20.0
    jitter_warning_ms: float = 50.0
    dns_resolution_warning_ms: float = 200.0


@dataclass
class PublicIpConfig:
    check_interval_seconds: int = 300
    providers: list[str] = field(default_factory=list)
    ipv6_providers: list[str] = field(default_factory=list)


@dataclass
class SpeedTestConfig:
    enabled: bool = True
    interval_seconds: int = 900  # 15 minutes
    timeout_seconds: int = 30


@dataclass
class FritzBoxConfig:
    enabled: bool = True
    # Router type: "fritzbox" (full DSL diagnostics) | "generic_tr064"
    # (experimental, sync rate only) | "none" (speedtest-only mode).
    vendor: str = "fritzbox"
    host: str = "192.168.178.1"
    timeout_seconds: int = 8
    # Throttling suspicion: flag when measured download falls below this
    # fraction of the negotiated sync rate (e.g. 0.5 = below 50% of line).
    throttle_ratio_threshold: float = 0.5
    # Optional credentials for extended DSL diagnostics (SNR, attenuation,
    # max attainable rate). Without these, only the unauthenticated WAN
    # status is read.
    username: str = "dslf-config"
    password: str = ""
    # Contracted line speed (Mbit/s) for the contract-vs-actual comparison.
    # 0 disables the contract comparison.
    contract_download_mbps: float = 0.0
    contract_upload_mbps: float = 0.0


@dataclass
class DatabaseConfig:
    path: str = "database/netwatch.db"
    wal_mode: bool = True
    vacuum_interval_days: int = 7


@dataclass
class LoggingConfig:
    level: str = "INFO"
    max_bytes: int = 10_485_760
    backup_count: int = 10


@dataclass
class DashboardConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    debug: bool = False


@dataclass
class ReportsConfig:
    output_dir: str = "reports"
    auto_generate: bool = True
    generate_time: str = "06:00"


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


@dataclass
class EmailConfig:
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    username: str = ""
    password: str = ""
    from_addr: str = ""
    to_addr: str = ""
    use_tls: bool = True


@dataclass
class NotificationsConfig:
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    email: EmailConfig = field(default_factory=EmailConfig)


@dataclass
class AppConfig:
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    targets_local: list[TargetConfig] = field(default_factory=list)
    targets_public_ip: list[TargetConfig] = field(default_factory=list)
    targets_public_domains: list[TargetConfig] = field(default_factory=list)
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    public_ip: PublicIpConfig = field(default_factory=PublicIpConfig)
    speedtest: SpeedTestConfig = field(default_factory=SpeedTestConfig)
    fritzbox: FritzBoxConfig = field(default_factory=FritzBoxConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    reports: ReportsConfig = field(default_factory=ReportsConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)

    @property
    def all_targets(self) -> list[TargetConfig]:
        return self.targets_local + self.targets_public_ip + self.targets_public_domains

    @property
    def db_path(self) -> Path:
        return _project_path(self.database.path)

    @property
    def reports_path(self) -> Path:
        return _project_path(self.reports.output_dir)

    @property
    def logs_path(self) -> Path:
        return _project_path("logs")


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _get(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Safe nested dict access."""
    for key in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(key, default)
    return d


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """
    Load configuration from YAML file.
    Falls back to sensible defaults if file is missing or PyYAML is not installed.
    """
    if config_path is None:
        config_path = _project_path("config/config.yaml")

    config_path = Path(config_path)

    # Fall back to the example config if the user hasn't created their own
    # yet (e.g. right after cloning the repo). This lets a fresh checkout
    # start up with sensible defaults instead of crashing.
    if not config_path.exists():
        example_path = _project_path("config/config.example.yaml")
        if example_path.exists():
            logger.warning(
                "config/config.yaml not found – using config.example.yaml. "
                "Copy it to config/config.yaml and edit it for your setup."
            )
            config_path = example_path

    raw: dict[str, Any] = {}
    if config_path.exists():
        if yaml is None:
            logger.warning("PyYAML not installed – using defaults")
        else:
            try:
                with config_path.open("r", encoding="utf-8") as fh:
                    raw = yaml.safe_load(fh) or {}
            except Exception as exc:
                logger.error("Failed to read config file %s: %s", config_path, exc)
    else:
        logger.warning("Config file not found: %s – using defaults", config_path)

    # --- monitoring ---
    m = raw.get("monitoring", {})
    monitoring = MonitoringConfig(
        interval_seconds=m.get("interval_seconds", 5),
        failure_threshold=m.get("failure_threshold", 3),
        recovery_threshold=m.get("recovery_threshold", 3),
        ping_count=m.get("ping_count", 3),
        ping_timeout_seconds=m.get("ping_timeout_seconds", 5),
        dns_timeout_seconds=m.get("dns_timeout_seconds", 5),
        traceroute_on_failure=m.get("traceroute_on_failure", True),
        mtr_on_failure=m.get("mtr_on_failure", True),
        traceroute_repeat_interval=m.get("traceroute_repeat_interval", 60),
    )

    # --- targets ---
    t = raw.get("targets", {})
    targets_local = [
        TargetConfig(name=x["name"], host=x["host"], type=x["type"])
        for x in t.get("local", [])
    ]
    targets_public_ip = [
        TargetConfig(name=x["name"], host=x["host"], type=x["type"])
        for x in t.get("public_ip", [])
    ]
    targets_public_domains = [
        TargetConfig(name=x["name"], host=x["host"], type=x["type"])
        for x in t.get("public_domains", [])
    ]

    # --- thresholds ---
    th = raw.get("thresholds", {})
    thresholds = ThresholdConfig(
        latency_warning_ms=th.get("latency_warning_ms", 100),
        latency_critical_ms=th.get("latency_critical_ms", 500),
        packet_loss_warning_percent=th.get("packet_loss_warning_percent", 5),
        packet_loss_critical_percent=th.get("packet_loss_critical_percent", 20),
        jitter_warning_ms=th.get("jitter_warning_ms", 50),
        dns_resolution_warning_ms=th.get("dns_resolution_warning_ms", 200),
    )

    # --- public ip ---
    pi = raw.get("public_ip", {})
    public_ip = PublicIpConfig(
        check_interval_seconds=pi.get("check_interval_seconds", 300),
        providers=pi.get("providers", ["https://api.ipify.org?format=json"]),
        ipv6_providers=pi.get("ipv6_providers", ["https://api6.ipify.org?format=json"]),
    )

    # --- speedtest ---
    sp = raw.get("speedtest", {})
    speedtest = SpeedTestConfig(
        enabled=sp.get("enabled", True),
        interval_seconds=sp.get("interval_seconds", 900),
        timeout_seconds=sp.get("timeout_seconds", 30),
    )

    # --- fritzbox ---
    fb = raw.get("fritzbox", {})
    fritzbox = FritzBoxConfig(
        enabled=fb.get("enabled", True),
        vendor=fb.get("vendor", "fritzbox"),
        host=fb.get("host", "192.168.178.1"),
        timeout_seconds=fb.get("timeout_seconds", 8),
        throttle_ratio_threshold=fb.get("throttle_ratio_threshold", 0.5),
        username=fb.get("username", "dslf-config"),
        password=fb.get("password", ""),
        contract_download_mbps=fb.get("contract_download_mbps", 0.0),
        contract_upload_mbps=fb.get("contract_upload_mbps", 0.0),
    )

    # --- database ---
    db = raw.get("database", {})
    database = DatabaseConfig(
        path=db.get("path", "database/netwatch.db"),
        wal_mode=db.get("wal_mode", True),
        vacuum_interval_days=db.get("vacuum_interval_days", 7),
    )

    # --- logging ---
    lg = raw.get("logging", {})
    logging_cfg = LoggingConfig(
        level=lg.get("level", "INFO"),
        max_bytes=lg.get("max_bytes", 10_485_760),
        backup_count=lg.get("backup_count", 10),
    )

    # --- dashboard ---
    dash = raw.get("dashboard", {})
    dashboard = DashboardConfig(
        host=dash.get("host", "0.0.0.0"),
        port=dash.get("port", 8080),
        debug=dash.get("debug", False),
    )

    # --- reports ---
    rp = raw.get("reports", {})
    reports = ReportsConfig(
        output_dir=rp.get("output_dir", "reports"),
        auto_generate=rp.get("auto_generate", True),
        generate_time=rp.get("generate_time", "06:00"),
    )

    # --- notifications ---
    notif_raw = raw.get("notifications", {})
    tg = notif_raw.get("telegram", {})
    em = notif_raw.get("email", {})
    notifications = NotificationsConfig(
        telegram=TelegramConfig(
            enabled=tg.get("enabled", False),
            bot_token=tg.get("bot_token", ""),
            chat_id=tg.get("chat_id", ""),
        ),
        email=EmailConfig(
            enabled=em.get("enabled", False),
            smtp_host=em.get("smtp_host", ""),
            smtp_port=em.get("smtp_port", 587),
            username=em.get("username", ""),
            password=em.get("password", ""),
            from_addr=em.get("from_addr", ""),
            to_addr=em.get("to_addr", ""),
            use_tls=em.get("use_tls", True),
        ),
    )

    return AppConfig(
        monitoring=monitoring,
        targets_local=targets_local,
        targets_public_ip=targets_public_ip,
        targets_public_domains=targets_public_domains,
        thresholds=thresholds,
        public_ip=public_ip,
        speedtest=speedtest,
        fritzbox=fritzbox,
        database=database,
        logging=logging_cfg,
        dashboard=dashboard,
        reports=reports,
        notifications=notifications,
    )
