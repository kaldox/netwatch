"""
NetWatch – PDF report generator.
Produces professional ISP-ready reports using ReportLab.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    HRFlowable,
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .statistics import (
    DailyStats,
    MonthlyStats,
    availability_sla_label,
    format_duration,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Colours / styling
# ---------------------------------------------------------------------------

BRAND_BLUE = colors.HexColor("#1a3a5c")
BRAND_LIGHT = colors.HexColor("#e8f0fa")
RED = colors.HexColor("#c0392b")
ORANGE = colors.HexColor("#e67e22")
GREEN = colors.HexColor("#27ae60")
GREY = colors.HexColor("#7f8c8d")
LIGHT_GREY = colors.HexColor("#ecf0f1")


def _styles() -> dict:
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "NwTitle",
            parent=ss["Title"],
            fontSize=22,
            textColor=BRAND_BLUE,
            spaceAfter=6,
        ),
        "subtitle": ParagraphStyle(
            "NwSubtitle",
            parent=ss["Normal"],
            fontSize=12,
            textColor=GREY,
            spaceAfter=12,
        ),
        "h1": ParagraphStyle(
            "NwH1",
            parent=ss["Heading1"],
            fontSize=14,
            textColor=BRAND_BLUE,
            spaceBefore=16,
            spaceAfter=6,
            borderPad=4,
        ),
        "h2": ParagraphStyle(
            "NwH2",
            parent=ss["Heading2"],
            fontSize=11,
            textColor=BRAND_BLUE,
            spaceBefore=10,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "NwBody",
            parent=ss["Normal"],
            fontSize=9,
            spaceAfter=4,
            leading=14,
        ),
        "small": ParagraphStyle(
            "NwSmall",
            parent=ss["Normal"],
            fontSize=8,
            textColor=GREY,
        ),
        "mono": ParagraphStyle(
            "NwMono",
            parent=ss["Code"],
            fontSize=7,
            fontName="Courier",
            leading=10,
        ),
        "center": ParagraphStyle(
            "NwCenter",
            parent=ss["Normal"],
            fontSize=9,
            alignment=TA_CENTER,
        ),
    }


def _avail_color(pct: float) -> colors.Color:
    if pct >= 99.9:
        return GREEN
    elif pct >= 99.0:
        return ORANGE
    else:
        return RED


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class ReportBuilder:
    """Builds a multi-page PDF report."""

    def __init__(
        self,
        output_path: Path,
        hostname: str,
        period_start: date,
        period_end: date,
    ) -> None:
        self._path = output_path
        self._hostname = hostname
        self._start = period_start
        self._end = period_end
        self._story: list = []
        self._styles = _styles()

    # ------------------------------------------------------------------

    def _p(self, text: str, style: str = "body") -> Paragraph:
        return Paragraph(text, self._styles[style])

    def _add(self, *items) -> None:
        self._story.extend(items)

    def _hr(self) -> HRFlowable:
        return HRFlowable(width="100%", thickness=1, color=BRAND_BLUE, spaceAfter=8)

    def _spacer(self, h: float = 0.3) -> Spacer:
        return Spacer(1, h * cm)

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    def add_cover_page(
        self,
        generated_at: Optional[datetime] = None,
    ) -> "ReportBuilder":
        S = self._styles
        if generated_at is None:
            generated_at = datetime.now(timezone.utc)

        self._add(
            self._spacer(4),
            self._p("NetWatch", "title"),
            self._p("Netzwerk-Monitoring-Bericht", "subtitle"),
            self._hr(),
            self._spacer(0.5),
        )

        meta = [
            ["Zeitraum", f"{self._start.isoformat()} bis {self._end.isoformat()}"],
            ["Hostname", self._hostname],
            ["Erstellt am", generated_at.strftime("%d.%m.%Y %H:%M UTC")],
        ]
        t = Table(meta, colWidths=[4 * cm, 12 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), BRAND_LIGHT),
            ("TEXTCOLOR", (0, 0), (0, -1), BRAND_BLUE),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, GREY),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ]))
        self._add(t, self._spacer(1))

        disclaimer = (
            "Dieser Bericht wurde automatisch durch das NetWatch-Monitoring-System erstellt. "
            "Die enthaltenen Messdaten dienen als Beweisgrundlage zur Ursachenanalyse "
            "von Netzwerkunterbrechungen. Alle Zeitangaben in UTC."
        )
        self._add(self._p(disclaimer, "small"), PageBreak())
        return self

    def add_summary(
        self,
        monthly: MonthlyStats,
        events: list[dict[str, Any]],
    ) -> "ReportBuilder":
        self._add(self._p("1. Zusammenfassung", "h1"), self._hr())

        avail_color = _avail_color(monthly.availability_percent)
        sla_label = availability_sla_label(monthly.availability_percent)

        summary_data = [
            ["Kennzahl", "Wert"],
            ["Verfügbarkeit", f"{monthly.availability_percent:.3f}%  ({sla_label})"],
            ["Ausfallzeit gesamt", format_duration(monthly.total_downtime_seconds)],
            ["Anzahl Ereignisse (gesamt)", str(monthly.outage_count)],
            ["ISP-Ausfälle", str(monthly.isp_failure_count)],
            ["Lokale Netzwerkausfälle", str(monthly.local_failure_count)],
            ["DNS-Ausfälle", str(monthly.dns_failure_count)],
            ["Längster Ausfall", format_duration(monthly.longest_outage_seconds)],
            ["Tage mit Ausfällen", str(monthly.days_with_outages)],
        ]
        if monthly.avg_latency_ms is not None:
            summary_data.append(["Durchschn. Latenz", f"{monthly.avg_latency_ms:.1f} ms"])
        if monthly.max_latency_ms is not None:
            summary_data.append(["Max. Latenz", f"{monthly.max_latency_ms:.1f} ms"])

        t = Table(summary_data, colWidths=[8 * cm, 8 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BACKGROUND", (0, 1), (0, -1), BRAND_LIGHT),
            ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, GREY),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GREY]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ]))
        self._add(t, self._spacer())
        return self

    def add_event_table(
        self,
        events: list[dict[str, Any]],
        title: str = "2. Ereignishistorie",
    ) -> "ReportBuilder":
        self._add(self._p(title, "h1"), self._hr())

        if not events:
            self._add(self._p("Keine Ereignisse im angegebenen Zeitraum.", "body"))
            return self

        headers = ["Zeitpunkt", "Typ", "Dauer", "Conf.", "IPv4 vorher", "Beschreibung"]
        rows = [headers]

        for ev in events[:100]:  # cap at 100 rows for PDF
            started = ev.get("started_at", "")[:19].replace("T", " ")
            duration = format_duration(ev.get("duration_seconds") or 0)
            conf = f"{ev.get('confidence_score', 0):.0%}"
            ipv4 = ev.get("public_ipv4_before") or "-"
            desc = (ev.get("description") or "")[:60]
            rows.append([started, ev.get("event_type", ""), duration, conf, ipv4, desc])

        col_widths = [3.5 * cm, 3.5 * cm, 2 * cm, 1.5 * cm, 3 * cm, 5 * cm]
        t = Table(rows, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.3, GREY),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GREY]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("WORDWRAP", (0, 0), (-1, -1), True),
        ]))
        self._add(t, self._spacer())

        if len(events) > 100:
            self._add(self._p(
                f"(Tabelle zeigt die ersten 100 von {len(events)} Ereignissen. "
                "Vollständige Daten in der Datenbank.)", "small"
            ))
        return self

    def add_isp_evidence(
        self,
        isp_events: list[dict[str, Any]],
        traceroutes: list[dict[str, Any]],
    ) -> "ReportBuilder":
        self._add(
            PageBreak(),
            self._p("3. ISP-Nachweis", "h1"),
            self._hr(),
        )

        isp_count = len(isp_events)
        total_isp_downtime = sum(ev.get("duration_seconds") or 0 for ev in isp_events)

        intro = (
            f"Im Berichtszeitraum wurden <b>{isp_count} ISP-bedingte Ausfälle</b> "
            f"mit einer Gesamtausfallzeit von <b>{format_duration(total_isp_downtime)}</b> "
            f"registriert. Bei diesen Ereignissen war das lokale Gateway erreichbar, "
            f"jedoch waren mehrere externe Ziele (1.1.1.1, 8.8.8.8, 9.9.9.9) gleichzeitig "
            f"nicht erreichbar – ein klares Indiz für eine Störung beim Internetprovider."
        )
        self._add(self._p(intro, "body"), self._spacer(0.5))

        if isp_events:
            self.add_event_table(isp_events, title="ISP-Ausfälle im Detail")

        if traceroutes:
            self._add(
                self._p("3.1 Traceroute-Auswertungen", "h2"),
            )
            for tr in traceroutes[:10]:
                ts = tr.get("timestamp", "")[:19].replace("T", " ")
                tool = tr.get("tool", "traceroute")
                target = tr.get("target_host", "")
                self._add(
                    self._p(f"<b>{tool}</b> → {target} | {ts} UTC", "body"),
                    self._p(
                        (tr.get("output") or "")[:2000].replace("\n", "<br/>"),
                        "mono",
                    ),
                    self._spacer(0.3),
                )
        return self

    def add_latency_analysis(
        self,
        daily_stats: list[dict[str, Any]],
    ) -> "ReportBuilder":
        self._add(
            PageBreak(),
            self._p("4. Latenz- und Paketverlustsanalyse", "h1"),
            self._hr(),
        )

        if not daily_stats:
            self._add(self._p("Keine Statistikdaten verfügbar.", "body"))
            return self

        headers = ["Datum", "Verfügb. %", "Ausfallzeit", "Ø Latenz ms", "Max Latenz ms", "Ø Verlust %"]
        rows = [headers]

        for d in sorted(daily_stats, key=lambda x: x["date_str"]):
            rows.append([
                d["date_str"],
                f"{d['availability_percent']:.3f}%",
                format_duration(d["downtime_seconds"]),
                f"{d['avg_latency_ms']:.1f}" if d.get("avg_latency_ms") else "-",
                f"{d['max_latency_ms']:.1f}" if d.get("max_latency_ms") else "-",
                f"{d['avg_packet_loss_percent']:.1f}" if d.get("avg_packet_loss_percent") else "-",
            ])

        t = Table(rows, colWidths=[3 * cm, 3 * cm, 3 * cm, 3 * cm, 3 * cm, 3 * cm], repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.3, GREY),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GREY]),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        self._add(t)
        return self

    def add_ip_history(
        self,
        ip_history: list[dict[str, Any]],
    ) -> "ReportBuilder":
        self._add(
            self._p("5. Öffentliche IP-Adresse – Historie", "h1"),
            self._hr(),
        )
        changes = [r for r in ip_history if r.get("changed")]

        self._add(
            self._p(
                f"Insgesamt {len(changes)} IP-Wechsel im Berichtszeitraum. "
                "Häufige IP-Wechsel können auf Verbindungsunterbrechungen seitens des ISP hinweisen.",
                "body",
            ),
        )

        if ip_history:
            headers = ["Zeitpunkt", "IPv4", "IPv6", "Geändert"]
            rows = [headers]
            for r in ip_history[:50]:
                ts = r.get("timestamp", "")[:19].replace("T", " ")
                rows.append([
                    ts,
                    r.get("ipv4") or "-",
                    r.get("ipv6") or "-",
                    "Ja" if r.get("changed") else "Nein",
                ])
            t = Table(rows, colWidths=[4 * cm, 4 * cm, 6 * cm, 2.5 * cm], repeatRows=1)
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), BRAND_BLUE),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.3, GREY),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GREY]),
            ]))
            self._add(t)
        return self

    def add_conclusion(
        self,
        monthly: MonthlyStats,
    ) -> "ReportBuilder":
        self._add(
            PageBreak(),
            self._p("6. Schlussfolgerung", "h1"),
            self._hr(),
        )

        # Build evidence-level assessment
        if monthly.isp_failure_count > 0 and monthly.local_failure_count == 0:
            verdict = (
                f"Die Analyse ergibt klare Hinweise auf <b>ISP-seitige Störungen</b>. "
                f"Von {monthly.outage_count} registrierten Ereignissen sind "
                f"{monthly.isp_failure_count} als ISP-Ausfall klassifiziert. "
                f"Das lokale Netzwerk (Gateway) war dabei stets erreichbar."
            )
        elif monthly.local_failure_count > 0 and monthly.isp_failure_count == 0:
            verdict = (
                f"Die Ausfälle sind auf <b>lokale Netzwerkprobleme</b> zurückzuführen "
                f"(Gateway nicht erreichbar). Kein ISP-Ausfall nachgewiesen."
            )
        elif monthly.isp_failure_count > 0 and monthly.local_failure_count > 0:
            verdict = (
                f"Es wurden sowohl <b>ISP-Ausfälle</b> ({monthly.isp_failure_count}×) als auch "
                f"<b>lokale Netzwerkprobleme</b> ({monthly.local_failure_count}×) festgestellt."
            )
        elif monthly.outage_count == 0:
            verdict = (
                "Im Berichtszeitraum wurden <b>keine Ausfälle</b> festgestellt. "
                f"Die Verfügbarkeit beträgt {monthly.availability_percent:.3f}%."
            )
        else:
            verdict = (
                f"Im Berichtszeitraum wurden {monthly.outage_count} Ereignisse registriert. "
                "Eine eindeutige Ursachenzuweisung erfordert weitere Analyse."
            )

        self._add(
            self._p(verdict, "body"),
            self._spacer(0.5),
            self._p(
                "Dieser Bericht wurde automatisch erstellt. Alle Rohdaten sind in der "
                "NetWatch-SQLite-Datenbank gespeichert und können auf Anfrage bereitgestellt werden.",
                "small",
            ),
        )
        return self

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self) -> Path:
        """Render the PDF to disk and return the path."""
        self._path.parent.mkdir(parents=True, exist_ok=True)

        doc = SimpleDocTemplate(
            str(self._path),
            pagesize=A4,
            rightMargin=2 * cm,
            leftMargin=2 * cm,
            topMargin=2 * cm,
            bottomMargin=2 * cm,
            title="NetWatch Bericht",
            author="NetWatch",
        )

        def _footer(canvas, doc):
            canvas.saveState()
            canvas.setFont("Helvetica", 7)
            canvas.setFillColor(GREY)
            page_num = canvas.getPageNumber()
            canvas.drawString(2 * cm, 1.2 * cm, "NetWatch – Automatisch generierter Monitoring-Bericht")
            canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Seite {page_num}")
            canvas.restoreState()

        doc.build(self._story, onFirstPage=_footer, onLaterPages=_footer)
        logger.info("PDF report generated: %s", self._path)
        return self._path


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------


def generate_monthly_report(
    output_dir: Path,
    hostname: str,
    year: int,
    month: int,
    monthly_stats: MonthlyStats,
    all_events: list[dict[str, Any]],
    isp_events: list[dict[str, Any]],
    traceroutes: list[dict[str, Any]],
    daily_stats: list[dict[str, Any]],
    ip_history: list[dict[str, Any]],
) -> Path:
    """Generate a complete monthly PDF report."""
    period_start = date(year, month, 1)
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    period_end = date(year, month, last_day)

    filename = f"netwatch_report_{year}_{month:02d}.pdf"
    output_path = output_dir / filename

    builder = ReportBuilder(output_path, hostname, period_start, period_end)
    (
        builder
        .add_cover_page()
        .add_summary(monthly_stats, all_events)
        .add_event_table(all_events)
        .add_isp_evidence(isp_events, traceroutes)
        .add_latency_analysis(daily_stats)
        .add_ip_history(ip_history)
        .add_conclusion(monthly_stats)
        .build()
    )
    return output_path
