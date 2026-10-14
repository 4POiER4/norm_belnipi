import re
import unittest

import fitz

from belener.normative_pdf import build_normative_pdf_bytes


class MixedRegistryPdfTests(unittest.TestCase):
    def test_mixed_summary_and_statuses_survive_export(self):
        payload = {
            "title": "Таблица нормативов",
            "combined": True,
            "headers": ["Обозначение", "Стройдок", "Введен (Стройдок)",
                        "Отменен (Стройдок)", "NormaCS", "Статус NormaCS (РФ)"],
            "summary": ("Всего в документе: 2; найдено в Стройдок: 1; "
                        "найдено в NormaCS: 2;\nдействует хотя бы в одном: 1; "
                        "частично: 1; расхождения: 1"),
            "rows": [
                {"fill": "active", "cells": [
                    {"text": "СП 1.01.01-2020 Действует · NormaCS · РФ ⚠ Реестры расходятся"},
                    {"text": "Стройдок Не действует", "href": "https://normy.stn.by/ips.php?123"},
                    {"text": "01.01.2020"}, {"text": "01.01.2024"},
                    {"text": "NormaCS", "href": "http://example.test/document/1/card"},
                    {"text": "Действует Введен: 2020-01-01"},
                ]},
                {"fill": "warn", "cells": [
                    {"text": "СП 2.02.02-2020 Частично действует · NormaCS · РФ"},
                    {"text": "— Не найден"}, {"text": "—"}, {"text": "—"},
                    {"text": "NormaCS", "href": "http://example.test/document/2/card"},
                    {"text": "Частично действует Введен: 2020-02-02"},
                ]},
            ],
        }
        with fitz.open(stream=build_normative_pdf_bytes(payload), filetype="pdf") as doc:
            extracted = " ".join(page.get_text() for page in doc)
        flat = re.sub(r"\s+", " ", extracted)
        self.assertIn("В NormaCS", flat)
        self.assertIn("Действует", flat)
        self.assertIn("расхождения: 1", flat)
        self.assertIn("Частично действует", flat)
        self.assertIn("Введен: 2020-02-02", flat)
        self.assertNotIn("действуетВведен", flat)

    def test_old_mixed_report_keeps_legacy_counter_meaning(self):
        payload = {
            "title": "Таблица нормативов",
            "headers": ["Обозначение", "Стройдок", "Введен (Стройдок)",
                        "Отменен (Стройдок)", "NormaCS", "Статус NormaCS (РФ)"],
            "summary": "Всего в документе: 1; найдено в Стройдок: 0; актуально: 0",
            "rows": [{"fill": "canceled", "cells": [
                {"text": "СП 1.01.01-2020"}, {"text": "—"}, {"text": "—"},
                {"text": "—"}, {"text": "NormaCS", "href": "http://example.test/document/1/card"},
                {"text": "Действует Введен: 2020-01-01"},
            ]}],
        }
        with fitz.open(stream=build_normative_pdf_bytes(payload), filetype="pdf") as doc:
            text = " ".join(page.get_text() for page in doc)
        self.assertIn("В старом отчёте", text)


if __name__ == "__main__":
    unittest.main()
