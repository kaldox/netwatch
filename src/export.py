"""
NetWatch – Provider evidence export.

Generates a provider-ready evidence package: a PDF summary plus CSV raw-data
attachments. The report is deliberately structured to separate three layers
of responsibility so the conclusion is defensible and can't be turned back
on the customer:

    1. House wiring   — what the FritzBox itself flags as in-home cabling
                        defects (e.g. unauthorised splices) and their cost.
    2. DSL line       — the physical line capacity the provider delivers
                        (current sync, max-attainable, SNR margin) vs. the
                        contracted speed.
    3. Provider net   — measured throughput / reachability vs. the synced
                        line, i.e. what arrives once the line is accounted for.

Each measurement is timestamped and attributed, and the Pi's own resource
state is included so "the measuring device was overloaded" can be ruled out.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .database import Database
from .config import AppConfig
from .statistics import format_duration

logger = logging.getLogger(__name__)

BRAND = colors.HexColor("#1a3a5c")
GREEN = colors.HexColor("#1a7f4b")
RED = colors.HexColor("#c0392b")
ORANGE = colors.HexColor("#d68910")
GREY = colors.HexColor("#666666")
LIGHTGREY = colors.HexColor("#f0f0f0")


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        "NWTitle", parent=styles["Title"], fontSize=20, textColor=BRAND,
        spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        "NWSub", parent=styles["Normal"], fontSize=10, textColor=GREY,
        spaceAfter=14,
    ))
    styles.add(ParagraphStyle(
        "NWH2", parent=styles["Heading2"], fontSize=13, textColor=BRAND,
        spaceBefore=14, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        "NWBody", parent=styles["Normal"], fontSize=9.5, leading=14,
        spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        "NWVerdict", parent=styles["Normal"], fontSize=10, leading=15,
        spaceBefore=6, spaceAfter=6, leftIndent=8, borderPadding=8,
    ))
    return styles


def _mbps(kbps: Optional[float]) -> str:
    if kbps is None:
        return "–"
    return f"{kbps/1000:.1f}"


def _fmt(v: Any, suffix: str = "") -> str:
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:.1f}{suffix}"
    return f"{v}{suffix}"


def _summary_table(rows: list[list[str]], col_widths=None) -> Table:
    t = Table(rows, colWidths=col_widths, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHTGREY]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _analyse(db: Database, cfg: AppConfig, days: int) -> dict[str, Any]:
    """Pull and aggregate everything needed for the report."""
    now = datetime.now(timezone.utc)
    since = (now.replace(microsecond=0)).isoformat()
    from datetime import timedelta
    start = (now - timedelta(days=days)).isoformat()

    speedtests = db.get_speedtests(start=start, end=now.isoformat(), limit=5000)
    ok_speeds = [s for s in speedtests if s.get("success")]
    downs = [s["download_mbps"] for s in ok_speeds if s.get("download_mbps") is not None]
    ups = [s["upload_mbps"] for s in ok_speeds if s.get("upload_mbps") is not None]

    fb_latest = db.get_latest_fritzbox_status() or {}
    fb_samples = db.get_fritzbox_status(start=start, end=now.isoformat(), limit=5000)
    sync_downs = [f["downstream_sync_mbps"] for f in fb_samples
                  if f.get("downstream_sync_mbps") is not None]

    cabling = db.get_fritzbox_log(category="cabling_issue", limit=500)
    disconnects = db.get_fritzbox_log(category="disconnect", limit=500)
    sync_changes = db.get_fritzbox_log(category="sync_change", limit=500)

    events = db.get_events(start=start, end=now.isoformat(), limit=2000)
    isp_events = [e for e in events if e.get("event_type") == "ISP_FAILURE"]

    def avg(arr):
        return sum(arr) / len(arr) if arr else None
    def mn(arr):
        return min(arr) if arr else None
    def mx(arr):
        return max(arr) if arr else None

    return {
        "now": now,
        "days": days,
        "speedtest_count": len(ok_speeds),
        "down_avg": avg(downs), "down_min": mn(downs), "down_max": mx(downs),
        "up_avg": avg(ups), "up_min": mn(ups), "up_max": mx(ups),
        "fb_latest": fb_latest,
        "sync_down_avg": avg(sync_downs),
        "sync_down_min": mn(sync_downs), "sync_down_max": mx(sync_downs),
        "cabling": cabling,
        "disconnects": disconnects,
        "sync_changes": sync_changes,
        "isp_events": isp_events,
        "all_speedtests": speedtests,
        "all_fb": fb_samples,
    }


def generate_provider_report(
    db: Database,
    cfg: AppConfig,
    output_dir: Path,
    days: int = 14,
) -> dict[str, Path]:
    """
    Build the provider evidence PDF and CSV files.
    Returns dict of {name: path} for the generated files.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    a = _analyse(db, cfg, days)
    styles = _styles()

    pdf_path = output_dir / f"netwatch_providernachweis_{ts}.pdf"
    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title="NetWatch Provider-Nachweis",
    )
    story = []

    # ---- Header ----
    story.append(Paragraph("Internet-Leistungsnachweis", styles["NWTitle"]))
    story.append(Paragraph(
        f"Erstellt am {a['now'].astimezone().strftime('%d.%m.%Y um %H:%M')} Uhr · "
        f"Messzeitraum: letzte {days} Tage · Messsystem: NetWatch auf Raspberry Pi",
        styles["NWSub"],
    ))

    contract_dl = cfg.fritzbox.contract_download_mbps
    contract_ul = cfg.fritzbox.contract_upload_mbps
    fb = a["fb_latest"]

    # ---- Executive summary ----
    story.append(Paragraph("Zusammenfassung", styles["NWH2"]))
    summary_lines = []
    if contract_dl:
        summary_lines.append(
            f"Vertraglich vereinbart: <b>{contract_dl:.0f} Mbit/s</b> Download"
            + (f", {contract_ul:.0f} Mbit/s Upload" if contract_ul else "") + "."
        )
    if a["down_avg"] is not None:
        summary_lines.append(
            f"Gemessener Download im Schnitt: <b>{a['down_avg']:.1f} Mbit/s</b> "
            f"(min. {a['down_min']:.1f}, max. {a['down_max']:.1f}) "
            f"über {a['speedtest_count']} Messungen."
        )
    dsl_max = fb.get("dsl_down_max_mbps")
    if dsl_max and contract_dl:
        pct = dsl_max / contract_dl * 100
        summary_lines.append(
            f"Physikalisches Leitungsmaximum laut FritzBox: <b>{dsl_max:.1f} Mbit/s</b> "
            f"= {pct:.0f}% der vertraglichen Geschwindigkeit."
        )
    for line in summary_lines:
        story.append(Paragraph("– " + line, styles["NWBody"]))

    # ---- Layer 1: House wiring ----
    story.append(Paragraph("1. Hausverkabelung (eigene Seite)", styles["NWH2"]))
    if a["cabling"]:
        latest_cab = a["cabling"][0]
        cost = latest_cab.get("cabling_cost_kbps")
        cost_txt = f" Geschätzter Verlust durch die Verkabelung: rund {cost/1000:.1f} Mbit/s." if cost else ""
        story.append(Paragraph(
            f"Die FritzBox meldet eine Beeinträchtigung durch die Verkabelung im Haus "
            f"(z. B. eine Abzweigung oder Mehrfachverteilung).{cost_txt} "
            f"Insgesamt {len(a['cabling'])} solcher Meldungen im Zeitraum. "
            f"Dieser Anteil ist der eigenen Installation zuzurechnen und wird hier "
            f"offen ausgewiesen, damit der verbleibende Nachweis sauber dem Anbieter "
            f"zugeordnet werden kann.",
            styles["NWBody"],
        ))
        cab_rows = [["Datum", "Uhrzeit", "Geschätzter Verlust", "Meldung"]]
        for c in a["cabling"][:8]:
            cost_kbps = c.get("cabling_cost_kbps")
            cab_rows.append([
                c.get("raw_date", "–"), c.get("raw_time", "–"),
                f"{cost_kbps/1000:.1f} Mbit/s" if cost_kbps else "–",
                (c.get("message", "")[:60] + "…") if len(c.get("message", "")) > 60 else c.get("message", ""),
            ])
        story.append(Spacer(1, 4))
        story.append(_summary_table(cab_rows, col_widths=[2*cm, 1.8*cm, 3*cm, 8*cm]))
    else:
        story.append(Paragraph(
            "Keine Verkabelungs-Warnungen der FritzBox im Messzeitraum erfasst. "
            "Die in-house-Verkabelung wird von der FritzBox nicht beanstandet.",
            styles["NWBody"],
        ))

    # ---- Layer 2: DSL line ----
    story.append(Paragraph("2. DSL-Leitung (Anbieter-Leitung)", styles["NWH2"]))
    line_rows = [["Kennwert", "Wert", "Bewertung"]]
    if contract_dl:
        line_rows.append(["Vertrag Download", f"{contract_dl:.0f} Mbit/s", "Sollwert"])
    if fb.get("downstream_sync_mbps"):
        sync = fb["downstream_sync_mbps"]
        b = ("ausreichend" if not contract_dl or sync >= contract_dl * 0.9 else "unter Vertrag")
        line_rows.append(["Aktueller Sync (Down)", f"{sync:.1f} Mbit/s", b])
    if dsl_max:
        b = ("deckt Vertrag" if not contract_dl or dsl_max >= contract_dl * 0.95 else "unter Vertrag")
        line_rows.append(["Phys. Maximum (Down)", f"{dsl_max:.1f} Mbit/s", b])
    if fb.get("dsl_down_snr_db") is not None:
        snr = fb["dsl_down_snr_db"]
        b = ("auffällig hoch" if snr > 12 else "normal")
        line_rows.append(["SNR-Marge (Down)", f"{snr:.1f} dB", b])
    if fb.get("dsl_down_attenuation_db") is not None:
        line_rows.append(["Daempfung (Down)", f"{fb['dsl_down_attenuation_db']:.1f} dB", "–"])
    story.append(_summary_table(line_rows, col_widths=[5*cm, 4*cm, 5.8*cm]))

    story.append(Spacer(1, 6))
    if dsl_max and contract_dl and dsl_max < contract_dl * 0.95:
        story.append(Paragraph(
            f"<b>Befund:</b> Die Leitung erreicht physikalisch maximal {dsl_max:.1f} Mbit/s "
            f"und kann die vertraglichen {contract_dl:.0f} Mbit/s damit nicht erfüllen.",
            styles["NWBody"],
        ))
    if fb.get("dsl_down_snr_db") and fb["dsl_down_snr_db"] > 12:
        story.append(Paragraph(
            f"<b>Hinweis:</b> Die SNR-Marge von {fb['dsl_down_snr_db']:.1f} dB liegt deutlich "
            f"über dem üblichen Wert (~6 dB). Das deutet auf eine anbieterseitig konservativ "
            f"konfigurierte Leitung hin, die vermutlich höher synchronisieren könnte.",
            styles["NWBody"],
        ))

    # ---- Layer 3: Provider network ----
    story.append(Paragraph("3. Anbieternetz (Durchsatz & Verfügbarkeit)", styles["NWH2"]))
    if a["down_avg"] is not None and a["sync_down_avg"]:
        util = a["down_avg"] / a["sync_down_avg"] * 100
        story.append(Paragraph(
            f"Vom synchronisierten Leitungsdurchsatz ({a['sync_down_avg']:.1f} Mbit/s) "
            f"kommen im Schnitt {a['down_avg']:.1f} Mbit/s tatsächlich an "
            f"({util:.0f}% der Leitung). "
            + (f"{len(a['isp_events'])} dokumentierte Anbieter-Ausfälle im Zeitraum."
               if a["isp_events"] else "Keine vollständigen Anbieter-Ausfälle im Zeitraum."),
            styles["NWBody"],
        ))
    if a["disconnects"]:
        story.append(Paragraph(
            f"Verbindungsabbrüche laut FritzBox-Protokoll: {len(a['disconnects'])} "
            f"(PPPoE-/LCP-Fehler). Diese liegen außerhalb des Einflusses des Heimnetzes.",
            styles["NWBody"],
        ))

    # ---- Measurement integrity ----
    story.append(Paragraph("4. Messintegrität", styles["NWH2"]))
    story.append(Paragraph(
        "Alle Messungen erfolgten automatisiert auf einem dauerhaft laufenden Raspberry Pi, "
        "direkt per Netzwerkkabel mit der FritzBox verbunden. Zu jeder Messung werden die "
        "Systemauslastung des Messgeräts (CPU, RAM, Temperatur) sowie die Messzyklusdauer "
        "miterfasst, um eine Verfälschung durch Überlastung des Messgeräts auszuschließen. "
        "Die FritzBox-Leitungswerte stammen unmittelbar aus dem Router über dessen "
        "TR-064-Schnittstelle.",
        styles["NWBody"],
    ))

    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "<i>Dieser Bericht wurde automatisch durch NetWatch erstellt. Die zugrunde "
        "liegenden Rohdaten liegen als CSV-Dateien bei.</i>",
        styles["NWSub"],
    ))

    doc.build(story)
    logger.info("Provider report PDF written: %s", pdf_path)

    # ---- CSV exports ----
    csv_files = {}

    speed_csv = output_dir / f"netwatch_speedtests_{ts}.csv"
    with speed_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "timestamp", "download_mbps", "upload_mbps", "latency_ms",
            "fritz_down_sync_mbps", "fritz_up_sync_mbps", "percent_of_line",
            "fritz_connection_status", "success",
        ])
        for s in a["all_speedtests"]:
            pct = ""
            if s.get("download_mbps") and s.get("fritz_down_sync_mbps"):
                pct = f"{s['download_mbps']/s['fritz_down_sync_mbps']*100:.1f}"
            w.writerow([
                s.get("timestamp", ""), s.get("download_mbps", ""),
                s.get("upload_mbps", ""), s.get("latency_ms", ""),
                s.get("fritz_down_sync_mbps", ""), s.get("fritz_up_sync_mbps", ""),
                pct, s.get("fritz_connection_status", ""), s.get("success", ""),
            ])
    csv_files["speedtests"] = speed_csv

    fb_csv = output_dir / f"netwatch_fritzbox_{ts}.csv"
    with fb_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "timestamp", "downstream_sync_mbps", "upstream_sync_mbps",
            "dsl_down_max_mbps", "dsl_down_snr_db", "dsl_down_attenuation_db",
            "physical_link_status", "connection_status", "wan_uptime_seconds",
        ])
        for f in a["all_fb"]:
            w.writerow([
                f.get("timestamp", ""), f.get("downstream_sync_mbps", ""),
                f.get("upstream_sync_mbps", ""), f.get("dsl_down_max_mbps", ""),
                f.get("dsl_down_snr_db", ""), f.get("dsl_down_attenuation_db", ""),
                f.get("physical_link_status", ""), f.get("connection_status", ""),
                f.get("wan_uptime_seconds", ""),
            ])
    csv_files["fritzbox"] = fb_csv

    log_csv = output_dir / f"netwatch_fritzbox_log_{ts}.csv"
    all_log = db.get_fritzbox_log(limit=5000)
    with log_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "category", "sync_down_kbps", "sync_up_kbps",
                    "cabling_cost_kbps", "message"])
        for e in all_log:
            w.writerow([
                e.get("event_timestamp", ""), e.get("category", ""),
                e.get("sync_down_kbps", ""), e.get("sync_up_kbps", ""),
                e.get("cabling_cost_kbps", ""), e.get("message", ""),
            ])
    csv_files["log"] = log_csv

    result = {"pdf": pdf_path}
    result.update(csv_files)
    return result
