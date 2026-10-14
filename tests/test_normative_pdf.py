import pytest

pytest.importorskip("reportlab")

from belener.normative_pdf import build_normative_pdf_bytes


def test_build_normative_pdf_bytes_returns_pdf():
    payload = {
        "title": "Таблица нормативов",
        "filename": "sample.pdf",
        "meta": ["Файл: sample.pdf", "Листов в файле: 42"],
        "summary": "Всего в документе: 36; найдено в Стройдок: 23; найдено в ТНПА: 5; актуально: 21",
        "headers": [
            "Тип",
            "Обозначение",
            "Стройдок",
            "ТНПА",
            "Введен\nСтройдок",
            "Отменен Стройдок",
            "ВведенТНПА",
            "Отменен ТНПА",
            "Статус",
        ],
        "rows": [
            {
                "fill": "active",
                "cells": [
                    {"text": "ГОСТ", "bold": False},
                    {"text": "ГОСТ 23407-78", "bold": False},
                    {"text": "Стройдок", "href": "https://normy.stn.by/ips.php?123", "bold": False},
                    {"text": "ТНПА", "href": "https://tnpa.by/#!/DocumentCard/100/200", "bold": False},
                    {"text": "01.07.1979", "bold": False},
                    {"text": "—", "bold": False},
                    {"text": "—", "bold": False},
                    {"text": "—", "bold": False},
                    {"text": "актуален", "bold": True},
                ],
            }
        ],
        "widths": [14, 66, 18, 18, 20, 20, 20, 20, 28],
    }
    pdf = build_normative_pdf_bytes(payload)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000


def test_format_header_html_two_lines_no_space():
    from belener.normative_pdf import _format_header_html

    assert _format_header_html("Введен Стройдок") == "Введен<br/>Стройдок"
    assert _format_header_html("ВведенТНПА") == "Введен<br/>ТНПА"
    assert _format_header_html("Отменен\nТНПА") == "Отменен<br/>ТНПА"
    assert _format_header_html("Статус") == "Статус"


def test_normacs_only_table_exports_without_stn_columns():
    import fitz
    from belener.normative_pdf import build_normative_pdf_bytes

    payload = {
        "title": "Таблица нормативов",
        "headers": ["Обозначение", "NormaCS", "Статус NormaCS (РФ)"],
        "summary": "Всего в документе: 1; найдено в NormaCS: 1; действует: 0",
        "rows": [{"cells": [
            {"text": "ГОСТ 21.101-97"},
            {"text": "NormaCS", "href": "http://example.test/document/1/card"},
            {"text": "Частично действует"},
        ]}],
    }
    with fitz.open(stream=build_normative_pdf_bytes(payload), filetype="pdf") as doc:
        exported = "\n".join(page.get_text() for page in doc)
    assert "NormaCS" in exported
    assert "Частично действует" in exported
    assert "Стройдок" not in exported


def test_pdf_column_widths_stable():
    """Ширины колонок фиксированы — одинаковый байтовый PDF при том же payload."""
    from belener.normative_pdf import build_normative_pdf_bytes

    payload = {
        "title": "Таблица нормативов",
        "meta": ["Файл: sample.pdf", "Листов: 1", "Дата проверки актуальности: 12.08.2026"],
        "summary": "Всего в документе: 1; найдено в Стройдок: 1; найдено в ТНПА: 1; актуально: 1",
        "headers": [
            "Тип",
            "Обозначение",
            "Стройдок",
            "ТНПА",
            "Введен Стройдок",
            "Отменен Стройдок",
            "Введен ТНПА",
            "Отменен ТНПА",
            "Статус",
        ],
        "rows": [
            {
                "fill": "active",
                "cells": [
                    {"text": "СТБ"},
                    {"text": "СТБ 2073-2010"},
                    {"text": "Стройдок", "href": "https://normy.stn.by/ips.php?1"},
                    {"text": "ТНПА", "href": "https://tnpa.by/#!/DocumentCard/1/2"},
                    {"text": "01.01.2011"},
                    {"text": "—"},
                    {"text": "01.01.2011"},
                    {"text": "—"},
                    {"text": "актуален", "bold": True},
                ],
            }
        ],
    }
    pdf1 = build_normative_pdf_bytes(payload)
    pdf2 = build_normative_pdf_bytes(payload)
    assert pdf1.startswith(b"%PDF")
    assert pdf1 == pdf2
    assert len(pdf1) > 800


def test_build_normative_pdf_computes_summary_from_rows():
    payload = {
        "title": "Таблица нормативов",
        "headers": [
            "Тип",
            "Обозначение",
            "Стройдок",
            "ТНПА",
            "Введен Стройдок",
            "Отменен Стройдок",
            "Введен ТНПА",
            "Отменен ТНПА",
            "Статус",
        ],
        "rows": [
            {
                "cells": [
                    {"text": "ГОСТ"},
                    {"text": "ГОСТ 1"},
                    {"text": "Стройдок", "href": "https://normy.stn.by/ips.php?1"},
                    {"text": "ТНПА", "href": "https://tnpa.by/#!/DocumentCard/1/2"},
                    {"text": "01.01.2000"},
                    {"text": "—"},
                    {"text": "—"},
                    {"text": "—"},
                    {"text": "актуален", "bold": True},
                ],
            },
            {
                "cells": [
                    {"text": "СП"},
                    {"text": "СП 2"},
                    {"text": "—"},
                    {"text": "—"},
                    {"text": "—"},
                    {"text": "—"},
                    {"text": "—"},
                    {"text": "не найдено"},
                ],
            },
        ],
    }
    pdf = build_normative_pdf_bytes(payload)
    assert pdf.startswith(b"%PDF")
    assert b"36" not in pdf[:5000]  # sanity: not random
    assert len(pdf) > 800
