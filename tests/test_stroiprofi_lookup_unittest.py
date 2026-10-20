import unittest
from datetime import date

from belener.stroiprofi_lookup import StroiprofiClient, _key, _status, check_normative_refs
from belener.status_merge import combined_verdict, stn_state
from belener.normative_extract import normative_refs_to_markdown


class FakeClient(StroiprofiClient):
    def __init__(self, search_items, card, *, catalog_items=None):
        super().__init__(base_url="https://стройпрофи.бел", login="", password="")
        self.search_items = search_items
        self.catalog_items = search_items if catalog_items is None else catalog_items
        self.card_data = card
        self.requested_day = None

    def search(self, query):
        return self.search_items

    def search_by_designation(self, query):
        return self.catalog_items

    def card(self, doc_id, *, as_of):
        self.requested_day = as_of
        return (self.card_data if "designation" in self.card_data
                else self.card_data[str(doc_id)])


class StroiprofiLookupTests(unittest.TestCase):
    def test_exact_designation_not_amendment_or_fulltext_hit(self):
        client = FakeClient([
            {"id": 3, "designation": "СП 4.03.02-2026"},
            {"id": 2, "designation": "Изменение № 1 СН 1.02.02-2023"},
            {"id": 1, "designation": "СН 1.02.02-2023"},
        ], {"designation": "СН 1.02.02-2023", "name": "Проектная документация",
            "status": "Действующий", "activateDate": "2023-10-02"})
        result = client.lookup("СН", "СН 1.02.02-2023", as_of=date(2026, 10, 19))
        self.assertTrue(result.found)
        self.assertEqual(result.status, "актуален")
        self.assertEqual(result.doc_id, "1")
        self.assertEqual(result.intro_date, "02.10.2023")
        self.assertEqual(client.requested_day, date(2026, 10, 19))
        self.assertTrue(result.url.endswith("/tnpa/document/1"))

    def test_canceled_and_upcoming_are_not_active(self):
        self.assertEqual(_status({"status": "Отменён"}), "отменён")
        self.assertEqual(_status({"status": "Не вступил в силу"}), "не введён")
        self.assertEqual(stn_state(enabled=True, found=True, status="частично действует"), "partial")
        verdict = combined_verdict("inactive", "active", rb_label="Стройпрофи · РБ")
        self.assertEqual(verdict.state, "active")
        self.assertTrue(verdict.conflict)
        self.assertIn("NormaCS", verdict.label)

    def test_latest_effective_edition_overrides_old_pdf_year(self):
        rows = [
            {"id": 1, "designation": "СН 3.02.04-2019", "activateDate": "2020-09-08"},
            {"id": 2, "designation": "СН 3.02.04-2026", "activateDate": "2026-09-06"},
            {"id": 3, "designation": "Изменение №1 СН 3.02.04-2026", "activateDate": "2026-10-01"},
            {"id": 4, "designation": "СП 3.02.04-2029", "activateDate": "2029-01-01"},
        ]
        cards = {
            "1": {"designation": "СН 3.02.04-2019", "status": "Действующий"},
            "2": {"designation": "СН 3.02.04-2026", "status": "Действующий"},
        }
        client = FakeClient(rows, cards)
        current = client.lookup("СН", "СН 3.02.04-2019", as_of=date(2026, 10, 19))
        self.assertEqual(current.stn_code, "СН 3.02.04-2026")
        self.assertEqual(current.status, "актуален")
        historical = client.lookup("СН", "СН 3.02.04-2019", as_of=date(2025, 1, 1))
        self.assertEqual(historical.stn_code, "СН 3.02.04-2019")

    def test_canceled_latest_edition_is_red_even_when_old_was_active(self):
        rows = [
            {"id": 1, "designation": "СП 4.04.01-2022", "activateDate": "2023-01-01"},
            {"id": 2, "designation": "СП 4.04.01-2026", "activateDate": "2026-01-01"},
        ]
        cards = {
            "1": {"designation": "СП 4.04.01-2022", "status": "Действующий"},
            "2": {"designation": "СП 4.04.01-2026", "status": "Отменён"},
        }
        result = FakeClient(rows, cards).lookup("СП", "СП 4.04.01-2022", as_of=date(2026, 10, 19))
        self.assertEqual(result.stn_code, "СП 4.04.01-2026")
        self.assertEqual(stn_state(enabled=True, found=result.found, status=result.status), "inactive")

    def test_ambiguous_designation_does_not_claim_status(self):
        client = FakeClient([{"id": 1, "designation": "ГОСТ 8267-93"},
                             {"id": 2, "designation": "ГОСТ 8267-93"}], {})
        result = client.lookup("ГОСТ", "ГОСТ 8267-93", as_of=date(2026, 10, 19))
        self.assertFalse(result.found)
        self.assertIn("несколько", result.status)

    def test_catalog_resolves_check_error_or_duplicate(self):
        from belener.stroiprofi_lookup import StroiprofiError

        class CheckErrorClient(FakeClient):
            def search(self, query):
                raise StroiprofiError("Стройпрофи вернул HTTP 500")

        card = {"designation": "СТБ 2522-2018 (EN 12237:2003)",
                "status": "Действующий"}
        catalog = [{"id": 7, "designation": card["designation"]}]
        failed = CheckErrorClient([], card, catalog_items=catalog)
        self.assertTrue(failed.lookup("СТБ", "СТБ 2522-2018", as_of=date(2026, 10, 19)).found)

        duplicate = FakeClient([{"id": 8, "designation": "ГОСТ 30331.3-95"},
                                {"id": 7, "designation": "ГОСТ 30331.3-95 (МЭК 364-4-41-92)"}],
                               {"designation": "ГОСТ 30331.3-95 (МЭК 364-4-41-92)",
                                "status": "Действующий"},
                               catalog_items=[{"id": 7, "designation": "ГОСТ 30331.3-95 (МЭК 364-4-41-92)"}])
        self.assertTrue(duplicate.lookup("ГОСТ", "ГОСТ 30331.3-95", as_of=date(2026, 10, 19)).found)

    def test_duplicate_kind_and_parenthetical_index(self):
        self.assertEqual(_key("СН СН 1.02.02-2023"), _key("СН 1.02.02-2023"))
        self.assertEqual(_key("ТКП 45-1.02-104-2008 (02250)"), _key("ТКП 45-1.02-104-2008"))

    def test_batch_preserves_refs_and_skips_duplicates(self):
        client = FakeClient([{"id": 1, "designation": "ГОСТ 8267-93"}],
                            {"designation": "ГОСТ 8267-93", "status": "Действующий"})
        refs = [{"kind": "ГОСТ", "ref": "ГОСТ 8267-93"},
                {"kind": "ГОСТ", "ref": "ГОСТ 8267-93"}]
        unchanged, checks = check_normative_refs(refs, today=date(2026, 10, 19), client=client)
        self.assertEqual(unchanged, refs)
        self.assertEqual(len(checks), 1)

    def test_report_uses_new_source_and_keeps_old_site_out(self):
        refs = [{"kind": "СН", "ref": "СН 1.02.02-2023"}]
        check = {"kind": "СН", "ref": refs[0]["ref"], "found": "1",
                 "status": "актуален", "doc_id": "8298", "intro_date": "02.10.2023",
                 "stn_name": "Проектная документация", "stn_code": "СН 1.02.02-2023",
                 "url": "https://стройпрофи.бел/tnpa/document/8298"}
        html = normative_refs_to_markdown(refs, stn_checks=[check], include_stn=True,
                                           rb_provider="stroiprofi", include_tnpa=False,
                                           include_normacs=True)
        self.assertIn("Стройпрофи", html)
        self.assertIn("/tnpa/document/8298", html)
        self.assertIn('class="row-active', html)
        self.assertIn('Последняя редакция: СН 1.02.02-2023', html)
        self.assertNotIn("Стройдок", html)
        self.assertNotIn("normy.stn.by", html)

    def test_incomplete_number_is_not_presented_as_inactive(self):
        ref = {"kind": "СН", "ref": "СН 1.02.02"}
        check = {"kind": "СН", "ref": ref["ref"], "found": "0",
                 "status": "неполное обозначение — уточните номер и год"}
        html = normative_refs_to_markdown([ref], stn_checks=[check], include_stn=True,
                                           rb_provider="stroiprofi", include_tnpa=False,
                                           include_normacs=True)
        self.assertIn("Уточните номер и год", html)
        self.assertNotIn('class="row-canceled', html)


if __name__ == "__main__":
    unittest.main()
