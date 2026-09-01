"""
NetWatch – Provider evidence export.

Generates a provider-ready evidence package: a PDF report plus CSV raw-data
attachments. The report separates three layers of responsibility so the
conclusion is defensible and can't be turned back on the customer:

    1. House wiring   — what the FritzBox flags as in-home cabling defects.
    2. DSL line       — the physical line capacity the provider delivers.
    3. Provider net   — measured throughput / availability vs. the line.

On top of that it compares the measured speeds against the three
contractual values (maximum / normally available / minimum) from the
product information sheet, using commonly-used reference thresholds
(no statute is invoked), and documents availability / outages with
timestamps.

NOTE: NetWatch is a *continuous documentation* tool, not a one-off
calibrated measurement. To take a case further it can be paired with an
official measurement tool (e.g. networktest.ch) and, in a dispute, the
telecoms ombudsman.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    CondPageBreak,
    HRFlowable,
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


# ---------------------------------------------------------------------------
# Styles / small formatting helpers
# ---------------------------------------------------------------------------

def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        "NWTitle", parent=styles["Title"], fontSize=20, textColor=BRAND, spaceAfter=6))
    styles.add(ParagraphStyle(
        "NWSub", parent=styles["Normal"], fontSize=10, textColor=GREY, spaceAfter=14))
    styles.add(ParagraphStyle(
        "NWFazit", parent=styles["Heading1"], fontSize=15, textColor=BRAND,
        spaceBefore=2, spaceAfter=8))
    styles.add(ParagraphStyle(
        "NWH2", parent=styles["Heading2"], fontSize=12.5, textColor=BRAND,
        spaceBefore=18, spaceAfter=2))
    styles.add(ParagraphStyle(
        "NWH3", parent=styles["Normal"], fontSize=9.5, textColor=BRAND,
        fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=3))
    styles.add(ParagraphStyle(
        "NWBody", parent=styles["Normal"], fontSize=9.5, leading=14, spaceAfter=6))
    styles.add(ParagraphStyle(
        "NWSmall", parent=styles["Normal"], fontSize=8.5, textColor=GREY, leading=12,
        spaceAfter=5))
    styles.add(ParagraphStyle(
        "NWBullet", parent=styles["Normal"], fontSize=8.5, textColor=GREY, leading=12,
        spaceAfter=3, leftIndent=10, firstLineIndent=-10))
    return styles


def _h2(text: str, styles) -> list:
    """A numbered section heading with an underline rule. Returned as a list
    (prefixed with a soft page break) so headings never get orphaned at the
    bottom of a page."""
    return [
        CondPageBreak(3.4 * cm),
        Paragraph(text, styles["NWH2"]),
        HRFlowable(width="100%", thickness=0.6, color=BRAND,
                   spaceBefore=2, spaceAfter=8),
    ]


def _fmt_num(v: Optional[float], suffix: str = "") -> str:
    if v is None:
        return "–"
    return f"{v:.1f}{suffix}"


def _local(ts: str):
    """Parse a stored ISO timestamp (UTC) and return an aware local datetime."""
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone()
    except Exception:
        return None


_CELL_HEAD = ParagraphStyle("cellHead", fontSize=8.5, leading=10.5,
                            textColor=colors.white, fontName="Helvetica-Bold")
_CELL_BODY = ParagraphStyle("cellBody", fontSize=8.5, leading=10.5,
                            textColor=colors.black)


def _table(rows: list[list[str]], col_widths=None) -> Table:
    # Wrap every cell in a Paragraph so long text wraps within its column
    # instead of overflowing into the neighbouring cell.
    wrapped = []
    for r_i, row in enumerate(rows):
        style = _CELL_HEAD if r_i == 0 else _CELL_BODY
        wrapped.append([
            c if isinstance(c, Paragraph) else Paragraph(str(c), style)
            for c in row
        ])
    t = Table(wrapped, colWidths=col_widths, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHTGREY]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _verdict_box(text: str, color) -> Table:
    t = Table([[Paragraph(text,
        ParagraphStyle("v", fontSize=10, leading=14, textColor=colors.white))]],
        colWidths=[14.8 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), color),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def _analyse(db: Database, cfg: AppConfig, days: int) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days)).isoformat()
    end = now.isoformat()

    speedtests = db.get_speedtests(start=start, end=end, limit=20000)
    ok = [s for s in speedtests if s.get("success")]
    downs = [s["download_mbps"] for s in ok if s.get("download_mbps") is not None]
    ups = [s["upload_mbps"] for s in ok if s.get("upload_mbps") is not None]

    # Group successful downloads by local calendar day (methodology needs
    # per-measurement-day evaluation).
    by_day: dict[str, list[float]] = {}
    for s in ok:
        if s.get("download_mbps") is None:
            continue
        dt = _local(s.get("timestamp", ""))
        if dt is None:
            continue
        by_day.setdefault(dt.date().isoformat(), []).append(s["download_mbps"])

    fb_latest = db.get_latest_fritzbox_status() or {}
    fb_samples = db.get_fritzbox_status(start=start, end=end, limit=20000)
    sync_downs = [f["downstream_sync_mbps"] for f in fb_samples
                  if f.get("downstream_sync_mbps") is not None]

    cabling = db.get_fritzbox_log(category="cabling_issue", limit=500)
    disconnects = db.get_fritzbox_log(category="disconnect", limit=500)
    sync_changes = db.get_fritzbox_log(category="sync_change", limit=500)

    events = db.get_events(start=start, end=end, limit=5000)
    isp_events = [e for e in events if e.get("event_type") == "ISP_FAILURE"]
    local_events = [e for e in events if e.get("event_type") == "LOCAL_NETWORK_FAILURE"]
    dns_events = [e for e in events if e.get("event_type") == "DNS_FAILURE"]

    # Self-diagnostic: local resolver (AdGuard/Pi-hole/etc.) reachability,
    # quantified separately from the externally-resolved DNS checks. Any
    # "dns"-type target configured under targets.local is treated as a
    # local-resolver probe (see config.example.yaml).
    local_dns_stats = []
    for t in cfg.targets_local:
        if t.type != "dns":
            continue
        failures, total = db.get_target_reachability(t.name, start, end)
        if total:
            local_dns_stats.append({
                "name": t.name, "failures": failures, "total": total,
                "percent": failures / total * 100,
            })

    daily = db.get_daily_stats(start=(now - timedelta(days=days)).date().isoformat(),
                               end=now.date().isoformat())
    total_downtime = sum(d.get("downtime_seconds", 0) or 0 for d in daily)
    total_outages = sum(d.get("outage_count", 0) or 0 for d in daily)
    isp_outage_days = sum(d.get("isp_failure_count", 0) or 0 for d in daily)
    longest_outage = max((d.get("longest_outage_seconds", 0) or 0 for d in daily),
                         default=0)
    avails = [d.get("availability_percent") for d in daily
              if d.get("availability_percent") is not None]
    avg_avail = sum(avails) / len(avails) if avails else None

    interfaces_seen = sorted(db.get_network_interfaces_seen(start, end))

    def avg(a): return sum(a) / len(a) if a else None

    return {
        "now": now, "days": days,
        "speedtest_count": len(ok),
        "measurement_days": sorted(by_day.keys()),
        "by_day": by_day,
        "downs": downs, "ups": ups,
        "down_avg": avg(downs), "down_min": min(downs) if downs else None,
        "down_max": max(downs) if downs else None,
        "up_avg": avg(ups), "up_min": min(ups) if ups else None,
        "up_max": max(ups) if ups else None,
        "fb_latest": fb_latest,
        "sync_down_avg": avg(sync_downs),
        "cabling": cabling, "disconnects": disconnects, "sync_changes": sync_changes,
        "isp_events": isp_events, "local_events": local_events, "dns_events": dns_events,
        "local_dns_stats": local_dns_stats, "interfaces_seen": interfaces_seen,
        "total_downtime": total_downtime, "total_outages": total_outages,
        "isp_outage_days": isp_outage_days, "longest_outage": longest_outage,
        "avg_avail": avg_avail,
        "all_speedtests": speedtests, "all_fb": fb_samples, "all_events": events,
    }


def _evaluate_contract(a: dict[str, Any], contract_max: float,
                       contract_normal: float, contract_min: float) -> dict[str, Any]:
    """Evaluate measured downloads against the three contractual values
    (maximum / normally available / minimum) using commonly-used reference
    thresholds (90% / 90% of measurements / minimum). No statute is invoked;
    these are orientation values only."""
    downs = a["downs"]
    by_day = a["by_day"]
    n = len(downs)
    res: dict[str, Any] = {"has_data": n > 0, "n": n, "deviation": False, "criteria": []}
    if n == 0:
        return res

    # Criterion 1 – maximum: 90% of max reached on >=2 measurement days
    if contract_max:
        thr = 0.9 * contract_max
        days_reached = sum(1 for vals in by_day.values() if any(v >= thr for v in vals))
        days_total = len(by_day)
        ok1 = days_reached >= 2
        res["criteria"].append((
            "Maximum (90%)",
            f">= {thr:.1f} Mbit/s an mind. 2 Messtagen erreicht",
            f"an {days_reached}/{days_total} Messtagen erreicht",
            ok1))
        if not ok1:
            res["deviation"] = True

    # Criterion 2 – normal available: reached in >=90% of measurements
    if contract_normal:
        reached = sum(1 for v in downs if v >= contract_normal)
        pct = reached / n * 100
        ok2 = pct >= 90
        res["criteria"].append((
            "Normal verfügbar (90% der Messungen)",
            f">= {contract_normal:.1f} Mbit/s in >= 90% der Messungen",
            f"in {pct:.0f}% der {n} Messungen erreicht",
            ok2))
        if not ok2:
            res["deviation"] = True

    # Criterion 3 – minimum: undercut on >=2 days => deviation
    if contract_min:
        days_under = sum(1 for vals in by_day.values() if any(v < contract_min for v in vals))
        ok3 = days_under < 2
        res["criteria"].append((
            "Minimum unterschritten",
            f"< {contract_min:.1f} Mbit/s an < 2 Messtagen",
            f"an {days_under} Messtagen unterschritten",
            ok3))
        if not ok3:
            res["deviation"] = True

    return res


# ---------------------------------------------------------------------------
# Executive summary ("Fazit")
# ---------------------------------------------------------------------------

def _summarise(db: Database, cfg: AppConfig, a: dict[str, Any],
               contract_eval: dict[str, Any]) -> dict[str, Any]:
    """Condense the whole analysis into one headline verdict plus the concrete
    findings that justify it. Rendered at the very top of the report so a
    reader who only skims the first page still gets the bottom line."""
    c_max = cfg.fritzbox.contract_download_mbps
    fb = a["fb_latest"]
    dsl_max = fb.get("dsl_down_max_mbps")

    # ISP outages the FritzBox's own WAN state confirms as a line drop
    # (same criterion as chapter 2's detail table).
    line_drops = 0
    for e in a["isp_events"]:
        fbs = db.get_fritzbox_status(event_id=e.get("event_id"), limit=1)
        if not fbs:
            continue
        conn = fbs[0].get("connection_status")
        ut = fbs[0].get("wan_uptime_seconds")
        if (conn and conn != "Connected") or (ut is not None and ut < 300):
            line_drops += 1

    throttle_pct = None
    if a["down_avg"] is not None and a["sync_down_avg"]:
        throttle_pct = a["down_avg"] / a["sync_down_avg"] * 100

    dsl_cant_meet = bool(dsl_max and c_max and dsl_max < c_max * 0.95)
    contract_deviation = bool(contract_eval.get("has_data") and contract_eval.get("deviation"))

    findings: list[str] = []
    severity = "green"  # green < orange < red

    if contract_deviation:
        severity = "red"
        violated = [name for name, _req, _res, ok in contract_eval["criteria"] if not ok]
        findings.append(
            "Geschwindigkeits-Richtwerte nicht erreicht: " + ", ".join(violated)
            + " (Details in Kapitel 1)."
        )
    if dsl_cant_meet:
        severity = "red"
        findings.append(
            f"Die Leitung erreicht physikalisch maximal {dsl_max:.1f} Mbit/s und kann den "
            f"vertraglichen Maximalwert ({c_max:.0f} Mbit/s) nicht erfüllen (Kapitel 5)."
        )
    if line_drops:
        severity = "red"
        findings.append(
            f"{line_drops} von {len(a['isp_events'])} Anbieter-Ausfällen sind durch das "
            f"Ereignisprotokoll der FritzBox als Leitungsabriss bestätigt (Kapitel 2/3)."
        )
    if throttle_pct is not None and throttle_pct < 50:
        severity = "red"
        findings.append(
            f"Vom synchronisierten Leitungsdurchsatz kommen im Schnitt nur "
            f"{throttle_pct:.0f}% an ({a['down_avg']:.1f} von {a['sync_down_avg']:.1f} "
            f"Mbit/s) — Drosselung im Providernetz wahrscheinlich (Kapitel 6)."
        )

    # Softer signals: lift to orange only if nothing above already made it red.
    if severity != "red":
        if a["isp_events"]:
            severity = "orange"
            findings.append(
                f"{len(a['isp_events'])} Anbieter-Ausfall/-Ausfälle dokumentiert, "
                f"Gesamt-Ausfallzeit {format_duration(a['total_downtime'])} (Kapitel 2)."
            )
        if a["disconnects"] or a["sync_changes"]:
            severity = "orange"
            findings.append(
                f"{len(a['disconnects'])} Zwangstrennungen und {len(a['sync_changes'])} "
                f"Neusynchronisierungen im Router-Protokoll — Hinweis auf eine instabile "
                f"Anbieter-Leitung (Kapitel 3/7)."
            )
        if throttle_pct is not None and throttle_pct < 80:
            severity = "orange"
            findings.append(
                f"Die Leitung wird im Schnitt nur zu {throttle_pct:.0f}% ausgenutzt "
                f"({a['down_avg']:.1f} von {a['sync_down_avg']:.1f} Mbit/s) — beobachten "
                f"(Kapitel 6)."
            )
        if a["avg_avail"] is not None and a["avg_avail"] < 99.9:
            severity = "orange"
            findings.append(
                f"Durchschnittliche Verfügbarkeit {a['avg_avail']:.3f}% liegt unter 99,9% "
                f"(Kapitel 2)."
            )

    if not findings:
        findings.append(
            "Im Messzeitraum sind weder eine erhebliche Vertragsabweichung noch "
            "anbieterseitig verursachte Ausfälle belegt."
        )

    headline = {
        "red": "Erhebliche Abweichung bzw. Anbieterstörung belegt",
        "orange": "Auffälligkeiten dokumentiert – Beobachtung bzw. Nachfrage beim Anbieter angezeigt",
        "green": "Keine erhebliche Vertragsabweichung im Messzeitraum belegt",
    }[severity]

    return {
        "severity": severity,
        "headline": headline,
        "findings": findings,
        "line_drops": line_drops,
        "throttle_pct": throttle_pct,
        "contract_deviation": contract_deviation,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def generate_provider_report(db: Database, cfg: AppConfig, output_dir: Path,
                             days: int = 14) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    a = _analyse(db, cfg, days)
    styles = _styles()

    fb = a["fb_latest"]
    c_max = cfg.fritzbox.contract_download_mbps
    c_norm = cfg.fritzbox.contract_normal_download_mbps
    c_min = cfg.fritzbox.contract_min_download_mbps
    dsl_max = fb.get("dsl_down_max_mbps")

    # Evaluated once up front so the executive summary and chapter 1 agree.
    contract_eval = _evaluate_contract(a, c_max, c_norm, c_min)

    pdf_path = output_dir / f"netwatch_providernachweis_{ts}.pdf"
    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm, bottomMargin=1.8 * cm,
        title="NetWatch Provider-Nachweis")
    story: list = []

    # ---- Header ----
    story.append(Paragraph("Internet-Leistungsnachweis", styles["NWTitle"]))
    period_from = (a["now"] - timedelta(days=days)).astimezone().strftime("%d.%m.%Y")
    period_to = a["now"].astimezone().strftime("%d.%m.%Y")
    story.append(Paragraph(
        f"Erstellt am {a['now'].astimezone().strftime('%d.%m.%Y um %H:%M')} Uhr · "
        f"Messzeitraum {period_from} – {period_to} ({days} Tage) · "
        f"Messsystem: NetWatch (Raspberry Pi, LAN-Kabel an Router)",
        styles["NWSub"]))

    # ---- Fazit (executive summary — deliberately the first thing on the page) ----
    summary = _summarise(db, cfg, a, contract_eval)
    story.append(Paragraph("Fazit", styles["NWFazit"]))
    _sev_color = {"red": RED, "orange": ORANGE, "green": GREEN}[summary["severity"]]
    _bullets = "<br/>".join("•&nbsp;" + f for f in summary["findings"])
    story.append(_verdict_box(
        f"<b>{summary['headline']}.</b><br/>{_bullets}", _sev_color))
    story.append(Spacer(1, 8))

    throttle_txt = (f"{summary['throttle_pct']:.0f}%"
                    if summary["throttle_pct"] is not None else "–")
    down_txt = _fmt_num(a["down_avg"], " Mbit/s")
    if c_max and a["down_avg"] is not None:
        down_txt += f"  ({a['down_avg'] / c_max * 100:.0f}% des Vertrags-Maximums)"
    kb_rows = [
        ["Kennwert", "Ergebnis"],
        ["Messzeitraum", f"{period_from} – {period_to} ({days} Tage)"],
        ["Erfolgreiche Durchsatzmessungen",
         f"{a['speedtest_count']} an {len(a['measurement_days'])} Messtagen"],
        ["Ø Download (real)", down_txt],
        ["Leitungsausnutzung (real / Sync)", throttle_txt],
        ["Ø Verfügbarkeit",
         f"{a['avg_avail']:.3f}%" if a["avg_avail"] is not None else "–"],
        ["Gesamt-Ausfallzeit", format_duration(a["total_downtime"])],
        ["Anbieter-Ausfälle (ISP)",
         f"{len(a['isp_events'])} (davon {summary['line_drops']} per Router bestätigt)"],
        ["Zwangstrennungen / Resyncs (Router)",
         f"{len(a['disconnects'])} / {len(a['sync_changes'])}"],
    ]
    if contract_eval.get("has_data") and contract_eval.get("criteria"):
        kb_rows.append(["Vertragswerte (Download)",
                        "Richtwert nicht erreicht" if summary["contract_deviation"]
                        else "Richtwerte im Messzeitraum eingehalten"])
    else:
        kb_rows.append(["Vertragswerte (Download)",
                        "keine Werte hinterlegt — Vergleich übersprungen"])
    story.append(_table(kb_rows, col_widths=[5.6 * cm, 9.2 * cm]))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "<b>Aufbau:</b> 1 Geschwindigkeit &amp; Vertragswerte · 2 Verfügbarkeit &amp; "
        "Ausfälle · 3 Zwangstrennungen · 4 Hausverkabelung · 5 DSL-Leitung · "
        "6 Anbieternetz · 7 Leitungsstabilität · 8 Methodik &amp; Absicherung. "
        "Alle Rohdaten liegen als CSV-Dateien bei.", styles["NWSmall"]))

    # ===================================================================
    # 1. Speed vs. contract values
    # ===================================================================
    story += _h2("1. Geschwindigkeit &amp; Vertragswerte (Download)", styles)
    if not c_max and not c_norm and not c_min:
        story.append(Paragraph(
            "Es sind keine Vertragswerte hinterlegt. In der Konfiguration lassen sich die "
            "Werte aus dem Produktinformationsblatt eintragen (Maximum, „normalerweise zur "
            "Verfügung stehend“, Minimum), damit dieser Abschnitt einen belastbaren "
            "Vergleich liefert.",
            styles["NWBody"]))
    else:
        story.append(Paragraph(
            "Gemessene Download-Werte gegenübergestellt den drei Vertragswerten aus dem "
            "Produktinformationsblatt:", styles["NWBody"]))
        ct_rows = [["Vertragswert", "Vereinbart", "Gemessen (Ø / min)", "Verhältnis"]]
        def ratio(meas, ref):
            return f"{meas/ref*100:.0f}%" if (meas is not None and ref) else "–"
        if c_max:
            ct_rows.append(["Maximum", f"{c_max:.1f} Mbit/s",
                            f"{_fmt_num(a['down_avg'])} / {_fmt_num(a['down_min'])}",
                            ratio(a["down_avg"], c_max)])
        if c_norm:
            ct_rows.append(["Normal verfügbar", f"{c_norm:.1f} Mbit/s",
                            f"{_fmt_num(a['down_avg'])} / {_fmt_num(a['down_min'])}",
                            ratio(a["down_avg"], c_norm)])
        if c_min:
            ct_rows.append(["Minimum", f"{c_min:.1f} Mbit/s",
                            f"{_fmt_num(a['down_avg'])} / {_fmt_num(a['down_min'])}",
                            ratio(a["down_min"], c_min)])
        story.append(_table(ct_rows, col_widths=[4.2*cm, 3.2*cm, 4.6*cm, 2.8*cm]))
        story.append(Spacer(1, 8))

        ev = contract_eval
        if ev["has_data"] and ev["criteria"]:
            story.append(Paragraph(
                "Bewertung anhand gebräuchlicher Richtwerte (es wird kein Gesetz "
                "herangezogen — reine Orientierung):", styles["NWBody"]))
            crit_rows = [["Richtwert", "Erwartung", "Ergebnis", "Status"]]
            for name, req, result, ok in ev["criteria"]:
                crit_rows.append([name, req, result,
                                  "erfüllt" if ok else "nicht erfüllt"])
            story.append(_table(crit_rows, col_widths=[4.4*cm, 4.8*cm, 3.6*cm, 2.0*cm]))
            story.append(Spacer(1, 8))
            if ev["deviation"]:
                story.append(_verdict_box(
                    "<b>Befund:</b> Mindestens ein Richtwert wird nicht erreicht — die "
                    "gemessene Leistung weicht deutlich von den Vertragswerten ab. Das ist "
                    "der Ansatzpunkt für eine Reklamation bei der Anbieterin.", RED))
            else:
                story.append(_verdict_box(
                    "<b>Befund:</b> Die Richtwerte werden im Messzeitraum eingehalten. "
                    "Eine deutliche Geschwindigkeitsabweichung ist nicht belegt.", GREEN))
        if c_max and a["downs"]:
            ref = c_norm or c_max
            below = sum(1 for v in a["downs"] if v < 0.8 * ref) / len(a["downs"]) * 100
            story.append(Spacer(1, 6))
            story.append(Paragraph(
                f"Ergänzender Richtwert (80 %): in <b>{below:.0f}%</b> der Messungen lag "
                f"der Download unter 80 % von {ref:.1f} Mbit/s. Ein dauerhaftes "
                f"Unterschreiten der 80-%-Marke gilt breit als Anhaltspunkt für eine "
                f"unzureichende Leistung.",
                styles["NWSmall"]))

    # ===================================================================
    # 2. Availability / outages
    # ===================================================================
    story += _h2("2. Verfügbarkeit &amp; Ausfälle", styles)
    av_rows = [["Kennwert", "Wert"]]
    if a["avg_avail"] is not None:
        av_rows.append(["Verfügbarkeit (Schnitt)", f"{a['avg_avail']:.3f}%"])
    av_rows.append(["Gesamt-Ausfallzeit", format_duration(a["total_downtime"])])
    av_rows.append(["Ausfälle gesamt", str(a["total_outages"])])
    av_rows.append(["davon Anbieter-Ausfälle (ISP)", str(len(a["isp_events"]))])
    av_rows.append(["Längster Einzelausfall", format_duration(a["longest_outage"])])
    story.append(_table(av_rows, col_widths=[7.4*cm, 7.4*cm]))
    if a["isp_events"]:
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            "Dokumentierte Anbieter-Ausfälle, jeweils mit dem Zustand, den die FritzBox im "
            "selben Moment meldete (unabhängige Bestätigung durch das Anbieter-Gerät):",
            styles["NWBody"]))
        out_rows = [["Beginn (lokal)", "Dauer", "FritzBox-WAN", "WAN-Uptime", "Bewertung"]]
        line_drop_count = 0
        for e in a["isp_events"][:14]:
            dt = _local(e.get("started_at", ""))
            beg = dt.strftime("%d.%m. %H:%M:%S") if dt else e.get("started_at", "–")
            dur = format_duration(e.get("duration_seconds") or 0) if e.get("ended_at") else "laufend"
            conn = "–"; up = "–"; verdict = "Providernetz (Leitung lief)"
            fbs = db.get_fritzbox_status(event_id=e.get("event_id"), limit=1)
            if fbs:
                f = fbs[0]
                conn = f.get("connection_status") or "–"
                ut = f.get("wan_uptime_seconds")
                up = format_duration(ut) if ut is not None else "–"
                if (conn and conn != "Connected") or (ut is not None and ut < 300):
                    verdict = "Leitungsabriss bestätigt"
                    line_drop_count += 1
            out_rows.append([beg, dur, conn, up, verdict])
        story.append(_table(out_rows, col_widths=[3.0*cm, 1.6*cm, 3.0*cm, 2.6*cm, 4.6*cm]))
        story.append(Paragraph(
            f"Bei <b>{line_drop_count}</b> der angezeigten Ausfälle war die Leitung laut FritzBox "
            f"im selben Moment getrennt bzw. gerade neu verbunden (WAN-Uptime zurückgesetzt) — "
            f"hier ist der Leitungsabriss durch das Anbieter-Gerät selbst belegt. Bei den übrigen "
            f"blieb die FritzBox verbunden, während die externen Ziele unerreichbar waren: eine "
            f"Störung im Providernetz oberhalb des Anschlusses.",
            styles["NWSmall"]))
    story.append(Paragraph(
        "Hinweis: NetWatch zeichnet auch während eines Ausfalls lokal weiter auf — "
        "Zeitpunkt, Dauer und die öffentliche IP vor/während/nach jedem Ausfall sind damit "
        "unabhängig belegt, selbst wenn das Internet zeitweise nicht erreichbar war.",
        styles["NWSmall"]))

    # ===================================================================
    # 3. Router disconnect log — independent evidence from the router
    # ===================================================================
    story += _h2("3. Zwangstrennungen laut Router-Protokoll", styles)
    reconnects = db.get_fritzbox_log(category="reconnect", limit=1000)
    n_disc = len(a["disconnects"]); n_recon = len(reconnects)
    if a["disconnects"] or reconnects:
        story.append(Paragraph(
            f"Die vom Messgerät unabhängige Beweisquelle ist das Ereignisprotokoll der "
            f"FritzBox selbst: Der Router dokumentiert jede Zwangstrennung mit Zeitstempel — "
            f"unabhängig davon, welches Gerät im Heimnetz misst, welcher DNS genutzt wird oder ob "
            f"gerade gesurft wird. Im Zeitraum protokolliert die FritzBox <b>{n_recon} "
            f"Neuverbindungen</b> und <b>{n_disc} Fehler-/Trennungsmeldungen</b> "
            f"(PPPoE-/LCP-/PPP-Timeouts) — rund <b>{n_recon / max(1, a['days']):.1f} "
            f"Leitungsabrisse pro Tag</b>.",
            styles["NWBody"]))
        dl_rows = [["Datum", "Uhrzeit", "Router-Meldung (wörtlich)"]]
        for e in a["disconnects"][:14]:
            msg = (e.get("message", "") or "")
            dl_rows.append([e.get("raw_date", "–"), e.get("raw_time", "–"),
                            (msg[:72] + "…") if len(msg) > 72 else msg])
        story.append(Spacer(1, 4))
        story.append(_table(dl_rows, col_widths=[2*cm, 1.8*cm, 11*cm]))
        story.append(Paragraph(
            "Diese Meldungen stammen wörtlich aus dem Anbieter-Gerät und sind der Kern des "
            "Nachweises: Die Leitung trennt sich regelmäßig zwangsweise — außerhalb des "
            "Einflusses des Heimnetzes und unabhängig von jeder Messung durch den Pi.",
            styles["NWSmall"]))
    else:
        story.append(Paragraph(
            "Im Messzeitraum wurden keine Zwangstrennungen im FritzBox-Protokoll erfasst.",
            styles["NWBody"]))

    # ===================================================================
    # 4. Layer 1: house wiring (own side)
    # ===================================================================
    story += _h2("4. Hausverkabelung (eigene Seite)", styles)
    if a["cabling"]:
        latest = a["cabling"][0]
        cost = latest.get("cabling_cost_kbps")
        cost_txt = (f" Geschätzter Verlust durch die Verkabelung: rund {cost/1000:.1f} Mbit/s."
                    if cost else "")
        story.append(Paragraph(
            f"Die FritzBox meldet eine Beeinträchtigung durch die Verkabelung im Haus.{cost_txt} "
            f"Insgesamt {len(a['cabling'])} solcher Meldungen im Zeitraum. Dieser Anteil ist der "
            f"eigenen Installation zuzurechnen und wird offen ausgewiesen, damit der verbleibende "
            f"Nachweis sauber dem Anbieter zugeordnet werden kann.",
            styles["NWBody"]))
        cab_rows = [["Datum", "Uhrzeit", "Geschätzter Verlust", "Meldung"]]
        for c in a["cabling"][:8]:
            ck = c.get("cabling_cost_kbps")
            msg = c.get("message", "")
            cab_rows.append([c.get("raw_date", "–"), c.get("raw_time", "–"),
                             f"{ck/1000:.1f} Mbit/s" if ck else "–",
                             (msg[:55] + "…") if len(msg) > 55 else msg])
        story.append(Spacer(1, 4))
        story.append(_table(cab_rows, col_widths=[2*cm, 1.8*cm, 3*cm, 8*cm]))
    else:
        story.append(Paragraph(
            "Keine Verkabelungs-Warnungen der FritzBox im Messzeitraum. Die in-house-"
            "Verkabelung wird vom Router nicht beanstandet.",
            styles["NWBody"]))

    # ===================================================================
    # 5. Layer 2: DSL line (provider's line)
    # ===================================================================
    story += _h2("5. DSL-Leitung (Anbieter-Leitung)", styles)
    line_rows = [["Kennwert", "Wert", "Bewertung"]]
    if c_max:
        line_rows.append(["Vertrag Maximum (Down)", f"{c_max:.1f} Mbit/s", "Sollwert"])
    if fb.get("downstream_sync_mbps"):
        sync = fb["downstream_sync_mbps"]
        b = "ausreichend" if (not c_max or sync >= c_max * 0.9) else "unter Vertrag"
        line_rows.append(["Aktueller Sync (Down)", f"{sync:.1f} Mbit/s", b])
    if dsl_max:
        b = "deckt Vertrag" if (not c_max or dsl_max >= c_max * 0.95) else "unter Vertrag"
        line_rows.append(["Phys. Maximum (Down)", f"{dsl_max:.1f} Mbit/s", b])
    if fb.get("dsl_down_snr_db") is not None:
        snr = fb["dsl_down_snr_db"]
        line_rows.append(["SNR-Marge (Down)", f"{snr:.1f} dB",
                          "auffällig hoch" if snr > 12 else "normal"])
    if fb.get("dsl_down_attenuation_db") is not None:
        line_rows.append(["Dämpfung (Down)", f"{fb['dsl_down_attenuation_db']:.1f} dB", "–"])
    story.append(_table(line_rows, col_widths=[5*cm, 4*cm, 5.8*cm]))
    story.append(Spacer(1, 6))
    if dsl_max and c_max and dsl_max < c_max * 0.95:
        story.append(Paragraph(
            f"<b>Befund:</b> Die Leitung erreicht physikalisch maximal {dsl_max:.1f} Mbit/s "
            f"und kann die vertraglichen {c_max:.0f} Mbit/s damit nicht erfüllen.",
            styles["NWBody"]))
    if fb.get("dsl_down_snr_db") and fb["dsl_down_snr_db"] > 12:
        story.append(Paragraph(
            f"<b>Hinweis:</b> Die SNR-Marge von {fb['dsl_down_snr_db']:.1f} dB liegt deutlich "
            f"über dem üblichen Wert (~6 dB) — Indiz für eine anbieterseitig konservativ "
            f"konfigurierte Leitung, die höher synchronisieren könnte.",
            styles["NWBody"]))

    # ===================================================================
    # 6. Layer 3: provider network
    # ===================================================================
    story += _h2("6. Anbieternetz (Durchsatz &amp; Abbrüche)", styles)
    if a["down_avg"] is not None and a["sync_down_avg"]:
        util = a["down_avg"] / a["sync_down_avg"] * 100
        story.append(Paragraph(
            f"Vom synchronisierten Leitungsdurchsatz ({a['sync_down_avg']:.1f} Mbit/s) kommen "
            f"im Schnitt {a['down_avg']:.1f} Mbit/s tatsächlich an ({util:.0f}% der Leitung).",
            styles["NWBody"]))
    if a["disconnects"]:
        story.append(Paragraph(
            f"Verbindungsabbrüche laut FritzBox-Protokoll: {len(a['disconnects'])} "
            f"(PPPoE-/LCP-Fehler) — außerhalb des Einflusses des Heimnetzes.",
            styles["NWBody"]))

    # ===================================================================
    # 7. Line stability / sync changes
    # ===================================================================
    story += _h2("7. Leitungsstabilität (Sync-Wechsel)", styles)
    syncs = a["sync_changes"]
    if syncs:
        per_day = len(syncs) / max(1, a["days"])
        downs_k = [s["sync_down_kbps"] for s in syncs if s.get("sync_down_kbps")]
        rng = ""
        if downs_k:
            rng = (f" Die Downstream-Sync-Rate schwankte dabei zwischen "
                   f"{min(downs_k)/1000:.1f} und {max(downs_k)/1000:.1f} Mbit/s.")
        story.append(Paragraph(
            f"Im Zeitraum hat sich die Leitung <b>{len(syncs)}-mal neu synchronisiert</b> "
            f"(rund {per_day:.1f} pro Tag).{rng} Häufige Resyncs sind ein Indiz für eine "
            f"instabile Anbieter-Leitung: jede Neusynchronisierung unterbricht kurz die "
            f"Verbindung, und die Rate kann danach niedriger liegen.",
            styles["NWBody"]))
        sc_rows = [["Datum", "Uhrzeit", "Neue Sync (Down/Up)", "Meldung"]]
        for sc in syncs[:10]:
            d = sc.get("sync_down_kbps"); u = sc.get("sync_up_kbps")
            if d and u:
                sv = f"{d/1000:.1f} / {u/1000:.1f} Mbit/s"
            elif d:
                sv = f"{d/1000:.1f} Mbit/s"
            else:
                sv = "–"
            msg = sc.get("message", "") or ""
            sc_rows.append([sc.get("raw_date", "–"), sc.get("raw_time", "–"), sv,
                            (msg[:46] + "…") if len(msg) > 46 else msg])
        story.append(Spacer(1, 4))
        story.append(_table(sc_rows, col_widths=[2*cm, 1.8*cm, 4.0*cm, 7.0*cm]))
    else:
        story.append(Paragraph(
            "Keine Sync-Wechsel im FritzBox-Protokoll erfasst — die Leitung blieb im "
            "Messzeitraum stabil synchronisiert (keine Zwangstrennungen/Resyncs).",
            styles["NWBody"]))

    # ===================================================================
    # 8. Methodology & safeguards  (how it was measured, why other causes
    #    are ruled out) — kept at the end so the findings come first.
    # ===================================================================
    story += _h2("8. Methodik &amp; Absicherung", styles)

    story.append(Paragraph("Wie gemessen wird", styles["NWH3"]))
    _method_points = [
        "Erreichbarkeit, Latenz, Jitter und Paketverlust alle 5 Sekunden; realer "
        "Durchsatz (Down-/Upload gegen Cloudflare) alle 15 Minuten.",
        f"Im Messzeitraum liegen <b>{a['speedtest_count']} erfolgreiche "
        f"Durchsatzmessungen</b> an <b>{len(a['measurement_days'])} Messtagen</b> vor.",
        "Alle Messungen kabelgebunden direkt am Router. CPU, RAM, Temperatur und "
        "Messzyklusdauer des Messgeräts werden je Messung miterfasst, um eine "
        "Verfälschung durch Überlastung auszuschließen.",
        "Die Leitungswerte stammen unmittelbar aus dem Router (TR-064). Alle Rohdaten "
        "liegen als CSV-Dateien bei und sind nachprüfbar.",
    ]
    for p in _method_points:
        story.append(Paragraph("•&nbsp;" + p, styles["NWBullet"]))

    story.append(Paragraph("Abgrenzung der Ursachen", styles["NWH3"]))
    story.append(Paragraph(
        "Anbieter-Ausfälle werden über direkte ICMP-Pings an feste öffentliche IP-Adressen "
        "(1.1.1.1, 8.8.8.8, 9.9.9.9) erkannt — ohne Namensauflösung. Ein lokaler oder selbst "
        "betriebener DNS-Server (z. B. AdGuard, Pi-hole) kann sie daher weder auslösen noch "
        "verfälschen. Die ergänzenden DNS-Checks fragen ebenfalls feste, unabhängige "
        "Nameserver direkt per IP ab, nicht den auf dem Messgerät konfigurierten Resolver. "
        "Jeder Ausfall wird zusätzlich mit dem WAN-Status der FritzBox im selben Moment "
        "abgeglichen. Reine Durchsatz- und Latenzwerte können durch gleichzeitige "
        "Eigennutzung im Haushalt beeinflusst sein und gelten hier nur als ergänzender "
        "Beleg.",
        styles["NWSmall"]))

    story.append(Paragraph("Ausschluss eigener Ursachen", styles["NWH3"]))
    story.append(Paragraph(
        "In der Praxis häufig vorgebrachte Einwände — und warum die Messmethodik sie "
        "bereits abdeckt:", styles["NWSmall"]))
    excl_rows = [["Möglicher Einwand", "Warum ausgeschlossen"]]
    excl_rows.append([
        "Fehler im Heimnetz (Kabel, Switch, Router-LAN)",
        "Gateway-Erreichbarkeit wird pro Zyklus separat geprüft; Ausfälle laufen als "
        "LOCAL_NETWORK_FAILURE und zählen nicht als Anbieter-Ausfall (Kapitel 2).",
    ])
    excl_rows.append([
        "Lokaler DNS-Server (z. B. AdGuard, Pi-hole)",
        "Anbieter-Ausfallerkennung nutzt ausschließlich IP-basierte ICMP-Checks ohne "
        "Namensauflösung. Die DNS-Checks auf öffentliche Domains fragen feste externe "
        "Nameserver direkt per IP ab, nicht den lokalen Resolver. Der lokale Resolver läuft "
        "als eigenes, getrennt ausgewertetes Diagnose-Ziel (Zahlen unten) und geht nicht in "
        "ISP- oder DNS-Ausfallwertung ein.",
    ])
    excl_rows.append([
        "Andere Dienste/Container auf demselben Gerät (z. B. Docker, Ressourcenlast)",
        "CPU-, RAM- und Lastmittel werden je Messzyklus auf Systemebene erfasst — das "
        "erfasst die Gesamtlast des Geräts unabhängig davon, welcher Prozess sie verursacht "
        "— und jedem Ereignis direkt zugeordnet. Die Messzykluszeit wird überwacht, damit ein "
        "überlastetes Gerät nicht als Netzausfall fehlinterpretiert wird.",
    ])
    ifaces = a["interfaces_seen"]
    wifi_ifaces = [i for i in ifaces if i.lower().startswith(("wl", "wifi"))]
    if ifaces:
        if wifi_ifaces:
            wlan_note = (
                f"<b>Achtung:</b> Im Zeitraum wurde das WLAN-Interface "
                f"{', '.join(wifi_ifaces)} beobachtet — die Kabelgebunden-Annahme trifft "
                f"für diesen Zeitraum nicht uneingeschränkt zu."
            )
        else:
            wlan_note = (
                f"Im Messzeitraum durchgehend über {', '.join(ifaces)} gemessen — "
                f"kein WLAN-Interface beobachtet."
            )
    else:
        wlan_note = (
            "Messgerät ist als Betriebsvoraussetzung kabelgebunden direkt am Router "
            "angeschlossen (siehe Kopfzeile dieses Berichts)."
        )
    excl_rows.append(["WLAN-Probleme zum Router", wlan_note])
    excl_rows.append([
        "Eigener Router-Neustart statt Anbieterstörung",
        "Das Router-Protokoll wird nach providerseitigen Symptomen klassifiziert "
        "(PPPoE-/LCP-Fehler, gescheiterte Anmeldung, PPP-Timeout, Kapitel 3); ein manueller "
        "Neustart erzeugt andere Meldungen und wird hier nicht mitgezählt.",
    ])
    excl_rows.append([
        "Eigene gleichzeitige Auslastung der Leitung (Streaming, Downloads)",
        "Die Ausfallerkennung basiert auf reiner Erreichbarkeit, nicht auf Durchsatz; "
        "Durchsatzwerte fließen nur als ergänzender, nicht als tragender Beleg ein.",
    ])
    story.append(Spacer(1, 4))
    story.append(_table(excl_rows, col_widths=[5.5*cm, 9.3*cm]))

    if a["local_dns_stats"]:
        story.append(Spacer(1, 6))
        n_dns = len(a["dns_events"])
        dns_clause = (f"wurde <b>1 DNS-Fehler-Ereignis</b> dokumentiert" if n_dns == 1
                     else f"wurden <b>{n_dns} DNS-Fehler-Ereignisse</b> dokumentiert")
        for s in a["local_dns_stats"]:
            story.append(Paragraph(
                f"Zahlen zum lokalen Resolver „{s['name']}“: im Messzeitraum in "
                f"<b>{s['failures']} von {s['total']}</b> Messungen "
                f"(<b>{s['percent']:.2f}%</b>) nicht erreichbar. Im selben Zeitraum {dns_clause} "
                f"— ein solches Ereignis setzt zwingend voraus, dass auch die über externe "
                f"Nameserver aufgelösten Domains gleichzeitig ausfallen; ein isolierter "
                f"Ausfall des lokalen Resolvers allein löst also keinen DNS- oder "
                f"Anbieter-Ausfall aus.",
                styles["NWSmall"]))

    story.append(Spacer(1, 8))
    story.append(Paragraph("Hinweis zur Verwendung", styles["NWH3"]))
    story.append(Paragraph(
        "Dieser Bericht ist eine automatisch erstellte technische Dokumentation, keine "
        "einmalige kalibrierte Messung. Für eine Reklamation wird das Protokoll direkt bei "
        "der Anbieterin vorgelegt; ergänzend lässt sich eine offizielle Messung "
        "(z. B. networktest.ch) heranziehen, im Streitfall die Schlichtungsstelle (ombudscom). "
        "Die verwendeten Prozentwerte (90 %, 80 %) sind gebräuchliche Richtwerte zur "
        "Orientierung, kein Gesetzesbezug.",
        styles["NWSmall"]))

    def _footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(GREY)
        canvas.drawString(
            2 * cm, 1.1 * cm,
            "NetWatch – automatisch erstellte technische Dokumentation")
        canvas.drawRightString(
            A4[0] - 2 * cm, 1.1 * cm, f"Seite {canvas.getPageNumber()}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    logger.info("Provider report PDF written: %s", pdf_path)

    # ---- CSV exports ----
    csv_files: dict[str, Path] = {}

    speed_csv = output_dir / f"netwatch_speedtests_{ts}.csv"
    with speed_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "download_mbps", "upload_mbps", "latency_ms",
                    "fritz_down_sync_mbps", "fritz_up_sync_mbps", "percent_of_line",
                    "fritz_connection_status", "success"])
        for s in a["all_speedtests"]:
            pct = ""
            if s.get("download_mbps") and s.get("fritz_down_sync_mbps"):
                pct = f"{s['download_mbps']/s['fritz_down_sync_mbps']*100:.1f}"
            w.writerow([s.get("timestamp", ""), s.get("download_mbps", ""),
                        s.get("upload_mbps", ""), s.get("latency_ms", ""),
                        s.get("fritz_down_sync_mbps", ""), s.get("fritz_up_sync_mbps", ""),
                        pct, s.get("fritz_connection_status", ""), s.get("success", "")])
    csv_files["speedtests"] = speed_csv

    fb_csv = output_dir / f"netwatch_fritzbox_{ts}.csv"
    with fb_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "downstream_sync_mbps", "upstream_sync_mbps",
                    "dsl_down_max_mbps", "dsl_down_snr_db", "dsl_down_attenuation_db",
                    "physical_link_status", "connection_status", "wan_uptime_seconds"])
        for f in a["all_fb"]:
            w.writerow([f.get("timestamp", ""), f.get("downstream_sync_mbps", ""),
                        f.get("upstream_sync_mbps", ""), f.get("dsl_down_max_mbps", ""),
                        f.get("dsl_down_snr_db", ""), f.get("dsl_down_attenuation_db", ""),
                        f.get("physical_link_status", ""), f.get("connection_status", ""),
                        f.get("wan_uptime_seconds", "")])
    csv_files["fritzbox"] = fb_csv

    # Outages / events CSV (new) — the availability evidence, machine-readable.
    ev_csv = output_dir / f"netwatch_ausfaelle_{ts}.csv"
    with ev_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["started_at", "ended_at", "duration_seconds", "event_type",
                    "confidence_score", "public_ipv4_before", "public_ipv4_during",
                    "public_ipv4_after", "description"])
        for e in a["all_events"]:
            w.writerow([e.get("started_at", ""), e.get("ended_at", ""),
                        e.get("duration_seconds", ""), e.get("event_type", ""),
                        e.get("confidence_score", ""), e.get("public_ipv4_before", ""),
                        e.get("public_ipv4_during", ""), e.get("public_ipv4_after", ""),
                        e.get("description", "")])
    csv_files["ausfaelle"] = ev_csv

    log_csv = output_dir / f"netwatch_fritzbox_log_{ts}.csv"
    all_log = db.get_fritzbox_log(limit=5000)
    with log_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "category", "sync_down_kbps", "sync_up_kbps",
                    "cabling_cost_kbps", "message"])
        for e in all_log:
            w.writerow([e.get("event_timestamp", ""), e.get("category", ""),
                        e.get("sync_down_kbps", ""), e.get("sync_up_kbps", ""),
                        e.get("cabling_cost_kbps", ""), e.get("message", "")])
    csv_files["log"] = log_csv

    result = {"pdf": pdf_path}
    result.update(csv_files)
    return result
