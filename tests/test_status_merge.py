import unittest

from belener.status_merge import combined_verdict, normacs_state, stn_state


class StatusMergeTests(unittest.TestCase):
    def test_combined_status_matrix(self):
        cases = [
            ("active", "active", "active", False),
            ("active", "missing", "active", False),
            ("active", "inactive", "active", True),
            ("inactive", "active", "active", True),
            ("missing", "active", "active", False),
            ("inactive", "inactive", "inactive", False),
            ("inactive", "missing", "inactive", False),
            ("missing", "inactive", "inactive", False),
            ("missing", "missing", "missing", False),
            ("inactive", "partial", "partial", True),
            ("missing", "partial", "partial", False),
            ("upcoming", "missing", "upcoming", False),
            ("inactive", "error", "unknown", False),
            ("error", "missing", "unknown", False),
        ]
        for stn, nc, state, conflict in cases:
            with self.subTest(stn=stn, normacs=nc):
                verdict = combined_verdict(stn, nc)
                self.assertEqual(verdict.state, state)
                self.assertEqual(verdict.conflict, conflict)
                if conflict:
                    self.assertTrue(verdict.note)

    def test_not_found_and_errors_never_become_cancellation(self):
        self.assertEqual(stn_state(enabled=True, found=False, status="нет в ИПС"), "missing")
        self.assertEqual(stn_state(enabled=True, found=False, status="ошибка проверки"), "error")
        self.assertEqual(stn_state(enabled=True, found=True, status="отменён"), "inactive")
        self.assertEqual(normacs_state(enabled=True, found=False, status="Не найден в NormaCS"), "missing")
        self.assertEqual(normacs_state(enabled=True, found=False, status="Ошибка проверки NormaCS"), "error")
        self.assertEqual(normacs_state(enabled=True, found=True, status_id="2"), "partial")
        self.assertEqual(combined_verdict("missing", "missing").tone, "neutral")

    def test_html_row_uses_combined_status(self):
        from belener.normative_extract import normative_refs_to_markdown

        cases = [
            ("актуален", "1", "", False, "row-active"),
            ("отменён", "1", "1", True, "row-active"),
            ("отменён", "1", "", False, "row-canceled"),
            ("нет в ИПС", "0", "1", True, "row-active"),
            ("нет в ИПС", "0", "", False, ""),
            ("отменён", "1", "2", True, "row-warn"),
        ]
        ref = "СП 1.01.01-2020"
        for stn_status, stn_found, nc_id, nc_found, class_name in cases:
            with self.subTest(stn=stn_status, normacs=nc_id):
                nc_status = {"1": "Действует", "2": "Частично действует"}.get(
                    nc_id, "Не найден в NormaCS")
                html = normative_refs_to_markdown(
                    [{"kind": "СП", "ref": ref}], include_stn=True, include_tnpa=False,
                    include_normacs=True,
                    stn_checks=[{"kind": "СП", "ref": ref, "found": stn_found,
                                 "status": stn_status, "doc_id": "123" if stn_found == "1" else ""}],
                    normacs_checks=[{"kind": "СП", "ref": ref, "found": nc_found,
                                     "status_id": nc_id, "status": nc_status}],
                )
                row = html.split("<tbody>", 1)[1].split("</tr>", 1)[0]
                if class_name:
                    self.assertIn(f'class="{class_name}"', row)
                else:
                    self.assertNotIn('class="row-', row)
                self.assertIn('data-overall-label=', row)
                if stn_status == "отменён" and nc_id == "1":
                    self.assertIn('data-overall-conflict="1"', row)
                    self.assertIn("⚠ Реестры расходятся", row)
                if nc_id == "2":
                    self.assertIn("Частично действует", row)


if __name__ == "__main__":
    unittest.main()
