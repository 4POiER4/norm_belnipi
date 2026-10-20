from __future__ import annotations

import io
import os
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _register_fonts() -> tuple[str, str]:
    pairs = [
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ),
        (
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        ),
        ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
        ("C:/Windows/Fonts/calibri.ttf", "C:/Windows/Fonts/calibrib.ttf"),
    ]
    for regular, bold in pairs:
        if not os.path.isfile(regular):
            continue
        try:
            pdfmetrics.registerFont(TTFont("BelenerSans", regular))
            if os.path.isfile(bold):
                pdfmetrics.registerFont(TTFont("BelenerSans-Bold", bold))
                return "BelenerSans", "BelenerSans-Bold"
            return "BelenerSans", "BelenerSans"
        except Exception:
            continue
    return "Helvetica", "Helvetica-Bold"


_TWO_LINE_HEADERS = (
    ("Введен", "Стройдок"),
    ("Отменен", "Стройдок"),
    ("Введен", "Стройпрофи"),
    ("Отменен", "Стройпрофи"),
    ("Введен", "ТНПА"),
    ("Отменен", "ТНПА"),
)


def _esc(text: Any) -> str:
    s = str(text or "")
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _format_header_html(header: Any) -> str:
    """Двухстрочные заголовки дат: «Введен» + «Стройдок|ТНПА» без пробела между строками."""
    text = str(header or "—").strip() or "—"
    # Явный перевод строки из клиента (spans / <br>)
    parts = [p.strip() for p in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if p.strip()]
    if len(parts) >= 2:
        return "<br/>".join(_esc(p) for p in parts[:2])
    flat = "".join(text.split())
    for line1, line2 in _TWO_LINE_HEADERS:
        if flat == f"{line1}{line2}":
            return f"{_esc(line1)}<br/>{_esc(line2)}"
    return _esc(text)


def _compute_summary_from_rows(payload: dict[str, Any]) -> str:
    rows = payload.get("rows") or []
    if not rows:
        return ""
    headers = [str(x or "") for x in (payload.get("headers") or [])]
    has_normacs = any("normacs" in h.casefold() for h in headers)
    rb_label = "Стройпрофи" if any("стройпрофи" in h.casefold() for h in headers) else "Стройдок"
    has_stn = any(x in h.casefold() for h in headers for x in ("стройдок", "стройпрофи"))
    if has_normacs and has_stn and payload.get("combined"):
        stn_col = next((i for i, h in enumerate(headers) if h.casefold() == rb_label.casefold()), -1)
        nc_col = next((i for i, h in enumerate(headers) if h.casefold() == "normacs"), -1)
        found_stn = sum(bool((row.get("cells") or [])[stn_col].get("href")) for row in rows
                        if 0 <= stn_col < len(row.get("cells") or []))
        found_nc = sum(bool((row.get("cells") or [])[nc_col].get("href")) for row in rows
                       if 0 <= nc_col < len(row.get("cells") or []))
        active = sum(row.get("fill") == "active" for row in rows)
        return (f"Всего в документе: {len(rows)}; найдено в {rb_label}: {found_stn}; "
                f"найдено в NormaCS: {found_nc};\nдействует хотя бы в одном: {active}")
    normacs_only = any("normacs" in h.casefold() for h in headers) and not any(
        "стройдок" in h.casefold() or "стройпрофи" in h.casefold() or "ипс" == h.casefold() for h in headers
    )
    if normacs_only:
        link_col = next((i for i, h in enumerate(headers) if h.casefold() == "normacs"), -1)
        status_col = next((i for i, h in enumerate(headers) if "статус" in h.casefold() and "normacs" in h.casefold()), -1)
        found = sum(bool((row.get("cells") or [])[link_col].get("href")) for row in rows
                    if 0 <= link_col < len(row.get("cells") or []))
        active = sum(str((row.get("cells") or [])[status_col].get("text") or "").strip().casefold().startswith("действует")
                     for row in rows if 0 <= status_col < len(row.get("cells") or []))
        return f"Всего в документе: {len(rows)}; найдено в NormaCS: {found};\nдействует: {active}"
    has_tnpa = any("тнпа" in h.casefold() for h in headers)
    total = len(rows)
    found_ips = 0
    found_tnpa = 0
    active = 0
    for row in rows:
        cells = row.get("cells") or []
        fill = str(row.get("fill") or "").strip()
        if has_tnpa and 5 <= len(cells) < 9:
            if str(cells[1].get("href") or "").strip():
                found_ips += 1
            if str(cells[2].get("href") or "").strip():
                found_tnpa += 1
            if fill == "active":
                active += 1
            continue
        if not has_tnpa and len(cells) >= 2:
            if str(cells[1].get("href") or "").strip():
                found_ips += 1
            if fill == "active":
                active += 1
            continue
        if len(cells) >= 3 and str(cells[2].get("href") or "").strip():
            found_ips += 1
        if has_tnpa and len(cells) >= 4 and str(cells[3].get("href") or "").strip():
            found_tnpa += 1
        if fill == "active":
            active += 1
        elif len(cells) >= 9:
            status = str(cells[8].get("text") or "").strip().casefold()
            if status == "актуален" or "актуален" in status:
                active += 1
    if has_tnpa:
        return (
            f"Всего в документе: {total}; найдено в {rb_label}: {found_ips}; "
            f"найдено в ТНПА: {found_tnpa};\nактуально: {active}"
        )
    return (
        f"Всего в документе: {total}; найдено в {rb_label}: {found_ips};\n"
        f"актуально: {active}"
    )


def _parse_summary(summary: str) -> tuple[str, str, str, str]:
    import re

    m = re.search(
        r"Всего в документе:\s*(\d+);\s*найдено в (?:ИПС|Стройдок|Стройпрофи):\s*(\d+);\s*"
        r"найдено в ТНПА:\s*(\d+);[\s\n]*(?:актуально|найдено хотя бы в одном):\s*(\d+)",
        summary or "",
        re.I,
    )
    if m:
        return m.group(1), m.group(2), m.group(3), m.group(4)
    m2 = re.search(
        r"Всего в документе:\s*(\d+);\s*найдено в (?:ИПС|Стройдок|Стройпрофи):\s*(\d+);"
        r"[\s\n]*(?:актуально|найдено хотя бы в одном):\s*(\d+)",
        summary or "",
        re.I,
    )
    if m2:
        return m2.group(1), m2.group(2), "", m2.group(3)
    return "", "", "", ""


def _parse_normacs_summary(summary: str) -> tuple[str, str, str]:
    import re

    match = re.search(
        r"Всего в документе:\s*(\d+);\s*найдено в NormaCS:\s*(\d+);\s*действует:\s*(\d+)",
        summary or "", re.I,
    )
    return match.groups() if match else ("", "", "")


def _parse_mixed_summary(summary: str) -> tuple[str, str, str, str, str, str]:
    import re

    patterns = (r"Всего в документе:\s*(\d+)", r"найдено в (?:Стройдок|Стройпрофи):\s*(\d+)",
                r"найдено в NormaCS:\s*(\d+)", r"действует хотя бы в одном:\s*(\d+)",
                r"частично:\s*(\d+)", r"расхождения:\s*(\d+)")
    return tuple((m.group(1) if (m := re.search(p, summary or "", re.I)) else "") for p in patterns)


def _sheet_from_meta(meta_lines: list[str]) -> str:
    import re

    for line in meta_lines:
        m = re.match(r"^Листов в файле:\s*(\d+)", line.strip(), re.I)
        if m:
            return m.group(1)
    for line in meta_lines:
        m = re.match(r"^Листов:\s*(\d+)\s*$", line.strip(), re.I)
        if m:
            return m.group(1)
    for line in meta_lines:
        m = re.match(r"^Лист:\s*(\d+)\s*$", line.strip(), re.I)
        if m:
            return m.group(1)
    return ""


def build_normative_pdf_bytes(payload: dict[str, Any]) -> bytes:
    # Палитра чата: белые карточки, серые границы, синие ссылки
    c_surface = colors.HexColor("#ffffff")
    c_border = colors.HexColor("#dde5ef")
    c_text = colors.HexColor("#1a2332")
    c_text_sec = colors.HexColor("#5a6b82")
    c_text_muted = colors.HexColor("#8b9bb0")
    c_accent = colors.HexColor("#0f766e")
    c_row_active = colors.HexColor("#e8f5ec")
    c_row_canceled = colors.HexColor("#fce9ea")
    c_row_replaced = colors.HexColor("#fff8e6")
    pad_h = 4
    pad_v = 3
    pad_h_card = 8
    pad_v_card = 6

    font_name, font_bold = _register_fonts()
    buf = io.BytesIO()
    # Фиксированные поля A4 landscape — одинаково в любом браузере/ОС
    page_w, page_h = landscape(A4)
    left_m = 9 * mm
    right_m = 9 * mm
    doc = SimpleDocTemplate(
        buf,
        pagesize=(page_w, page_h),
        leftMargin=left_m,
        rightMargin=right_m,
        topMargin=10 * mm,
        bottomMargin=9 * mm,
        title=str(payload.get("title") or "Таблица нормативов"),
        author="Belener",
        creator="Belener normative export",
    )
    # Одинаковые даты в метаданных → байтово стабильный PDF на разных ПК
    def _fix_metadata(canvas, _doc):
        canvas.setTitle(str(payload.get("title") or "Таблица нормативов"))
        canvas.setAuthor("Belener")
        canvas.setCreator("Belener normative export")

    avail_w = page_w - left_m - right_m

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "BelenerTitle",
        parent=styles["Heading2"],
        fontName=font_name,
        fontSize=14,
        leading=17,
        textColor=c_text,
        spaceAfter=0,
    )
    subtitle_style = ParagraphStyle(
        "BelenerSubtitle",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=8,
        leading=10,
        textColor=c_text_sec,
    )
    card_label_style = ParagraphStyle(
        "BelenerCardLabel",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=7.0,
        leading=8.2,
        textColor=c_text_sec,
        alignment=TA_CENTER,
    )
    card_value_style = ParagraphStyle(
        "BelenerCardValue",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=12,
        leading=14,
        textColor=c_accent,
        alignment=TA_CENTER,
    )
    info_label_style = ParagraphStyle(
        "BelenerInfoLabel",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=7.4,
        leading=8.8,
        textColor=c_text_muted,
    )
    info_value_style = ParagraphStyle(
        "BelenerInfoValue",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=8.4,
        leading=10,
        textColor=c_accent,
    )
    info_file_style = ParagraphStyle(
        "BelenerInfoFile",
        parent=info_value_style,
        textColor=c_accent,
    )
    cell_style = ParagraphStyle(
        "BelenerCell",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=8.2,
        leading=9.6,
        textColor=c_text,
    )
    header_style = ParagraphStyle(
        "BelenerHeader",
        parent=cell_style,
        fontName=font_bold,
        fontSize=8.2,
        leading=8.4,
        alignment=TA_CENTER,
        textColor=colors.white,
        spaceBefore=0,
        spaceAfter=0,
    )

    meta_lines = [str(x).strip() for x in (payload.get("meta") or []) if str(x).strip()]
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        summary = _compute_summary_from_rows(payload)

    story = []
    raw_headers = [str(x or "—") for x in (payload.get("headers") or [])]
    rb_label = "Стройпрофи" if any("стройпрофи" in h.casefold() for h in raw_headers) else "Стройдок"
    has_normacs_header = any("normacs" in h.casefold() for h in raw_headers)
    normacs_only = has_normacs_header and not any(x in h.casefold() for h in raw_headers for x in ("стройдок", "стройпрофи"))
    mixed_normacs = (has_normacs_header and not normacs_only
                     and any(x in h.casefold() for h in raw_headers for x in ("стройдок", "стройпрофи"))
                     and (payload.get("combined") or "действует хотя бы в одном" in summary.casefold()))
    partial = conflicts = ""
    if mixed_normacs:
        total, found_stn, found_normacs, active, partial, conflicts = _parse_mixed_summary(summary)
        found_tnpa = ""
    elif normacs_only:
        total, found_normacs, active = _parse_normacs_summary(summary)
        found_stn = found_tnpa = ""
    else:
        total, found_stn, found_tnpa, active = _parse_summary(summary)
    file_name = next((x.split(":", 1)[1].strip() for x in meta_lines if x.startswith("Файл:")), "")
    sheet = _sheet_from_meta(meta_lines)
    check_date = next((x.split(":", 1)[1].strip() for x in meta_lines if x.startswith("Дата проверки актуальности:")), "")

    header_table = Table(
        [[
            Paragraph(_esc(payload.get("title") or "Таблица нормативов"), title_style),
            Paragraph("Экспорт по результатам проверки нормативных документов", subtitle_style),
        ]],
        colWidths=[avail_w * 0.67, avail_w * 0.33],
    )
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), c_surface),
        ("BOX", (0, 0), (-1, -1), 0.5, c_border),
        ("LEFTPADDING", (0, 0), (-1, -1), pad_h_card + 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), pad_h_card + 2),
        ("TOPPADDING", (0, 0), (-1, -1), pad_v_card + 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), pad_v_card + 2),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 5))

    info_parts = [16.0, 78.0, 16.0, 18.0, 20.0, 32.0]
    info_scale = avail_w / (sum(info_parts) * mm)
    info_col_widths = [p * mm * info_scale for p in info_parts]
    info_table = Table(
        [[
            Paragraph("<b>Файл</b>", info_label_style),
            Paragraph(_esc(file_name or "—"), info_file_style),
            Paragraph("<b>Листов</b>", info_label_style),
            Paragraph(f"<b>{_esc(sheet or '—')}</b>", info_value_style),
            Paragraph("<b>Статус</b>" if normacs_only else "<b>Проверка</b>", info_label_style),
            Paragraph(f"<b>{_esc('Текущий' if normacs_only else check_date or '—')}</b>", info_value_style),
        ]],
        colWidths=info_col_widths,
    )
    info_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), c_surface),
        ("BOX", (0, 0), (-1, -1), 0.5, c_border),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), pad_h_card),
        ("RIGHTPADDING", (0, 0), (-1, -1), pad_h_card),
        ("TOPPADDING", (0, 0), (-1, -1), pad_v_card),
        ("BOTTOMPADDING", (0, 0), (-1, -1), pad_v_card),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 4))

    show_tnpa_stats = found_tnpa != "" or any(
        "тнпа" in str(h or "").casefold() for h in (payload.get("headers") or [])
    )
    if show_tnpa_stats or mixed_normacs:
        stat_w1 = avail_w / 4.0
        stat_w2 = avail_w / 4.0
        stat_w3 = avail_w / 4.0
        stat_w4 = avail_w - stat_w1 - stat_w2 - stat_w3
        stats_table = Table(
            [
                [
                    Paragraph(_esc("Найдено"), card_label_style),
                    Paragraph(_esc(f"В {rb_label}"), card_label_style),
                    Paragraph(_esc("В NormaCS" if mixed_normacs else "В ТНПА"), card_label_style),
                    Paragraph(_esc("Действует в ≥1" if mixed_normacs else "Актуально"), card_label_style),
                ],
                [
                    Paragraph(f"<b>{_esc(total if total != '' else '—')}</b>", card_value_style),
                    Paragraph(f"<b>{_esc(found_stn if found_stn != '' else '—')}</b>", card_value_style),
                    Paragraph(f"<b>{_esc((found_normacs if mixed_normacs else found_tnpa) or '—')}</b>", card_value_style),
                    Paragraph(f"<b>{_esc(active if active != '' else '—')}</b>", card_value_style),
                ],
            ],
            colWidths=[stat_w1, stat_w2, stat_w3, stat_w4],
        )
        stats_style = [
            ("BACKGROUND", (0, 0), (-1, -1), c_surface),
            ("BOX", (0, 0), (-1, -1), 0.5, c_border),
            ("LINEAFTER", (0, 0), (0, -1), 0.5, c_border),
            ("LINEAFTER", (1, 0), (1, -1), 0.5, c_border),
            ("LINEAFTER", (2, 0), (2, -1), 0.5, c_border),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), pad_h_card),
            ("RIGHTPADDING", (0, 0), (-1, -1), pad_h_card),
            ("TOPPADDING", (0, 0), (-1, 0), pad_v_card),
            ("BOTTOMPADDING", (0, 1), (-1, 1), pad_v_card + 2),
        ]
    else:
        stat_w1 = avail_w / 3.0
        stat_w2 = avail_w / 3.0
        stat_w3 = avail_w - stat_w1 - stat_w2
        stats_table = Table(
            [
                [
                    Paragraph(_esc("Найдено"), card_label_style),
                    Paragraph(_esc("В NormaCS" if normacs_only else f"В {rb_label}"), card_label_style),
                    Paragraph(_esc("Действует" if normacs_only else "Актуально"), card_label_style),
                ],
                [
                    Paragraph(f"<b>{_esc(total if total != '' else '—')}</b>", card_value_style),
                    Paragraph(f"<b>{_esc((found_normacs if normacs_only else found_stn) or '—')}</b>", card_value_style),
                    Paragraph(f"<b>{_esc(active if active != '' else '—')}</b>", card_value_style),
                ],
            ],
            colWidths=[stat_w1, stat_w2, stat_w3],
        )
        stats_style = [
            ("BACKGROUND", (0, 0), (-1, -1), c_surface),
            ("BOX", (0, 0), (-1, -1), 0.5, c_border),
            ("LINEAFTER", (0, 0), (0, -1), 0.5, c_border),
            ("LINEAFTER", (1, 0), (1, -1), 0.5, c_border),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), pad_h_card),
            ("RIGHTPADDING", (0, 0), (-1, -1), pad_h_card),
            ("TOPPADDING", (0, 0), (-1, 0), pad_v_card),
            ("BOTTOMPADDING", (0, 1), (-1, 1), pad_v_card + 2),
        ]
    stats_table.setStyle(TableStyle(stats_style))
    story.append(stats_table)
    story.append(Spacer(1, 5))
    if normacs_only:
        story.append(Paragraph(
            "NormaCS: текущие статусы базы РФ; историческая дата проверки не применяется.", subtitle_style
        ))
        story.append(Spacer(1, 5))
    elif mixed_normacs:
        story.append(Paragraph(
            f"NormaCS: текущие статусы базы РФ; результаты {rb_label} относятся к РБ. "
            "Зелёный итог означает действие хотя бы по одному реестру и не подтверждает "
            "применимость в другой стране. Расхождения показаны в строках.", subtitle_style
        ))
        story.append(Spacer(1, 5))
        if partial or conflicts:
            story.append(Paragraph(
                f"Частично действует: {_esc(partial or '0')}; расхождения: {_esc(conflicts or '0')}.",
                subtitle_style))
            story.append(Spacer(1, 5))
    elif has_normacs_header:
        story.append(Paragraph(
            ("NormaCS: текущие статусы базы РФ; результаты Стройдока относятся к РБ. "
             "В старом отчёте цвет и счётчик актуальных документов относятся к Стройдоку."
             if rb_label == "Стройдок" else
             "NormaCS: текущие статусы базы РФ; результаты Стройпрофи относятся к РБ. "
             "Цвет и счётчик актуальных документов относятся к Стройпрофи."), subtitle_style
        ))
        story.append(Spacer(1, 5))

    # Канонические заголовки — не зависят от DOM/браузера клиента
    has_tnpa_header = any("тнпа" in h.casefold() for h in raw_headers)
    if has_normacs_header:
        headers = raw_headers
    elif has_tnpa_header or len(raw_headers) >= 5:
        headers = [
            "Обозначение",
            rb_label,
            "ТНПА",
            "Введен",
            "Отменен",
        ]
    elif len(raw_headers) == 4:
        headers = [
            "Обозначение",
            rb_label,
            "Введен",
            "Отменен",
        ]
    else:
        headers = raw_headers
    rows = payload.get("rows") or []
    table_data: list[list[Any]] = [
        [Paragraph(_format_header_html(h), header_style) for h in headers]
    ]

    row_fills: list[tuple[int, str]] = []
    for idx, row in enumerate(rows, start=1):
        fill = str(row.get("fill") or "").strip()
        cells = []
        for col_idx, cell in enumerate(row.get("cells") or []):
            text = _esc(cell.get("text") or "—")
            if cell.get("bold"):
                text = f"<b>{text}</b>"
            href = str(cell.get("href") or "").strip()
            if href:
                text = f'<link href="{_esc(href)}" color="#1d4ed8">{text}</link>'
            elif not has_normacs_header and col_idx >= (3 if has_tnpa_header or len(headers) >= 5 else 2) and text not in ("—", "&mdash;"):
                text = f"<nobr>{text}</nobr>"
            cells.append(Paragraph(text, cell_style))
        # Выравниваем число колонок под заголовок
        while len(cells) < len(headers):
            cells.append(Paragraph("—", cell_style))
        table_data.append(cells[: len(headers)])
        if fill:
            row_fills.append((idx, fill))

    # Эталонные ширины (как в рабочем PDF 279 mm): без redistrib по клиенту
    if normacs_only and len(headers) == 3:
        widths_mm = [98.0, 34.0, 64.0]
    elif has_normacs_header and len(headers) == 6:
        widths_mm = [55.0, 26.0, 24.0, 24.0, 25.0, 42.0]
    elif len(headers) == 5:
        widths_mm = [70.0, 24.0, 22.0, 40.0, 40.0]
    elif len(headers) == 4:
        widths_mm = [80.0, 30.0, 45.0, 45.0]
    else:
        widths_mm = [avail_w / mm / max(len(headers), 1)] * max(len(headers), 1)
    if len(headers) in (4, 5) or has_normacs_header:
        scale = (avail_w / mm) / sum(widths_mm)
        widths_mm = [round(w * scale, 4) for w in widths_mm]
        # компенсация округления на последнюю колонку
        widths_mm[-1] = round((avail_w / mm) - sum(widths_mm[:-1]), 4)
    col_widths = [w * mm for w in widths_mm]
    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    style_cmds: list[tuple[Any, ...]] = [
        ("BACKGROUND", (0, 0), (-1, 0), c_accent),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, c_accent),
        ("GRID", (0, 1), (-1, -1), 0.25, c_border),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), pad_h),
        ("RIGHTPADDING", (0, 0), (-1, -1), pad_h),
        ("TOPPADDING", (0, 0), (-1, -1), pad_v),
        ("BOTTOMPADDING", (0, 0), (-1, -1), pad_v),
    ]
    if len(headers) >= 5:
        style_cmds.extend(
            [
                ("ALIGN", (2, 1), (2, -1), "CENTER"),
                ("ALIGN", (3, 1), (4, -1), "CENTER"),
            ]
        )
    elif len(headers) == 4:
        style_cmds.extend(
            [
                ("ALIGN", (2, 1), (3, -1), "CENTER"),
            ]
        )
    fill_map = {
        "active": c_row_active,
        "canceled": c_row_canceled,
        "replaced": c_row_replaced,
        "warn": c_row_replaced,
    }
    styled: set[int] = set()
    for row_idx, fill in row_fills:
        color = fill_map.get(fill)
        if color:
            style_cmds.append(("BACKGROUND", (0, row_idx), (-1, row_idx), color))
            styled.add(row_idx)
    for row_idx in range(1, len(table_data)):
        if row_idx not in styled:
            style_cmds.append(("BACKGROUND", (0, row_idx), (-1, row_idx), c_surface))
    table.setStyle(TableStyle(style_cmds))
    story.append(table)

    def _stable_canvas(*args, **kwargs):
        kwargs["invariant"] = 1
        return pdf_canvas.Canvas(*args, **kwargs)

    doc.build(story, onFirstPage=_fix_metadata, onLaterPages=_fix_metadata,
              canvasmaker=_stable_canvas)
    return buf.getvalue()
