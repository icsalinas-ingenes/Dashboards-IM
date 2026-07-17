"""
Reportes PDF — diseño propio, independiente del look del dashboard web.
Encabezado + filtros aplicados + KPIs + gráfica de evolución + comparativos
(médico/sucursal, general y por origen de óvulos, paginados) + tabla completa.
"""
from __future__ import annotations

import io
from datetime import datetime
from math import ceil
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image,
)
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import HorizontalBarChart
from reportlab.graphics.charts.linecharts import HorizontalLineChart

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
LOGO_INGENES = ASSETS_DIR / "logo_ingenes.png"
LOGO_20ANIOS = ASSETS_DIR / "logo_20anios.jpg"

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
SUBHEAD = ParagraphStyle("rSubhead", parent=styles["Normal"], fontName="Helvetica-Bold",
                          fontSize=10, textColor=INK, spaceBefore=10, spaceAfter=3)
FOOTNOTE = ParagraphStyle("rFoot", parent=styles["Normal"], fontSize=7.5,
                           textColor=MUTED, spaceBefore=4)
LEGEND = ParagraphStyle("rLegend", parent=styles["Normal"], fontSize=9,
                         textColor=INK, spaceAfter=6)
CARD_LABEL = ParagraphStyle("rCardLabel", parent=styles["Normal"], fontName="Helvetica-Bold",
                             fontSize=9.5, textColor=MUTED, spaceAfter=4)
CARD_VALUE = ParagraphStyle("rCardValue", parent=styles["Normal"], fontName="Helvetica-Bold",
                             fontSize=24, textColor=INK, leading=27, spaceAfter=4)
CARD_SUB = ParagraphStyle("rCardSub", parent=styles["Normal"], fontSize=9.5, textColor=MUTED)

ORIGEN_LABELS = {"Propios": "Propios", "Ovodon": "Ovodonación"}
ORIGEN_COLORS = {"Propios": NAVY, "Ovodon": GREEN}

# Filas por página de gráfica: cada Drawing es un flowable atómico (no se
# parte entre páginas), así que si no cabe en una página reportlab truena.
# Con row_h=17/23pt y una página carta ya sin márgenes (~690pt), esto deja
# margen de sobra para el título que va antes en la misma página.
BAR_ROWS_PER_PAGE = 36
ORIGEN_BAR_ROWS_PER_PAGE = 24


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _draw_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, "Ingenes Institute — Dashboards Inteligencia Médica")
    canvas.drawRightString(doc.pagesize[0] - 18 * mm, 10 * mm, f"Página {canvas.getPageNumber()}")
    canvas.restoreState()


def _header_logos() -> Table:
    content_w = letter[0] - 36 * mm
    ingenes = Image(str(LOGO_INGENES), width=42, height=33.6)
    ingenes.hAlign = "LEFT"
    anios = Image(str(LOGO_20ANIOS), width=68, height=27.2)
    anios.hAlign = "RIGHT"
    t = Table([[ingenes, anios]], colWidths=[content_w / 2, content_w / 2])
    t.setStyle(TableStyle([
        ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, LINE),
    ]))
    return t


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


MAX_MONTH_LABELS = 18


def _line_chart(serie: list[dict]) -> Drawing:
    vals = [float(s["tasa"]) if s["tasa"] is not None else 0.0 for s in serie]
    n = len(serie)
    # Con series largas (varios años de mensualidades) un label por mes se
    # amontona; se etiqueta solo cada k-ésimo punto y se rota para que quepa.
    step = max(1, ceil(n / MAX_MONTH_LABELS))
    d = Drawing(letter[0] - 36 * mm, 150)
    lc = HorizontalLineChart()
    lc.x, lc.y = 36, 40
    lc.width, lc.height = d.width - 52, 92
    lc.data = [vals]
    lc.categoryAxis.categoryNames = [s["mes"][2:] if i % step == 0 else "" for i, s in enumerate(serie)]
    lc.categoryAxis.labels.fontName = "Helvetica-Bold"
    lc.categoryAxis.labels.fontSize = 8
    lc.categoryAxis.labels.fillColor = INK
    lc.categoryAxis.labels.angle = 40 if step > 1 else 0
    lc.categoryAxis.labels.boxAnchor = "ne" if step > 1 else "n"
    lc.categoryAxis.labels.dx = -2
    lc.categoryAxis.labels.dy = -6
    lc.valueAxis.valueMin = 0
    lc.valueAxis.labelTextFormat = "%d%%"
    lc.valueAxis.labels.fontName = "Helvetica-Bold"
    lc.valueAxis.labels.fontSize = 8
    lc.valueAxis.labels.fillColor = INK
    lc.lines[0].strokeColor = NAVY
    lc.lines[0].strokeWidth = 2.2
    lc.lines[0].symbol = None
    d.add(lc)
    return d


def _bar_chart(items: list[dict]) -> Drawing:
    rows = list(reversed(items))  # #1 arriba
    row_h = 17
    d = Drawing(letter[0] - 36 * mm, row_h * len(rows) + 30)
    bc = HorizontalBarChart()
    bc.x, bc.y = 150, 8
    bc.width, bc.height = d.width - 195, row_h * len(rows)
    bc.data = [[float(r["tasa"]) if r["tasa"] is not None else 0.0 for r in rows]]
    bc.categoryAxis.categoryNames = [
        ((r["clave"] or "—")[:26] + "…") if r["clave"] and len(r["clave"]) > 26 else (r["clave"] or "—")
        for r in rows
    ]
    bc.categoryAxis.labels.fontName = "Helvetica-Bold"
    bc.categoryAxis.labels.fontSize = 8
    bc.categoryAxis.labels.fillColor = INK
    bc.valueAxis.valueMin = 0
    bc.valueAxis.labels.fontSize = 7.5
    bc.valueAxis.labels.fillColor = MUTED
    bc.bars[0].fillColor = GREEN
    bc.barLabels.fontName = "Helvetica-Bold"
    bc.barLabels.fontSize = 8
    bc.barLabels.fillColor = INK
    bc.barLabels.boxAnchor = "w"  # si no, reportlab centra el label sobre la punta de la barra
    bc.barLabelFormat = "%.1f%%"
    bc.barLabels.nudge = 6
    d.add(bc)
    return d


def _ranking_section(story: list, title: str, items: list[dict]) -> None:
    if not items:
        return
    story.append(Paragraph(f"{title} ({len(items)})", H2))
    for chunk in _chunks(items, BAR_ROWS_PER_PAGE):
        story.append(_bar_chart(chunk))


def _origen_summary_table(general: list[dict]) -> Table:
    # Cada card es una celda con 3 flowables apilados (label/valor/ciclos) en
    # vez de 3 filas de tabla con padding manual — así reportlab reserva el
    # alto real de cada línea (fontSize 24 en el valor) y no se encima nada.
    cells, accents = [], []
    for g in general:
        label = ORIGEN_LABELS.get(g["origen"], g["origen"])
        cells.append([
            Paragraph(label.upper(), CARD_LABEL),
            Paragraph(_fmt_pct(g["tasa"]), CARD_VALUE),
            Paragraph(f"{_fmt_int(g['ciclos'])} ciclos", CARD_SUB),
        ])
        accents.append(ORIGEN_COLORS.get(g["origen"], MUTED))
    w = (letter[0] - 36 * mm) / max(len(cells), 1)
    t = Table([cells], colWidths=[w] * len(cells))
    style = [
        ("BACKGROUND", (0, 0), (-1, -1), SURFACE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 16),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 11),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
    ]
    for i, color in enumerate(accents):
        style.append(("LINEBEFORE", (i, 0), (i, 0), 3, color))
    t.setStyle(TableStyle(style))
    return t


def _origen_bar_chart(items: list[dict]) -> Drawing:
    rows = list(reversed(items))  # #1 arriba
    row_h = 23
    d = Drawing(letter[0] - 36 * mm, row_h * len(rows) + 30)
    bc = HorizontalBarChart()
    bc.x, bc.y = 150, 8
    bc.width, bc.height = d.width - 195, row_h * len(rows)
    bc.data = [
        [float(r["propios"]["tasa"]) if r["propios"] and r["propios"]["tasa"] is not None else 0.0 for r in rows],
        [float(r["ovodon"]["tasa"]) if r["ovodon"] and r["ovodon"]["tasa"] is not None else 0.0 for r in rows],
    ]
    bc.categoryAxis.categoryNames = [
        ((r["clave"] or "—")[:26] + "…") if r["clave"] and len(r["clave"]) > 26 else (r["clave"] or "—")
        for r in rows
    ]
    bc.categoryAxis.labels.fontName = "Helvetica-Bold"
    bc.categoryAxis.labels.fontSize = 8
    bc.categoryAxis.labels.fillColor = INK
    bc.valueAxis.valueMin = 0
    bc.valueAxis.labels.fontSize = 7.5
    bc.valueAxis.labels.fillColor = MUTED
    bc.groupSpacing = 5
    bc.bars[0].fillColor = NAVY
    bc.bars[1].fillColor = GREEN
    bc.barLabels.fontName = "Helvetica-Bold"
    bc.barLabels.fontSize = 7.5
    bc.barLabels.fillColor = INK
    bc.barLabels.boxAnchor = "w"
    bc.barLabelFormat = "%.1f%%"
    bc.barLabels.nudge = 6
    d.add(bc)
    return d


def _origen_section(story: list, subtitle: str, items: list[dict]) -> None:
    if not items:
        return

    def total_ciclos(r):
        p = r["propios"]["ciclos"] if r["propios"] else 0
        o = r["ovodon"]["ciclos"] if r["ovodon"] else 0
        return p + o

    ordered = sorted(items, key=total_ciclos, reverse=True)
    story.append(Paragraph(f"{subtitle} ({len(ordered)})", SUBHEAD))
    story.append(Paragraph(
        '<font color="#1E3A6E">—</font> Propios &nbsp;&nbsp;&nbsp; '
        '<font color="#8CC63F">—</font> Ovodonación', LEGEND,
    ))
    for chunk in _chunks(ordered, ORIGEN_BAR_ROWS_PER_PAGE):
        story.append(_origen_bar_chart(chunk))


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
        _header_logos(),
        Spacer(1, 4),
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

    _ranking_section(story, "Comparativo por médico", data["ranking"]["medico"])
    _ranking_section(story, "Comparativo por sucursal", data["ranking"]["sucursal"])

    origen = data.get("origen") or {}
    if origen.get("general"):
        story.append(Paragraph("Comparativo por origen de óvulos", H2))
        story.append(_origen_summary_table(origen["general"]))
        story.append(Spacer(1, 6))
        _origen_section(story, "Por médico", origen.get("medico") or [])
        _origen_section(story, "Por sucursal", origen.get("sucursal") or [])
        story.append(Paragraph(
            "Cada barra es Σ blastos ÷ Σ óvulos del grupo, separado por origen del óvulo.", FOOTNOTE,
        ))

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
        _header_logos(),
        Spacer(1, 4),
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

    _ranking_section(story, "Comparativo por médico", data["ranking"]["medico"])
    _ranking_section(story, "Comparativo por sucursal", data["ranking"]["sucursal"])

    origen = data.get("origen") or {}
    if origen.get("general"):
        story.append(Paragraph("Comparativo por origen de óvulos", H2))
        story.append(_origen_summary_table(origen["general"]))
        story.append(Spacer(1, 6))
        _origen_section(story, "Por médico", origen.get("medico") or [])
        _origen_section(story, "Por sucursal", origen.get("sucursal") or [])
        story.append(Paragraph(
            "Cada barra es positivos ÷ total del grupo, separado por origen del óvulo.", FOOTNOTE,
        ))

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
