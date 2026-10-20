import unittest

from belener.normative_crops import _prefer_complete_layer_refs
from belener.normative_extract import normative_refs_to_markdown


class TextLayerRepairTests(unittest.TestCase):
    def test_unique_complete_designation_replaces_ocr_fragments(self):
        layer = [{"kind": "ТКП", "ref": "ТКП 45-1.02-104-2008"}]
        ocr = [{"kind": "ТКП", "ref": "ТКП 45-1.02-104"},
               {"kind": "ТКП", "ref": "ТКП 45-1.02-104-2"}]
        self.assertEqual([x["ref"] for x in _prefer_complete_layer_refs(ocr, layer)],
                         ["ТКП 45-1.02-104-2008"])

    def test_ambiguous_versions_are_not_guessed(self):
        layer = [{"kind": "СН", "ref": "СН 1.02.02-2019"},
                 {"kind": "СН", "ref": "СН 1.02.02-2023"}]
        ocr = [{"kind": "СН", "ref": "СН 1.02.02"}]
        self.assertEqual(_prefer_complete_layer_refs(ocr, layer), ocr)

    def test_report_explains_merged_fragments(self):
        html = normative_refs_to_markdown(
            [{"kind": "СН", "ref": "СН 1.02.02-2023"}],
            rb_provider="stroiprofi", include_stn=False, include_tnpa=False,
            merged_fragment_count=2,
        )
        self.assertIn("Уникальных обозначений:</strong> 1", html)
        self.assertIn("Объединено неполных повторов:</strong> 2", html)


if __name__ == "__main__":
    unittest.main()
