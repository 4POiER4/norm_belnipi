"""Многостраничные PDF: бюджет, сетка тайлов, отчёт."""

from belener.config import (
    gost_check_budget_human,
    gost_check_total_budget_sec,
    normative_ocr_budget_sec,
    pipeline_stn_deadline,
    pipeline_tnpa_deadline,
    tnpa_batch_budget_sec,
    tile_grid_for_page_count,
)
from belener.normative_extract import normative_refs_to_markdown
from belener.normative_refs import extract_normative_refs, merge_normative_refs_from_sources


def test_budget_scales_with_page_count():
    one = gost_check_total_budget_sec(1)
    twelve = gost_check_total_budget_sec(12)
    hundred = gost_check_total_budget_sec(100)
    two_hundred = gost_check_total_budget_sec(200)
    assert one == 600.0
    assert twelve > one
    # 2..12: полный rate 28 с/лист
    assert twelve == one + 11 * 28.0
    assert hundred > twelve
    # 100 листов: полный OCR без упирания в минутный «хвост»
    assert hundred >= 3000.0
    assert hundred <= 14400.0
    # 200+ листов: заметно больше старого потолка 1 ч
    assert two_hundred > hundred
    assert two_hundred > 3600.0
    assert two_hundred >= 4800.0  # ≥ ~80 мин — хватает на full-page OCR
    assert two_hundred <= 14400.0
    assert normative_ocr_budget_sec(12) >= normative_ocr_budget_sec(1)
    assert normative_ocr_budget_sec(200) >= 4000.0
    assert "мин" in gost_check_budget_human(12)
    assert "ч" in gost_check_budget_human(200)


def test_tnpa_budget_scales_with_refs_and_pages(monkeypatch):
    monkeypatch.setenv("PDF_TNPA_TIMEOUT", "70")
    monkeypatch.setenv("PDF_TNPA_PARALLEL", "1")
    monkeypatch.setenv("PDF_TNPA_BUDGET_MAX", "1800")
    small = tnpa_batch_budget_sec(page_count=1, refs_count=5)
    large = tnpa_batch_budget_sec(page_count=100, refs_count=40)
    assert large > small
    assert small >= 180.0
    # 11 refs × ~82 с волны не должны обрезаться OCR-капом ~330 с
    eleven = tnpa_batch_budget_sec(page_count=1, refs_count=11)
    assert eleven >= 11 * 70

    import time

    t0 = time.monotonic()
    dl = pipeline_tnpa_deadline(pipeline_t0=t0, page_count=100, refs_count=40)
    assert dl >= t0 + 180.0


def test_stn_gets_fresh_budget_after_long_ocr():
    """225-листовой OCR не должен оставить Стройдок с истёкшим дедлайном."""
    import time

    now = time.monotonic()
    pipeline_started_long_ago = now - 20_000.0
    deadline = pipeline_stn_deadline(
        pipeline_t0=pipeline_started_long_ago,
        page_count=225,
        refs_count=40,
    )
    assert deadline >= now + 300.0


def test_tile_grid_shrinks_for_many_pages():
    assert tile_grid_for_page_count(1) == (4, 2)
    assert tile_grid_for_page_count(3) == (3, 2)
    assert tile_grid_for_page_count(12) == (2, 2)
    assert tile_grid_for_page_count(20) == (1, 1)
    assert tile_grid_for_page_count(50) == (1, 1)
    assert tile_grid_for_page_count(100) == (1, 1)


def test_long_doc_skips_supplement_zones():
    import fitz
    from belener.tile_ocr import supplements_for_page_scan

    wide = fitz.Rect(0, 0, 1200, 600)
    assert supplements_for_page_scan(wide, 1)
    assert supplements_for_page_scan(wide, 4)
    assert supplements_for_page_scan(wide, 13) == []
    assert supplements_for_page_scan(wide, 42) == []


def test_long_doc_ocr_budget_covers_full_pages():
    # 42 листа полного OCR должны влезать в окно тома, не в 280 с TILE_BUDGET.
    assert normative_ocr_budget_sec(42) >= 900.0
    assert normative_ocr_budget_sec(42) < gost_check_total_budget_sec(42)


def test_long_document_skips_expensive_global_normative_reparse(monkeypatch):
    from belener import normative_crops

    calls: list[list[str]] = []

    def _fake_finalize(sources):
        calls.append(list(sources))
        page_no = len(calls)
        return [{"kind": "ГОСТ", "ref": f"ГОСТ {10000 + page_no}-2020"}]

    monkeypatch.setattr(normative_crops, "_finalize_refs", _fake_finalize)
    texts = [f"page {i}" for i in range(225)]
    refs, page_refs = normative_crops._merge_document_refs(texts, list(texts))

    assert len(calls) == 225
    assert len(page_refs) == 225
    assert len(refs) == 225


def test_short_document_keeps_global_tile_voting(monkeypatch):
    from belener import normative_crops

    calls: list[list[str]] = []

    def _fake_finalize(sources):
        calls.append(list(sources))
        return [{"kind": "ГОСТ", "ref": "ГОСТ 10704-91"}]

    monkeypatch.setattr(normative_crops, "_finalize_refs", _fake_finalize)
    refs, page_refs = normative_crops._merge_document_refs(
        ["page one", "page two"],
        ["tile one", "tile two", "tile three"],
    )

    assert len(calls) == 3  # две страницы + общий проход по тайлам
    assert len(page_refs) == 2
    assert len(refs) == 1


def test_preview_page_indices_include_all_pages():
    from belener.normative_extract import _preview_page_indices

    sparse = [
        [{"kind": "ГОСТ", "ref": "ГОСТ 10704-91"}],
        [],
        [],
        [{"kind": "СП", "ref": "СП 1.03.11-2023"}],
    ]
    assert _preview_page_indices(42, sparse) == list(range(42))
    assert _preview_page_indices(4, sparse) == [0, 1, 2, 3]


def test_multipage_preview_generates_all_pages():
    import fitz

    from belener.normative_extract import generate_pdf_preview_pages_with_highlights

    doc = fitz.open()
    for _ in range(3):
        page = doc.new_page(width=600, height=400)
        page.insert_text((72, 100), "ГОСТ 10704-91")
    tmp = doc.tobytes()
    doc.close()

    import os
    import tempfile

    path = os.path.join(tempfile.gettempdir(), "belener_preview_test.pdf")
    with open(path, "wb") as f:
        f.write(tmp)

    refs = [{"kind": "ГОСТ", "ref": "ГОСТ 10704-91"}]
    page_normative_refs = [
        [{"kind": "ГОСТ", "ref": "ГОСТ 10704-91"}],
        [],
        [],
    ]
    pages = generate_pdf_preview_pages_with_highlights(
        path, refs, page_normative_refs=page_normative_refs
    )
    try:
        os.unlink(path)
    except OSError:
        pass
    assert len(pages) == 3
    assert all(p.get("url") for p in pages)
    md = normative_refs_to_markdown(
        [{"kind": "ГОСТ", "ref": "ГОСТ 481-80"}],
        filename="multi.pdf",
        page_count=12,
        pages_processed=8,
        budget_exhausted=True,
        check_date=None,
        preview_pages=[
            {"page": 1, "url": "/api/preview/a.jpg"},
            {"page": 3, "url": "/api/preview/b.jpg"},
        ],
        page_normative_refs=[
            [{"kind": "ГОСТ", "ref": "ГОСТ 481-80"}],
            [],
            [{"kind": "ГОСТ", "ref": "ГОСТ 481-80"}],
        ],
    )
    assert "Листов в файле:</strong> 12" in md
    assert "обработано:</strong> 8" in md
    assert "не все листы" in md
    assert "<strong>Файл:</strong>" in md
    assert "**Файл:**" not in md
    assert 'class="normative-workspace"' in md
    assert "normative-workspace-list" in md
    assert "normative-workspace-preview" in md
    assert 'data-preview-page="1"' in md
    assert "preview-page-btn" in md


def test_material_prefix_not_preferred_in_merge():
    out = merge_normative_refs_from_sources(
        "20-В ГОСТ 2590-2006",
        "ГОСТ 2590-2006",
        "ГОСТ 2590-2006",
    )
    gost = [r for r in out if r["kind"] == "ГОСТ" and "2590" in r["ref"]]
    assert gost
    assert gost[0]["ref"].startswith("ГОСТ")


def test_hide_tnpa_column_when_search_disabled():
    md = normative_refs_to_markdown(
        [{"kind": "ГОСТ", "ref": "ГОСТ 10704-91"}],
        filename="x.pdf",
        include_tnpa=False,
        preview_pages=[{"page": 1, "url": "/api/preview/a.jpg"}],
    )
    assert "<th>ТНПА</th>" not in md
    assert "найдено в ТНПА" not in md
    assert "<th>Стройдок</th>" in md
    assert "найдено в Стройдок" in md

    md_on = normative_refs_to_markdown(
        [{"kind": "ГОСТ", "ref": "ГОСТ 10704-91"}],
        filename="x.pdf",
        include_tnpa=True,
        preview_pages=[{"page": 1, "url": "/api/preview/a.jpg"}],
    )
    assert "<th>ТНПА</th>" in md_on
    assert "найдено в ТНПА" in md_on
