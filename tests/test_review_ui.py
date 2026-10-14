import json
from html import unescape
import re

import fitz
import pytest

from belener.normative_extract import _collect_highlight_rects, normative_refs_to_markdown


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_preview_click_coordinates_match_rotated_page(rotation):
    doc = fitz.open()
    page = doc.new_page(width=600, height=300)
    page.set_rotation(rotation)
    words = [(72, 100, 110, 115, "ГОСТ", 0, 0, 0),
             (115, 100, 180, 115, "10704-91", 0, 0, 1)]
    marks = []
    hits, count, rects = _collect_highlight_rects(
        page, [{"ref": "ГОСТ 10704-91"}], [words], mark_items=marks
    )
    assert hits == 1 and count == 2 and len(marks) == 2
    for rect, mark in zip(rects, marks):
        rotated = rect * page.rotation_matrix
        assert mark["ref"] == "ГОСТ 10704-91"
        assert mark["x"] == pytest.approx(100 * rotated.x0 / page.rect.width, abs=1e-5)
        assert mark["y"] == pytest.approx(100 * rotated.y0 / page.rect.height, abs=1e-5)
        assert 0 <= mark["x"] < 100 and 0 <= mark["y"] < 100
        assert mark["x"] + mark["w"] <= 100.00001
    doc.close()


def test_review_metadata_preserves_sources_and_escaped_preview_hits():
    ref = 'ГОСТ 10704-91'
    marks = [{"ref": ref, "x": 12, "y": 33, "w": 8, "h": 5}]
    html = normative_refs_to_markdown(
        [{"kind": "ГОСТ", "ref": ref}], filename='drawing<test>.pdf', include_tnpa=False,
        stn_checks=[{"ref": ref, "kind": "ГОСТ", "found": "0", "status": "ошибка проверки", "error": "timeout"}],
        page_normative_refs=[[{"ref": ref}]],
        preview_pages=[{"page": 1, "url": "/api/preview/marked.jpg", "plain_url": "/api/preview/plain.jpg", "highlights": marks}],
    )
    assert 'data-stn-status="ошибка проверки"' in html
    assert 'data-stn-found="0"' in html
    assert 'data-stn-error="timeout"' in html
    assert 'src="/api/preview/plain.jpg"' in html
    assert 'data-highlight-url="/api/preview/marked.jpg"' in html
    assert 'data-interactive-highlights="1"' in html
    encoded = re.search(r'data-review-marks="([^"]+)"', html).group(1)
    assert json.loads(unescape(encoded)) == marks
    assert 'drawing&lt;test&gt;.pdf' in html
    assert '<th>ТНПА</th>' not in html


def test_legacy_preview_remains_viewable_without_interactive_metadata():
    html = normative_refs_to_markdown(
        [{"kind": "ГОСТ", "ref": "ГОСТ 10704-91"}], include_tnpa=False,
        preview_pages=[{"page": 1, "url": "/api/preview/old.jpg"}],
    )
    assert 'src="/api/preview/old.jpg"' in html
    assert 'data-interactive-highlights="0"' in html
    assert 'data-review-marks="[]"' in html
