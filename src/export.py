"""
NetWatch – Provider evidence export.

Generates a provider-ready evidence package: a PDF report plus CSV raw-data
attachments. The report separates three layers of responsibility so the
conclusion is defensible and can't be turned back on the customer:

    1. House wiring   — what the FritzBox flags as in-home cabling defects.
    2. DSL line       — the physical line capacity the provider delivers.
    3. Provider net   — measured throughput / availability vs. the line.

On top of that it evaluates the measured speeds against the contractual
minimum / normal / maximum values using the criteria recognised by the
German regulator (Bundesnetzagentur, Vfg 99/2021) as an orientation, and
documents availability / outages with timestamps.

NOTE: NetWatch is a *continuous documentation* tool. It is deliberately
honest about its own limits and is NOT the official regulator measurement.
For a formal claim it should be paired with the official procedure
(DE: breitbandmessung.de Desktop-App · CH: networktest.ch + ombudscom).
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
        "NWH2", parent=styles["Heading2"], fontSize=13, textColor=BRAND,
        spaceBefore=14, spaceAfter=6))
    styles.add(ParagraphStyle(
        "NWBody", parent=styles["Normal"], fontSize=9.5, leading=14, spaceAfter=6))
    styles.add(ParagraphStyle(
        "NWSmall", parent=styles["Normal"], fontSize=8, textColor=GREY, leading=11,
        spaceAfter=4))
    return styles


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
        "isp_events": isp_events, "local_events": local_events,
        "total_downtime": total_downtime, "total_outages": total_outages,
        "isp_outage_days": isp_outage_days, "longest_outage": longest_outage,
        "avg_avail": avg_avail,
        "all_speedtests": speedtests, "all_fb": fb_samples, "all_events": events,
    }


def _evaluate_contract(a: dict[str, Any], contract_max: float,
                       contract_normal: float, contract_min: float) -> dict[str, Any]:
    """Evaluate measured downloads against the three contractual values using
    the Bundesnetzagentur (Vfg 99/2021) criteria as orientation."""
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

    # ---- 0. Data basis & methodology ----
    story.append(Paragraph("Datengrundlage &amp; Methodik", styles["NWH2"]))
    story.append(Paragraph(
        f"NetWatch misst kontinuierlich und automatisiert: Erreichbarkeit, Latenz, "
        f"Jitter und Paketverlust alle 5 Sekunden, den realen Durchsatz (Down-/Upload "
        f"gegen Cloudflare) alle 15 Minuten. Im Zeitraum liegen "
        f"<b>{a['speedtest_count']} erfolgreiche Durchsatzmessungen</b> an "
        f"<b>{len(a['measurement_days'])} Messtagen</b> vor. Alle Messungen erfolgen "
        f"kabelgebunden direkt am Router; die Systemlast des Messgeräts wird je Messung "
        f"miterfasst, um eine Verfälschung durch Überlastung auszuschließen.",
        styles["NWBody"]))
    story.append(Paragraph(
        "Wichtig zur Einordnung: NetWatch ist eine <b>durchgehende Langzeit-Dokumentation</b> "
        "und ersetzt nicht das amtliche Messverfahren. Für einen rechtsverbindlichen Nachweis "
        "sollte dieser Bericht mit dem offiziellen Verfahren kombiniert werden "
        "(Deutschland: Breitbandmessung-Desktop-App der Bundesnetzagentur; "
        "Schweiz: networktest.ch, Schlichtung über ombudscom). Die folgende Bewertung "
        "orientiert sich an den Kriterien der Bundesnetzagentur (Vfg 99/2021).",
        styles["NWSmall"]))

    # ---- 1. Contract evaluation (core) ----
    story.append(Paragraph("1. Vertragswerte &amp; Bewertung (Download)", styles["NWH2"]))
    if not c_max and not c_norm and not c_min:
        story.append(Paragraph(
            "Es sind keine Vertragswerte hinterlegt. Tragen Sie in der Konfiguration "
            "die Werte aus Ihrem Produktinformationsblatt ein (Maximum, "
            "„normalerweise zur Verfügung stehend“, Minimum), damit dieser Abschnitt "
            "eine belastbare Bewertung liefert.",
            styles["NWBody"]))
    else:
        ct_rows = [["Vertragswert", "Vereinbart", "Gemessen (Schnitt / min)", "Verhältnis"]]
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
        story.append(Spacer(1, 6))

        ev = _evaluate_contract(a, c_max, c_norm, c_min)
        if ev["has_data"] and ev["criteria"]:
            crit_rows = [["Kriterium (angelehnt an Vfg 99/2021)", "Anforderung", "Ergebnis", "Status"]]
            for name, req, result, ok in ev["criteria"]:
                crit_rows.append([name, req, result, "erfüllt" if ok else "VERLETZT"])
            story.append(_table(crit_rows, col_widths=[4.4*cm, 4.8*cm, 3.6*cm, 2.0*cm]))
            story.append(Spacer(1, 8))
            if ev["deviation"]:
                story.append(_verdict_box(
                    "<b>Befund:</b> Mindestens ein anerkanntes Kriterium ist verletzt — die "
                    "gemessene Leistung weicht erheblich von der vertraglich zugesicherten ab. "
                    "Dies begründet dem Grunde nach ein Minderungs- bzw. Sonderkündigungsrecht "
                    "(formaler Nachweis über das amtliche Verfahren empfohlen).", RED))
            else:
                story.append(_verdict_box(
                    "<b>Befund:</b> Die anerkannten Kriterien werden im Messzeitraum eingehalten. "
                    "Eine erhebliche Geschwindigkeitsabweichung ist nicht belegt.", GREEN))
        # CH 80% rule
        if c_max and a["downs"]:
            ref = c_norm or c_max
            below = sum(1 for v in a["downs"] if v < 0.8 * ref) / len(a["downs"]) * 100
            story.append(Spacer(1, 6))
            story.append(Paragraph(
                f"Zur Schweizer Praxis (Konsumentenschutz/ombudscom): in "
                f"<b>{below:.0f}%</b> der Messungen lag der Download unter 80% von "
                f"{ref:.1f} Mbit/s. Liegt die Leistung überwiegend unter 80% des Zugesicherten, "
                f"bestehen Ansprüche gegenüber der Anbieterin.",
                styles["NWSmall"]))

    # ---- 2. Availability / outages ----
    story.append(Paragraph("2. Verfügbarkeit &amp; Ausfälle", styles["NWH2"]))
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
        story.append(Paragraph("Dokumentierte Anbieter-Ausfälle (Auszug):", styles["NWBody"]))
        out_rows = [["Beginn (lokal)", "Dauer", "Beschreibung"]]
        for e in a["isp_events"][:12]:
            dt = _local(e.get("started_at", ""))
            beg = dt.strftime("%d.%m.%Y %H:%M:%S") if dt else e.get("started_at", "–")
            dur = format_duration(e.get("duration_seconds") or 0) if e.get("ended_at") else "laufend"
            desc = (e.get("description", "") or "")[:58]
            out_rows.append([beg, dur, desc])
        story.append(_table(out_rows, col_widths=[4.4*cm, 2.4*cm, 8.0*cm]))
    story.append(Paragraph(
        "Hinweis: NetWatch zeichnet auch während eines Ausfalls lokal weiter auf — der "
        "Zeitpunkt, die Dauer und die öffentliche IP vor/während/nach jedem Ausfall sind "
        "damit unabhängig belegt, selbst wenn das Internet zeitweise nicht erreichbar war.",
        styles["NWSmall"]))

    story.append(PageBreak())

    # ---- 3. Layer 1: house wiring ----
    story.append(Paragraph("3. Hausverkabelung (eigene Seite)", styles["NWH2"]))
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

    # ---- 4. Layer 2: DSL line ----
    story.append(Paragraph("4. DSL-Leitung (Anbieter-Leitung)", styles["NWH2"]))
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

    # ---- 5. Layer 3: provider network ----
    story.append(Paragraph("5. Anbieternetz (Durchsatz &amp; Abbrüche)", styles["NWH2"]))
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

    # ---- 6. Integrity + legal note ----
    story.append(Paragraph("6. Messintegrität &amp; Hinweise", styles["NWH2"]))
    story.append(Paragraph(
        "Alle Messungen erfolgten automatisiert auf einem dauerhaft laufenden Raspberry Pi, "
        "direkt per Netzwerkkabel mit dem Router verbunden. Zu jeder Messung werden CPU-, RAM- "
        "und Temperaturauslastung des Messgeräts sowie die Messzyklusdauer erfasst, um eine "
        "Verfälschung durch Überlastung auszuschließen. Die Leitungswerte stammen unmittelbar "
        "aus dem Router (TR-064). Die Rohdaten liegen als CSV-Dateien bei und sind nachprüfbar.",
        styles["NWBody"]))
    story.append(Paragraph(
        "Rechtlicher Hinweis: Dieser Bericht ist eine technische Dokumentation und für sich "
        "genommen keine rechtsverbindliche amtliche Messung. Für die Durchsetzung von "
        "Minderung oder Sonderkündigung ist das Messprotokoll direkt beim Anbieter vorzulegen; "
        "empfohlen wird die Kombination mit dem amtlichen Verfahren (DE: Bundesnetzagentur "
        "Breitbandmessung; CH: networktest.ch / Schlichtungsstelle ombudscom).",
        styles["NWSmall"]))

    doc.build(story)
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
