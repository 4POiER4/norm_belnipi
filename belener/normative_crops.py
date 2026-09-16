"""Нормативы из PDF: тот же tile OCR, что и для полного текста листа."""

from __future__ import annotations

import logging
import time
from typing import Any

import fitz

from belener.normative_refs import merge_normative_refs, merge_normative_refs_from_sources
from belener.tile_ocr import (
    PIPELINE,
    TILE_COLS,
    TILE_ROWS,
    extract_document_tiles,
    page_tile_jobs,
)

log = logging.getLogger("belener.normative_crops")

# На коротких документах общий проход нужен для голосования между пересекающимися
# тайлами. На длинных документах используется сетка 1x1, поэтому повторный разбор
# всего корпуса не добавляет сигнал, но даёт нелинейное время работы.
GLOBAL_MERGE_MAX_PAGES = 20

__all__ = [
    "PIPELINE",
    "TILE_COLS",
    "TILE_ROWS",
    "extract_normatives_document_crops",
    "extract_normatives_page_tiles",
    "page_tile_jobs",
    "_finalize_refs",
]


def _finalize_refs(all_sources: list[str]) -> list[dict[str, str]]:
    uniq = [s for s in all_sources if str(s or "").strip()]
    if not uniq:
        return []
    return merge_normative_refs_from_sources(*uniq)


def _merge_document_refs(
    page_texts: list[str],
    all_sources: list[str],
) -> tuple[list[dict[str, str]], list[list[dict[str, str]]]]:
    """Постраничный разбор и ограниченное голосование между OCR-источниками."""
    page_normative_refs = [
        _finalize_refs([text]) if str(text or "").strip() else []
        for text in page_texts
    ]
    document_refs: list[dict[str, str]] = []
    if len(page_texts) <= GLOBAL_MERGE_MAX_PAGES:
        document_refs = _finalize_refs(all_sources)
    else:
        log.info(
            "normative merge: skip global reparse pages=%s sources=%s chars=%s",
            len(page_texts),
            len(all_sources),
            sum(len(str(s or "")) for s in all_sources),
        )
    refs = merge_normative_refs(
        *[prefs for prefs in page_normative_refs if prefs],
        document_refs,
    )
    return refs, page_normative_refs


def extract_normatives_page_tiles(doc, page_index, *, dpi, deadline, tile_max_sec, overlap_frac):
    from belener.tile_ocr import extract_page_tiles

    chunks, _, _ = extract_page_tiles(
        doc, page_index, dpi=dpi, deadline=deadline, tile_max_sec=tile_max_sec, overlap_frac=overlap_frac
    )
    return chunks


def extract_normatives_document_crops(
    doc: fitz.Document,
    filename: str = "document.pdf",
    *,
    pipeline_deadline: float | None = None,
    source_path: str | None = None,
) -> dict[str, Any]:
    from belener.scanned import page_text_layer_usable

    tiles = extract_document_tiles(
        doc,
        filename,
        pipeline_deadline=pipeline_deadline,
        source_path=source_path,
    )
    page_texts = list(tiles.get("page_texts") or [])
    all_sources = list(tiles.get("all_sources") or [])

    # Полный текстовый слой листа — даже если tile OCR оборвался по бюджету.
    # Иначе на searchable PDF теряются ГОСТ/СН из непройденных тайлов.
    pages_n = int(tiles.get("pages_planned") or tiles.get("pages_processed") or doc.page_count or 0)
    pages_n = max(pages_n, int(tiles.get("pages_processed") or 0))
    pages_n = min(max(1, pages_n), doc.page_count)
    for i in range(pages_n):
        if not page_text_layer_usable(doc, i):
            continue
        try:
            layer = (doc[i].get_text("text") or "").strip()
        except Exception:
            layer = ""
        if not layer:
            continue
        if i < len(page_texts):
            if layer not in page_texts[i]:
                page_texts[i] = f"{layer}\n\n{page_texts[i]}".strip() if page_texts[i] else layer
        else:
            page_texts.append(layer)
        if layer not in all_sources:
            all_sources.insert(0, layer)

    parse_t0 = time.monotonic()
    # Сначала постраничные (как жёлтая подсветка), потом документный merge —
    # иначе соседи СН/СП пропадают из таблицы или уезжают в конец списка.
    refs, page_normative_refs = _merge_document_refs(page_texts, all_sources)
    log.info(
        "normative merge done %.1fs pages=%s refs=%s",
        time.monotonic() - parse_t0,
        len(page_texts),
        len(refs),
    )
    return {
        "ok": True,
        "filename": filename,
        "page_count": doc.page_count,
        "pages_processed": tiles.get("pages_processed", 0),
        "tiles_done": tiles.get("tiles_done"),
        "tiles_expected": tiles.get("tiles_expected"),
        "budget_exhausted": bool(tiles.get("budget_exhausted")),
        "elapsed_sec": tiles.get("elapsed_sec", 0.0),
        "pipeline": f"{PIPELINE}+normative",
        "normative_refs": refs,
        "page_normative_refs": page_normative_refs,
        "page_preview_words": tiles.get("page_preview_words") or [],
        "page_tile_zones": tiles.get("page_tile_zones") or [],
        "vision_model": None,
        "source_text_chars": sum(len(s) for s in page_texts),
        "page_texts": all_sources,
        "drawing": None,
    }
