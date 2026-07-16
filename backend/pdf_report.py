"""
Reportes PDF — diseño propio, independiente del look del dashboard web.
Un reporte de una sola pasada: encabezado + filtros aplicados + KPIs +
gráfica de evolución + comparativo (top 20) + tabla completa (pagina sola).
"""
from __future__ import annotations

import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
)
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import HorizontalBarChart
from reportlab.graphics.charts.linecharts import HorizontalLineChart

NAVY = colors.HexColor("#1E3A6E")
GREEN = colors.HexColor("#8CC63F")
MUTED = colors.HexColor("#7C879B")
INK = colors.HexColor("#1B2A4A")
LINE = colors.HexColor("#E4E8F0")
SURFACE = colors.HexColor("#F4F6FA")

styles = getSampleStyleSheet()
TITLE = ParagraphStyle("rTitle", parent=styles["Title"], fontName="Helvetica-Bold",
                        fontSize=19, textColor=NAVY, spaceAfter=2, leading=22)
SUBTITLE = ParagraphStyle("rSubtitle", parent=styles["Normal"], fontSize=9.5,
                           textColor=MUTED, spaceAfter=14, leading=13)
H2 = ParagraphStyle("rH2", parent=styles["Heading2"], fontName="Helvetica-Bold",
                     fontSize=12.5, textColor=NAVY, spaceBefore=16, spaceAfter=7)
FOOTNOTE = ParagraphStyle("rFoot", parent=styles["Normal"], fontSize=7.5,
                           textColor=MUTED, spaceBefore=4)


def _draw_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, "Ingenes Institute — Dashboards Inteligencia Médica")
    canvas.drawRightString(doc.pagesize[0] - 18 * mm, 10 * mm, f"Página {canvas.getPageNumber()}")
    canvas.restoreState()


def _kpi_table(headers: list[str], values: list[str]) -> Table:
    t = Table([headers, values], colWidths=[(letter[0] - 36 * mm) / len(headers)] * len(headers))
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
        ("BACKGROUND", (0, 1), (-1, 1), SURFACE),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 1), (-1, 1), 15),
        ("TEXTCOLOR", (0, 1), (-1, 1), INK),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 0), 6), ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, 1), 9), ("BOTTOMPADDING", (0, 1), (-1, 1), 11),
        ("GRID", (0, 0), (-1, -1), 0.6, LINE),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, LINE),
    ]))
    return t


def _line_chart(serie: list[dict]) -> Drawing:
    vals = [float(s["tasa"]) if s["tasa"] is not None else 0.0 for s in serie]
    d = Drawing(letter[0] - 36 * mm, 130)
    lc = HorizontalLineChart()
    lc.x, lc.y = 34, 26
    lc.width, lc.height = d.width - 50, 88
    lc.data = [vals]
    lc.categoryAxis.categoryNames = [s["mes"][2:] for s in serie]
    lc.categoryAxis.labels.fontSize = 6
    lc.categoryAxis.labels.angle = 0
    lc.categoryAxis.labels.dy = -8
    lc.valueAxis.valueMin = 0
    lc.valueAxis.labelTextFormat = "%d%%"
    lc.valueAxis.labels.fontSize = 6.5
    lc.lines[0].strokeColor = NAVY
    lc.lines[0].strokeWidth = 2
    lc.lines[0].symbol = None
    d.add(lc)
    return d


def _bar_chart(items: list[dict], top_n: int = 20) -> Drawing:
    top = items[:top_n]
    rows = list(reversed(top))  # #1 arriba
    row_h = 15
    d = Drawing(letter[0] - 36 * mm, row_h * len(rows) + 30)
    bc = HorizontalBarChart()
    bc.x, bc.y = 140, 8
    bc.width, bc.height = d.width - 160, row_h * len(rows)
    bc.data = [[float(r["tasa"]) if r["tasa"] is not None else 0.0 for r in rows]]
    bc.categoryAxis.categoryNames = [
        ((r["clave"] or "—")[:26] + "…") if r["clave"] and len(r["clave"]) > 26 else (r["clave"] or "—")
        for r in rows
    ]
    bc.categoryAxis.labels.fontSize = 6.5
    bc.valueAxis.valueMin = 0
    bc.bars[0].fillColor = GREEN
    bc.barLabels.fontSize = 6.5
    bc.barLabelFormat = "%.1f%%"
    bc.barLabels.nudge = 8
    d.add(bc)
    return d


def _detalle_table(header: list[str], rows: list[list[str]], col_widths: list[float]) -> Table:
    data = [header] + rows
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.3),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (1, -1), "LEFT"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), SURFACE))
    t.setStyle(TableStyle(style))
    return t


def _fmt_int(n) -> str:
    return f"{n:,.0f}" if n is not None else "0"


def _fmt_pct(n) -> str:
    return f"{n:.1f}%" if n is not None else "—"


def build_blastos_pdf(data: dict, meta_lines: list[str], num_label: str, den_label: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=18 * mm, bottomMargin=18 * mm,
                             leftMargin=18 * mm, rightMargin=18 * mm)
    story = [
        Paragraph("Reporte — Tasa de blastos sobre óvulos", TITLE),
        Paragraph(f"{num_label} ÷ {den_label} · Generado {datetime.now().strftime('%d/%m/%Y %H:%M')}"
                   f"<br/>Filtros: {' · '.join(meta_lines)}", SUBTITLE),
    ]
    kpis = data["kpis"]
    story.append(_kpi_table(
        ["Tasa de blastos", "Blastos", "Óvulos", "Ciclos"],
        [_fmt_pct(kpis["tasa"]), _fmt_int(kpis["blastos"]), _fmt_int(kpis["ovulos"]), _fmt_int(kpis["ciclos"])],
    ))

    if data["serie"]:
        story.append(Paragraph("Evolución mensual de la tasa", H2))
        story.append(_line_chart(data["serie"]))

    if data["ranking"]["medico"]:
        n = min(20, len(data["ranking"]["medico"]))
        story.append(Paragraph(f"Comparativo por médico — top {n} de {len(data['ranking']['medico'])}", H2))
        story.append(_bar_chart(data["ranking"]["medico"], top_n=20))

    story.append(Paragraph(f"Detalle por médico y sucursal ({len(data['tabla'])} combinaciones)", H2))
    tot = kpis["ciclos"] or 1
    rows = [[
        r["medico"] or "—", r["sucursal"] or "—", _fmt_int(r["ciclos"]),
        f"{100 * r['ciclos'] / tot:.1f}%", _fmt_int(r["ovulos"]), _fmt_int(r["blastos"]), _fmt_pct(r["tasa"]),
    ] for r in data["tabla"]]
    w = letter[0] - 36 * mm
    story.append(_detalle_table(
        ["Médico", "Sucursal", "Ciclos", "% total", "Óvulos", "Blastos", "Tasa"],
        rows, [w * .30, w * .18, w * .10, w * .10, w * .11, w * .11, w * .10],
    ))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Tasa = Σ blastos ÷ Σ óvulos del grupo — nunca el promedio de tasas por ciclo.", FOOTNOTE))

    doc.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    return buf.getvalue()


def build_embarazo_pdf(data: dict, meta_lines: list[str]) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=18 * mm, bottomMargin=18 * mm,
                             leftMargin=18 * mm, rightMargin=18 * mm)
    story = [
        Paragraph("Reporte — Tasa de embarazo", TITLE),
        Paragraph(f"Positivo ÷ (Positivo + Negativo) · Generado {datetime.now().strftime('%d/%m/%Y %H:%M')}"
                   f"<br/>Filtros: {' · '.join(meta_lines)}", SUBTITLE),
    ]
    kpis = data["kpis"]
    story.append(_kpi_table(
        ["Tasa de embarazo", "Positivos", "Negativos", "Ciclos"],
        [_fmt_pct(kpis["tasa"]), _fmt_int(kpis["positivos"]), _fmt_int(kpis["negativos"]), _fmt_int(kpis["ciclos"])],
    ))

    if data["serie"]:
        story.append(Paragraph("Evolución mensual de la tasa", H2))
        story.append(_line_chart(data["serie"]))

    if data["ranking"]["medico"]:
        n = min(20, len(data["ranking"]["medico"]))
        story.append(Paragraph(f"Comparativo por médico — top {n} de {len(data['ranking']['medico'])}", H2))
        story.append(_bar_chart(data["ranking"]["medico"], top_n=20))

    story.append(Paragraph(f"Detalle por médico y sucursal ({len(data['tabla'])} combinaciones)", H2))
    tot = kpis["ciclos"] or 1
    rows = [[
        r["medico"] or "—", r["sucursal"] or "—", _fmt_int(r["ciclos"]),
        f"{100 * r['ciclos'] / tot:.1f}%", _fmt_int(r["positivos"]), _fmt_int(r["negativos"]), _fmt_pct(r["tasa"]),
    ] for r in data["tabla"]]
    w = letter[0] - 36 * mm
    story.append(_detalle_table(
        ["Médico", "Sucursal", "Ciclos", "% total", "Positivos", "Negativos", "Tasa"],
        rows, [w * .30, w * .18, w * .10, w * .10, w * .11, w * .11, w * .10],
    ))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Tasa = positivos ÷ total del grupo — nunca el promedio de tasas por ciclo.", FOOTNOTE))

    doc.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    return buf.getvalue()
