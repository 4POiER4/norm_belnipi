"""Извлечение нормативов (ГОСТ/ОСТ/ТУ/…) из PDF и изображений — OCR по тайлам листа."""

from __future__ import annotations

import logging
import json
import os
import re
import uuid
from html import escape
from datetime import date
from pathlib import Path
from typing import Any

import fitz

from belener.normative_crops import extract_normatives_document_crops
from belener.status_merge import combined_verdict, normacs_state, stn_state
from belener.config import preview_store_dir, prune_preview_store
from belener.normative_refs import (
    _phrase_matches_highlight_ref,
    _ref_highlight_target,
    highlight_patterns_for_normative_ref,
    word_looks_like_kind_token,
)

log = logging.getLogger("belener.normative_extract")

_KIND_ALIASES: dict[str, frozenset[str]] = {
    "ГОСТ": frozenset({"гост", "gost"}),
    "ОСТ": frozenset({"ост", "ost", "oct"}),
    "ТУ": frozenset({"ту", "tu"}),
    "СТБ": frozenset({"стб", "stb"}),
    "СТП": frozenset({"стп", "stp", "ctn", "stn"}),
    "ТКП": frozenset({"ткп", "tkp"}),
    "СНиП": frozenset({"снип", "snip", "chn", "chip"}),
    "СН": frozenset({"сн", "ch"}),
    "НРР": frozenset({"нрр", "hrr", "nrr"}),
    "СП": frozenset({"сп", "sp"}),
    "РД": frozenset({"рд", "rd", "pa"}),
    "СО": frozenset({"со", "co", "so"}),
}


def _kind_aliases(kind: str) -> frozenset[str]:
    return _KIND_ALIASES.get(kind, frozenset({kind.casefold()}))


def _kind_regex(kind: str) -> str:
    return {
        "ГОСТ": r"гост|gost",
        "ОСТ": r"ост|ost|oct",
        "ТУ": r"ту|tu",
        "СТБ": r"стб|stb",
        "СТП": r"стп|stp|ctn|stn",
        "ТКП": r"ткп|tkp",
        "СНиП": r"снип|snip|chn|chip",
        "СН": r"сн|ch",
        "НРР": r"нрр|hrr|nrr",
        "СП": r"сп|sp",
        "РД": r"рд|rd|pa",
        "СО": r"со|co|so",
    }.get(kind, re.escape(kind))


def _word_text(word) -> str:
    raw = str(word[4] or "").strip()
    text = re.sub(r"^[\(\[\"']+|[\)\]\}\"'.,;:!?]+$", "", raw)
    return re.sub(r",(?=\d)", ".", text)


def _word_text_sane(word) -> bool:
    txt = _word_text(word)
    if not txt or len(txt) > 120:
        return False
    if "\n" in txt or "\t" in txt:
        return False
    return True


def _number_body_matches_ref(word, ref_str: str, kind: str) -> bool:
    """Номер без маркера типа: 34.03.304-87 ↔ РД 34.03.304-67 (год OCR может отличаться)."""
    from belener.normative_refs import _body_year_digits, _sanitize_normative_ref

    tok = _word_text(word) if not isinstance(word, str) else re.sub(r",(?=\d)", ".", word)
    if not tok or not re.search(r"\d", tok):
        return False
    ref_body, _ref_year = _body_year_digits(kind, _sanitize_normative_ref(ref_str))
    tok_body, _tok_year = _body_year_digits(kind, f"{kind} {tok}")
    ref_d = re.sub(r"\D", "", ref_body)
    tok_d = re.sub(r"\D", "", tok_body)
    if len(ref_d) < 4 or not tok_d:
        return False
    return tok_d == ref_d


def _word_rect(word) -> fitz.Rect:
    return fitz.Rect(word[:4])


def _words_on_same_line(words: list, a: int, b: int) -> bool:
    ra, rb = _word_rect(words[a]), _word_rect(words[b])
    tolerance = max(3.5, min(ra.height, rb.height) * 0.55)
    return abs((ra.y0 + ra.y1) * 0.5 - (rb.y0 + rb.y1) * 0.5) <= tolerance


def _median_word_height(words: list, start: int, end: int) -> float:
    hs = [_word_rect(words[k]).height for k in range(start, end + 1) if _word_text(words[k])]
    if not hs:
        return 12.0
    hs.sort()
    return hs[len(hs) // 2]


def _tighten_word_rect(word, *, line_h: float) -> fitz.Rect:
    """OCR часто раздувает bbox; поджимаем к высоте строки таблицы."""
    r = _word_rect(word)
    txt = _word_text(word)
    if not txt:
        return r
    h = max(r.height, 0.1)
    if h > line_h * 1.45:
        cy = (r.y0 + r.y1) * 0.5
        nh = min(h, line_h * 1.12)
        r = fitz.Rect(r.x0, cy - nh * 0.5, r.x1, cy + nh * 0.5)
    if r.width > 14:
        inset = min(1.2, r.width * 0.03)
        r = fitz.Rect(r.x0 + inset, r.y0, r.x1 - inset, r.y1)
    return r


def _rect_overlap_ratio(a: fitz.Rect, b: fitz.Rect) -> float:
    inter = a & b
    if inter.is_empty:
        return 0.0
    area = inter.get_area()
    denom = min(a.get_area(), b.get_area())
    return area / denom if denom > 0 else 0.0


def _word_has_kind_marker(word, kind: str) -> bool:
    text = _word_text(word)
    if not text:
        return False
    if text.casefold() in _kind_aliases(kind):
        return True
    if word_looks_like_kind_token(text, kind):
        return True
    return bool(re.search(rf"(?<![a-zа-яё]){_kind_regex(kind)}(?![a-zа-яё])", text, re.I))


def _rect_fingerprint(rect: fitz.Rect) -> tuple[int, int, int, int]:
    """Округление до пункта: грубое //2 сливало соседние строки таблицы."""
    return (
        round(rect.x0),
        round(rect.y0),
        round(rect.x1),
        round(rect.y1),
    )


def _dedupe_rects(rects: list[fitz.Rect]) -> list[fitz.Rect]:
    out: list[fitz.Rect] = []
    seen: set[tuple[int, int, int, int]] = set()
    for r in rects:
        if r is None or r.is_empty or r.width < 2 or r.height < 2:
            continue
        fp = _rect_fingerprint(r)
        if fp in seen:
            continue
        seen.add(fp)
        out.append(r)
    return out


def _is_number_token(text: str) -> bool:
    t = (text or "").strip()
    if not t or not re.search(r"\d", t):
        return False
    if re.match(r"^[A-Za-zА-Яа-яЁё]-\d", t):
        return False
    if re.fullmatch(r"\d{1,2}", t):
        return False
    letters = re.sub(r"[^a-zа-яё]", "", t, flags=re.I)
    return len(letters) < 3


_SNIP_NUMBER_RE = re.compile(r"^\d{1,2}\.\d{2}\.\d{2}-\d{2,4}$")
_STP_RD_NUMBER_RE = re.compile(r"^\d+(?:\.\d+){2,}-\d{2,4}$")
_TKP_NUMBER_RE = re.compile(r"^\d{2}(?:[.\-]\d+)+-\d{2,4}$")
_SP_NUMBER_RE = re.compile(r"^\d{2}\.\d{2,3}\.?\d{0,5}-\d{4}$")
_TU_NUMBER_RE = re.compile(r"^\d{1,3}(?:-\d+){2,}-\d{2,4}$")

_BARE_NUMBER_KIND_PATTERNS: dict[str, re.Pattern[str]] = {
    "СНиП": _SNIP_NUMBER_RE,
    "СТП": _STP_RD_NUMBER_RE,
    "РД": _STP_RD_NUMBER_RE,
    "ТКП": _TKP_NUMBER_RE,
    "СП": _SP_NUMBER_RE,
    "ТУ": _TU_NUMBER_RE,
}


def _bare_number_token_ok(kind: str, text: str) -> bool:
    """Номер без маркера типа — только для узких форматов (не ГОСТ/ОСТ)."""
    t = re.sub(r",(?=\d)", ".", (text or "").strip())
    rx = _BARE_NUMBER_KIND_PATTERNS.get(kind)
    return bool(rx and rx.match(t))


def _phrase_matches_ref(phrase: str, ref_str: str) -> bool:
    kind, canon, dedupe = _ref_highlight_target(ref_str)
    if not kind or not canon:
        return False
    from belener.normative_refs import _normalize_highlight_phrase

    phrase = _normalize_highlight_phrase(phrase, kind)
    return _phrase_matches_highlight_ref(
        phrase, kind=kind, canon=canon, dedupe=dedupe, ref_str=ref_str
    )


def _trim_span_to_ref_tokens(words: list, start: int, end: int, kind: str) -> tuple[int, int]:
    kind_i = start
    for k in range(start, end + 1):
        if _word_has_kind_marker(words[k], kind):
            kind_i = k
            break
    last = kind_i
    for k in range(kind_i + 1, end + 1):
        if not _words_on_same_line(words, kind_i, k):
            break
        if _is_number_token(_word_text(words[k])):
            last = k
        else:
            break
    return kind_i, last


def _span_phrase(words: list, start: int, end: int) -> str:
    return " ".join(_word_text(words[k]) for k in range(start, end + 1) if _word_text(words[k]))


def _span_matches_ref(words: list, start: int, end: int, ref_str: str) -> bool:
    kind, _, _ = _ref_highlight_target(ref_str)
    if kind:
        a, b = _trim_span_to_ref_tokens(words, start, end, kind)
        if a <= b and _word_has_kind_marker(words[a], kind):
            phrase = _span_phrase(words, a, b)
            if phrase and _phrase_matches_ref(phrase, ref_str):
                return True
    phrase = _span_phrase(words, start, end)
    return bool(phrase) and _phrase_matches_ref(phrase, ref_str)


def _pinpoint_rects_for_span(words: list, start: int, end: int) -> list[fitz.Rect]:
    line_h = _median_word_height(words, start, end)
    rects: list[fitz.Rect] = []
    for k in range(start, end + 1):
        txt = _word_text(words[k])
        if txt:
            rects.append(_tighten_word_rect(words[k], line_h=line_h))
    return _dedupe_rects(rects)


def _all_word_spans_for_ref(words: list, ref_str: str) -> list[tuple[int, int]]:
    """Все вхождения норматива: повтор одного ГОСТ на листе → несколько spans."""
    kind, canon, _ = _ref_highlight_target(ref_str)
    if not kind or not canon or not words:
        return []

    n = len(words)
    seen: set[tuple[int, int]] = set()
    spans: list[tuple[int, int]] = []

    for k in range(n):
        token = _word_text(words[k])
        if not token or not _word_has_kind_marker(words[k], kind):
            continue
        if _phrase_matches_ref(token, ref_str):
            pair = (k, k)
            if pair not in seen:
                seen.add(pair)
                spans.append(pair)

    for i in range(n):
        for j in range(i, min(i + 14, n)):
            if j > i and not _words_on_same_line(words, i, j):
                continue
            if not _span_matches_ref(words, i, j, ref_str):
                continue
            a, b = _trim_span_to_ref_tokens(words, i, j, kind)
            if a > b or not _span_matches_ref(words, a, b, ref_str):
                continue
            if not _word_has_kind_marker(words[a], kind):
                continue
            if not all(_words_on_same_line(words, a, k) for k in range(a, b + 1)):
                continue
            pair = (a, b)
            if pair in seen:
                continue
            seen.add(pair)
            spans.append(pair)

    from belener.normative_refs import _body_year_digits

    body, _year = _body_year_digits(kind, ref_str)
    if body and len(body) >= 4:
        for k in range(n):
            token = _word_text(words[k])
            if not token or not _is_number_token(token):
                continue
            if not _phrase_matches_ref(f"{kind} {token}", ref_str):
                continue
            a = k
            for j in range(k - 1, max(-1, k - 6), -1):
                if not _words_on_same_line(words, j, k):
                    break
                if _word_has_kind_marker(words[j], kind):
                    a = j
                    break
            if not _word_has_kind_marker(words[a], kind):
                continue
            pair = (a, k)
            if pair in seen:
                continue
            seen.add(pair)
            spans.append(pair)

    if body and len(body) >= 4:
        for k in range(n):
            token = _word_text(words[k])
            if not token or not _number_body_matches_ref(words[k], ref_str, kind):
                continue
            kind_i = k
            found_kind = False
            for j in range(k - 1, max(-1, k - 4), -1):
                if not _words_on_same_line(words, j, k):
                    break
                if _word_has_kind_marker(words[j], kind):
                    kind_i = j
                    found_kind = True
                    break
            if not found_kind:
                if _bare_number_token_ok(kind, token):
                    pair = (k, k)
                    if pair not in seen:
                        seen.add(pair)
                        spans.append(pair)
                continue
            pair = (kind_i, k)
            if pair in seen:
                continue
            seen.add(pair)
            spans.append(pair)

    return spans


def _quad_to_word_rects(words: list, quad_rect: fitz.Rect, ref_str: str) -> list[fitz.Rect]:
    kind, _, _ = _ref_highlight_target(ref_str)
    idxs = [k for k, w in enumerate(words) if _word_rect(w).intersects(quad_rect)]
    if not idxs:
        return []

    rects: list[fitz.Rect] = []
    seen: set[tuple[int, int]] = set()
    idx_set = set(idxs)
    for i in idxs:
        if not _word_has_kind_marker(words[i], kind):
            continue
        for j in idxs:
            if j < i or j - i > 11:
                continue
            if not _span_matches_ref(words, i, j, ref_str):
                continue
            a, b = _trim_span_to_ref_tokens(words, i, j, kind)
            if a > b or not _span_matches_ref(words, a, b, ref_str):
                continue
            if not all(k in idx_set for k in range(a, b + 1)):
                continue
            if not all(_words_on_same_line(words, a, k) for k in range(a, b + 1)):
                continue
            pair = (a, b)
            if pair in seen:
                continue
            seen.add(pair)
            rects.extend(_pinpoint_rects_for_span(words, a, b))
    return _dedupe_rects(rects)


def _search_quad_rects(page: fitz.Page, words: list, ref_str: str) -> list[fitz.Rect]:
    rects: list[fitz.Rect] = []
    for pattern in highlight_patterns_for_normative_ref(ref_str):
        try:
            hits = page.search_for(pattern, quads=True)
        except TypeError:
            hits = page.search_for(pattern)
        for hit in hits or []:
            try:
                rect = hit.rect
            except Exception:
                rect = fitz.Rect(hit)
            part = _quad_to_word_rects(words, rect, ref_str)
            if part:
                rects.extend(part)
    return _dedupe_rects(rects)


def _word_span_rects(words: list, ref_str: str) -> list[fitz.Rect]:
    rects: list[fitz.Rect] = []
    for start, end in _all_word_spans_for_ref(words, ref_str):
        rects.extend(_pinpoint_rects_for_span(words, start, end))
    return _dedupe_rects(rects)


def _expand_hit_rect(rect: fitz.Rect, *, pad: float = 5.0) -> fitz.Rect:
    return fitz.Rect(rect.x0 - pad, rect.y0 - pad, rect.x1 + pad, rect.y1 + pad)


def _merge_word_sources(sources: list[list]) -> list:
    """Текстовый слой + OCR: объединить без дублей по позиции."""
    out: list = []
    for src in sources:
        if not src:
            continue
        for w in src:
            if not _word_text_sane(w):
                continue
            txt = _word_text(w)
            rw = _word_rect(w)
            if rw.is_empty:
                continue
            duplicate = False
            for kept in out:
                if _word_text(kept) != txt:
                    continue
                if _rect_overlap_ratio(rw, _word_rect(kept)) > 0.62:
                    duplicate = True
                    break
            if not duplicate:
                out.append(w)
    out.sort(key=lambda w: (round(_word_rect(w).y0), _word_rect(w).x0))
    return out


def _highlight_rects_for_ref(
    page: fitz.Page | None,
    words: list,
    ref_str: str,
) -> list[fitz.Rect]:
    """Все вхождения норматива: search_for по PDF + spans по словам."""
    rects: list[fitz.Rect] = []
    seen_fp: set[tuple[int, int, int, int]] = set()

    def collect(new_rects: list[fitz.Rect]) -> None:
        for r in _dedupe_rects(new_rects):
            fp = _rect_fingerprint(r)
            if fp in seen_fp:
                continue
            seen_fp.add(fp)
            rects.append(r)

    if page is not None:
        for pattern in highlight_patterns_for_normative_ref(ref_str):
            try:
                hits = page.search_for(pattern, quads=True)
            except TypeError:
                hits = page.search_for(pattern)
            for hit in hits or []:
                try:
                    hit_rect = hit.rect
                except Exception:
                    hit_rect = fitz.Rect(hit)
                clip_words = page.get_text("words", clip=_expand_hit_rect(hit_rect)) or []
                if clip_words:
                    collect(_word_span_rects(clip_words, ref_str))
                if words:
                    collect(_quad_to_word_rects(words, hit_rect, ref_str))

    if words:
        collect(_word_span_rects(words, ref_str))

    return rects


def _merge_source_rects(sources: list[list], ref_str: str, page: fitz.Page | None) -> list[fitz.Rect]:
    """Совместимость: объединённые слова + search_for."""
    merged_words = _merge_word_sources(sources)
    return _highlight_rects_for_ref(page, merged_words, ref_str)


def _find_pinpoint_rects(
    words: list,
    ref_str: str,
    *,
    page: fitz.Page | None = None,
) -> list[fitz.Rect]:
    rects: list[fitz.Rect] = []
    if page is not None:
        rects.extend(_search_quad_rects(page, words, ref_str))
    rects.extend(_word_span_rects(words, ref_str))
    return _dedupe_rects(rects)


def _mark_pinpoint_rect(page: fitz.Page, rect: fitz.Rect, used: set[tuple[int, int, int, int]]) -> None:
    if rect is None or rect.is_empty:
        return
    fp = _rect_fingerprint(rect)
    if fp in used:
        return
    used.add(fp)
    pad = 0.25
    box = fitz.Rect(rect.x0 - pad, rect.y0 - pad, rect.x1 + pad, rect.y1 + pad) & page.rect
    if box.is_empty:
        return
    annot = page.add_rect_annot(box)
    annot.set_colors(stroke=(0.95, 0.78, 0.0), fill=(1.0, 1.0, 0.0))
    annot.set_opacity(0.38)
    annot.set_border(width=0.25)
    annot.update()


def _draw_highlight_shapes(page: fitz.Page, rects: list[fitz.Rect]) -> int:
    """Жёлтые рамки на сканах: shape рисуется в pixmap, annots на image-only PDF не видны."""
    shape = page.new_shape()
    drawn = 0
    seen: set[tuple[int, int, int, int]] = set()
    for rect in rects:
        if rect is None or rect.is_empty:
            continue
        fp = _rect_fingerprint(rect)
        if fp in seen:
            continue
        seen.add(fp)
        pad = 0.5
        box = fitz.Rect(rect.x0 - pad, rect.y0 - pad, rect.x1 + pad, rect.y1 + pad) & page.rect
        if box.is_empty or box.width < 1 or box.height < 1:
            continue
        shape.draw_rect(box)
        drawn += 1
    if drawn:
        shape.finish(color=(0.95, 0.78, 0.0), fill=(1.0, 1.0, 0.0), fill_opacity=0.42, width=0.2)
        shape.commit(overlay=True)
    return drawn


def _render_preview_image_with_highlights(
    page: fitz.Page,
    rects: list[fitz.Rect],
    *,
    dpi: int = 144,
    base_image=None,
):
    """JPEG-превью: жёлтые рамки рисуем поверх pixmap (надёжно на image-only PDF)."""
    from PIL import Image, ImageDraw

    if base_image is None:
        pix = page.get_pixmap(dpi=dpi, annots=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    else:
        img = base_image
    if not rects:
        return img
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    scale = dpi / 72.0
    seen: set[tuple[int, int, int, int]] = set()
    for rect in rects:
        if rect is None or rect.is_empty:
            continue
        rect = rect * page.rotation_matrix
        fp = _rect_fingerprint(rect)
        if fp in seen:
            continue
        seen.add(fp)
        x0 = int(rect.x0 * scale) - 2
        y0 = int(rect.y0 * scale) - 2
        x1 = int(rect.x1 * scale) + 2
        y1 = int(rect.y1 * scale) + 2
        draw.rectangle(
            [x0, y0, x1, y1],
            fill=(255, 235, 0, 160),
            outline=(220, 150, 0, 255),
            width=2,
        )
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def _filter_pinpoint_rects(rects: list[fitz.Rect], page_rect: fitz.Rect | None) -> list[fitz.Rect]:
    """Отсечь раздутые bbox (зоны тайлов, ошибочный OCR) — только точечная подсветка."""
    if not rects:
        return []
    pr = page_rect
    max_w = min(210.0, (pr.width * 0.32) if pr and not pr.is_empty else 210.0)
    max_h = min(42.0, (pr.height * 0.07) if pr and not pr.is_empty else 42.0)
    max_area = max_w * max_h * 1.8
    out: list[fitz.Rect] = []
    for rect in rects:
        if rect is None or rect.is_empty:
            continue
        if rect.width > max_w or rect.height > max_h:
            continue
        if rect.width * rect.height > max_area:
            continue
        out.append(rect)
    return _dedupe_rects(out)


def _zone_text_mentions_ref(text: str, ref_str: str) -> bool:
    import re

    from belener.normative_refs import _sanitize_normative_ref

    ref_norm = re.sub(r"[\s\-–—]+", "", _sanitize_normative_ref(ref_str).casefold())
    if len(ref_norm) < 5:
        return False
    blob = re.sub(r"[\s\-–—]+", "", str(text or "").casefold())
    return ref_norm in blob or ref_norm[:-1] in blob or ref_norm[:-2] in blob


def _ocr_words_for_ref_zones(
    doc: fitz.Document,
    page_index: int,
    ref_str: str,
    tile_zones: list,
    *,
    dpi: int = 320,
) -> list:
    """Точечный OCR только в зонах, где текст тайла уже содержит норматив."""
    from belener.ocr import tesseract_words_from_rect

    words: list = []
    for _zone, rect, text in tile_zones:
        if not text or rect is None or rect.is_empty:
            continue
        if not _zone_text_mentions_ref(text, ref_str):
            continue
        batch = tesseract_words_from_rect(doc, page_index, rect, dpi=dpi, timeout=10.0)
        if batch:
            words.extend(batch)
    return words


def _collect_highlight_rects(
    page: fitz.Page | None,
    refs: list[dict],
    sources: list[list],
    *,
    doc: fitz.Document | None = None,
    page_index: int = 0,
    tile_zones: list | None = None,
    mark_items: list[dict] | None = None,
) -> tuple[int, int, list[fitz.Rect]]:
    """Собрать все bbox для нормативов из таблицы ответа (только точечные)."""
    merged_words = _merge_word_sources(sources)
    page_rect = page.rect if page is not None else None
    seen_fp: set[tuple[int, int, int, int]] = set()
    all_rects: list[fitz.Rect] = []
    highlighted_refs = 0

    for r in refs:
        ref_str = (r.get("ref") or "").strip()
        if not ref_str:
            continue
        rects = _highlight_rects_for_ref(page, merged_words, ref_str)
        rects = _filter_pinpoint_rects(_dedupe_rects(rects), page_rect)
        if not rects and tile_zones and doc is not None:
            extra = _ocr_words_for_ref_zones(doc, page_index, ref_str, tile_zones)
            if extra:
                merged_extra = _merge_word_sources([merged_words, extra])
                rects = _highlight_rects_for_ref(page, merged_extra, ref_str)
                rects = _filter_pinpoint_rects(_dedupe_rects(rects), page_rect)
        if not rects:
            log.debug("preview highlight miss: %s", ref_str)
            continue
        highlighted_refs += 1
        if mark_items is not None and page is not None:
            bounds = page.rect
            for rect in rects:
                rotated = (rect * page.rotation_matrix) & bounds
                if rotated.is_empty:
                    continue
                mark_items.append({
                    "ref": ref_str,
                    "x": round(100 * (rotated.x0 - bounds.x0) / bounds.width, 5),
                    "y": round(100 * (rotated.y0 - bounds.y0) / bounds.height, 5),
                    "w": round(100 * rotated.width / bounds.width, 5),
                    "h": round(100 * rotated.height / bounds.height, 5),
                })
        for rect in rects:
            fp = _rect_fingerprint(rect)
            if fp in seen_fp:
                continue
            seen_fp.add(fp)
            all_rects.append(rect)

    return highlighted_refs, len(all_rects), all_rects


def _fullpage_ocr_words(page: fitz.Page, *, page_index: int = 0) -> list:
    """Полностраничный OCR (как в старых версиях) — запасной путь для подсветки."""
    import gc

    best: list = []
    for dpi in (220, 180, 260, 140):
        try:
            gc.collect()
            try:
                fitz.TOOLS.store_shrink(100)
            except Exception:
                pass
            tp = page.get_textpage_ocr(language="rus+eng", dpi=dpi, full=True)
            try:
                ocr_words = page.get_text("words", textpage=tp) or []
            finally:
                del tp
            if len(ocr_words) > len(best):
                best = ocr_words
            if best and len(best) >= 40:
                break
        except Exception as exc:
            log.debug("fullpage preview OCR page=%s dpi=%s: %s", page_index + 1, dpi, exc)
    return best


def _preview_words_for_page(
    doc: fitz.Document,
    page_index: int,
    *,
    page_count: int = 1,
    deadline: float | None = None,
) -> list:
    from belener.tile_ocr import collect_page_preview_words

    return collect_page_preview_words(
        doc,
        page_index,
        page_count=page_count,
        deadline=deadline,
    )


def _preview_word_sources(
    pdf_path: str,
    page_index: int,
    *,
    page_count: int = 1,
    cached_words: list | None = None,
    deadline: float | None = None,
) -> list[list]:
    """Слова листа для подсветки: текстовый слой + тайлы; без повторного OCR если слова уже есть."""
    import gc
    import time

    sources: list[list] = []
    cached_count = len(cached_words or [])
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        from belener.scanned import page_text_layer_usable

        usable_text = page_text_layer_usable(doc, page_index)
        if usable_text:
            text_words = page.get_text("words") or []
            if text_words:
                sources.append(text_words)

        if cached_words:
            sources.append(cached_words)

        word_total = sum(len(s) for s in sources)
        if cached_count >= 50 or word_total >= 50:
            log.debug(
                "preview words page=%s cached=%s total=%s (skip extra OCR)",
                page_index + 1,
                cached_count,
                word_total,
            )
            return sources

        now = time.monotonic()
        if deadline is not None and now >= deadline:
            return sources

        if word_total < 30:
            dl = deadline if deadline is not None else now + 25.0
            if dl > now + 1.0:
                ocr_words = _preview_words_for_page(
                    doc,
                    page_index,
                    page_count=page_count,
                    deadline=dl,
                )
                if ocr_words:
                    sources.append(ocr_words)

        word_total = sum(len(s) for s in sources)
        if word_total < 30 and (deadline is None or time.monotonic() < deadline - 8.0):
            full_words = _fullpage_ocr_words(page, page_index=page_index)
            if full_words:
                sources.append(full_words)

        log.debug(
            "preview words page=%s sources=%s total=%s scan=%s",
            page_index + 1,
            len(sources),
            sum(len(s) for s in sources),
            not usable_text,
        )
    finally:
        doc.close()
        gc.collect()
    return sources


def _highlight_on_page(
    page: fitz.Page,
    refs: list[dict],
    *,
    words: list | None = None,
    extra_word_sources: list[list] | None = None,
) -> tuple[int, int]:
    """Точечная подсветка всех вхождений нормативов из таблицы ответа."""
    sources: list[list] = []
    if words is not None:
        sources.append(words)
    elif page is not None:
        sources.append(page.get_text("words") or [])
    for extra in extra_word_sources or []:
        if extra and extra not in sources:
            sources.append(extra)

    merged_words = _merge_word_sources(sources)

    used: set[tuple[int, int, int, int]] = set()
    highlighted_refs = 0
    total_marks = 0

    for r in refs:
        ref_str = (r.get("ref") or "").strip()
        if not ref_str:
            continue
        rects = _highlight_rects_for_ref(page, merged_words, ref_str)
        rects = _dedupe_rects(rects)
        if not rects:
            log.debug("preview highlight miss: %s", ref_str)
            continue
        highlighted_refs += 1
        for rect in rects:
            before = len(used)
            _mark_pinpoint_rect(page, rect, used)
            if len(used) > before:
                total_marks += 1

    return highlighted_refs, total_marks


def _preview_page_indices(page_count: int, page_normative_refs: list[list[dict]] | None = None) -> list[int]:
    """Все листы документа в превью (подсветка только где есть нормативы)."""
    del page_normative_refs  # совместимость вызовов; фильтр по нормативам убран
    n = max(0, int(page_count))
    return list(range(n)) if n > 0 else []


def _page_refs_for_preview(
    page_index: int,
    refs: list[dict],
    page_normative_refs: list[list[dict]] | None,
) -> list[dict]:
    """Подсветка листа: только то, что есть в итоговой таблице."""
    from belener.normative_refs import _canonical_key

    page_refs: list[dict] = []
    if page_normative_refs is not None:
        if page_index < len(page_normative_refs):
            page_refs = list(page_normative_refs[page_index] or [])
        else:
            return []
    else:
        return list(refs or [])
    if not refs:
        return page_refs
    table_keys = {
        _canonical_key(str(item.get("kind") or ""), str(item.get("ref") or ""))
        for item in refs
    }
    table_keys.discard("")
    on_page = [
        item
        for item in page_refs
        if _canonical_key(str(item.get("kind") or ""), str(item.get("ref") or "")) in table_keys
    ]
    # Таблица уже объединена с page_normative_refs — on_page должен совпасть.
    # Fallback: не гасим подсветку, если ключи по какой-то причине разъехались.
    return on_page if on_page else page_refs


def _pages_by_ref(page_normative_refs: list[list[dict]] | None) -> dict[str, list[int]]:
    """ref → номера листов (1-based), где обозначение встретилось."""
    out: dict[str, list[int]] = {}
    if not page_normative_refs:
        return out
    for i, prefs in enumerate(page_normative_refs):
        page_no = i + 1
        for item in prefs or []:
            ref = str(item.get("ref") or "").strip()
            if not ref:
                continue
            pages = out.setdefault(ref, [])
            if page_no not in pages:
                pages.append(page_no)
    return out


def generate_pdf_preview_pages_with_highlights(
    pdf_path: str,
    refs: list[dict],
    *,
    page_normative_refs: list[list[dict]] | None = None,
    page_preview_words: list[list] | None = None,
    page_tile_zones: list[list] | None = None,
    preview_word_deadline: float | None = None,
    pipeline_deadline: float | None = None,
) -> list[dict[str, Any]]:
    """Превью листов PDF с жёлтой подсветкой нормативов из ответа."""
    import time

    pages_out: list[dict[str, Any]] = []
    try:
        probe = fitz.open(pdf_path)
        page_count = probe.page_count
        probe.close()
        if page_count == 0:
            return pages_out

        store_dir = preview_store_dir()
        try:
            pruned = prune_preview_store()
            if pruned:
                log.info("preview store: pruned %s old files", pruned)
        except Exception as e:
            log.debug("preview prune skipped: %s", e)
        for page_index in _preview_page_indices(page_count, page_normative_refs):
            if pipeline_deadline is not None and time.monotonic() >= pipeline_deadline - 2.0:
                log.warning("preview: stop at page=%s (deadline)", page_index + 1)
                break
            page_refs = _page_refs_for_preview(page_index, refs, page_normative_refs)
            if not page_refs:
                doc = fitz.open(pdf_path)
                try:
                    preview_img = _render_preview_image_with_highlights(doc[page_index], [], dpi=144)
                finally:
                    doc.close()
                fname = f"preview_{uuid.uuid4().hex}.jpg"
                out_path = os.path.join(store_dir, fname)
                preview_img.save(out_path, format="JPEG", quality=92)
                if not os.path.isfile(out_path):
                    continue
                pages_out.append(
                    {
                        "page": page_index + 1,
                        "url": f"/api/preview/{fname}",
                        "refs": [],
                        "marks": 0,
                    }
                )
                continue
            cached = None
            if page_preview_words and page_index < len(page_preview_words):
                cached = page_preview_words[page_index] or None
            dl = preview_word_deadline
            if dl is None:
                dl = pipeline_deadline
            if dl is None and not cached:
                dl = time.monotonic() + (25.0 if page_count <= 1 else 15.0)
            tile_zones = None
            if page_tile_zones and page_index < len(page_tile_zones):
                tile_zones = page_tile_zones[page_index] or None
            word_sources = _preview_word_sources(
                pdf_path,
                page_index,
                page_count=page_count,
                cached_words=cached,
                deadline=dl,
            )
            doc = fitz.open(pdf_path)
            try:
                page = doc[page_index]
                mark_items: list[dict] = []
                sources: list[list] = []
                for src in word_sources:
                    if src and src not in sources:
                        sources.append(src)
                highlighted_refs, total_marks, rects = _collect_highlight_rects(
                    page,
                    page_refs,
                    sources,
                    doc=doc,
                    page_index=page_index,
                    tile_zones=tile_zones,
                    mark_items=mark_items,
                )
                if total_marks == 0 and page_refs:
                    if pipeline_deadline is None or time.monotonic() < pipeline_deadline - 12.0:
                        if sum(len(s) for s in sources) < 50:
                            extra = _fullpage_ocr_words(page, page_index=page_index)
                            if extra:
                                sources.append(extra)
                                mark_items.clear()
                                highlighted_refs, total_marks, rects = _collect_highlight_rects(
                                    page,
                                    page_refs,
                                    sources,
                                    doc=doc,
                                    page_index=page_index,
                                    tile_zones=tile_zones,
                                    mark_items=mark_items,
                                )
                plain_img = _render_preview_image_with_highlights(page, [], dpi=144)
                preview_img = _render_preview_image_with_highlights(
                    page, rects, dpi=144, base_image=plain_img
                )
            finally:
                doc.close()

            fname = f"preview_{uuid.uuid4().hex}.jpg"
            out_path = os.path.join(store_dir, fname)
            preview_img.save(out_path, format="JPEG", quality=92)
            plain_fname = fname.replace(".jpg", "_plain.jpg")
            if mark_items:
                plain_img.save(os.path.join(store_dir, plain_fname), format="JPEG", quality=92)
            if not os.path.isfile(out_path):
                continue
            pages_out.append(
                {
                    "page": page_index + 1,
                    "url": f"/api/preview/{fname}",
                    "refs": highlighted_refs,
                    "marks": total_marks,
                    "plain_url": f"/api/preview/{plain_fname}" if mark_items else f"/api/preview/{fname}",
                    "highlights": mark_items,
                }
            )
            log.debug(
                "preview page=%s words=%s marks=%s",
                page_index + 1,
                sum(len(s) for s in word_sources),
                total_marks,
            )

        log.info(
            "preview highlights: %s pages, %s marks total on %s",
            len(pages_out),
            sum(int(p.get("marks") or 0) for p in pages_out),
            pdf_path,
        )
    except Exception as e:
        log.warning("Preview generation failed: %s", e)
    return pages_out


def generate_pdf_preview_with_highlights(pdf_path: str, refs: list[dict]) -> str | None:
    """Превью первого листа (совместимость)."""
    pages = generate_pdf_preview_pages_with_highlights(pdf_path, refs)
    return pages[0]["url"] if pages else None


def extract_normatives_from_document(
    doc: fitz.Document,
    filename: str = "document.pdf",
    *,
    source_path: str | None = None,
    allow_drawing_fallback: bool | None = None,
    pipeline_deadline: float | None = None,
) -> dict[str, Any]:
    """Нормативы: сетка тайлов по листу → OCR (основной путь)."""
    return extract_normatives_document_crops(
        doc,
        filename,
        pipeline_deadline=pipeline_deadline,
        source_path=source_path,
    )


def extract_normatives_from_image_path(
    path: str,
    filename: str | None = None,
    *,
    pipeline_deadline: float | None = None,
) -> dict[str, Any]:
    """Изображение как одностраничный PDF → те же тайлы и OCR."""
    p = Path(path)
    doc = fitz.open(str(p))
    try:
        return extract_normatives_from_document(
            doc,
            filename or p.name,
            pipeline_deadline=pipeline_deadline,
        )
    finally:
        doc.close()


def extract_normatives_pdf_path(
    path: str,
    filename: str | None = None,
    *,
    pipeline_deadline: float | None = None,
) -> dict[str, Any]:
    p = Path(path)
    path_str = str(p.resolve())
    doc = fitz.open(path_str)
    try:
        return extract_normatives_from_document(
            doc,
            filename or p.name,
            source_path=path_str,
            pipeline_deadline=pipeline_deadline,
        )
    finally:
        doc.close()


def normative_refs_to_markdown(
    refs: list[dict[str, str]],
    *,
    filename: str = "",
    pipeline: str = "",
    include_context: bool = False,
    stn_checks: list | None = None,
    include_stn: bool = True,
    rb_provider: str = "stn",
    tnpa_checks: list | None = None,
    include_tnpa: bool | None = None,
    normacs_checks: list | None = None,
    include_normacs: bool = False,
    normacs_error: str = "",
    check_date: date | None = None,
    stn_error: str = "",
    tnpa_error: str = "",
    page_count: int = 0,
    pages_processed: int = 0,
    budget_exhausted: bool = False,
    merged_fragment_count: int = 0,
    source_path: str = "",
    page_normative_refs: list[list[dict]] | None = None,
    page_preview_words: list[list] | None = None,
    preview_pages: list[dict[str, Any]] | None = None,
) -> str:
    rb_label = "Стройпрофи" if rb_provider == "stroiprofi" else "Стройдок"
    rb_label_full = rb_label + " · РБ"
    lines = ["## Нормативные документы (ГОСТ, ОСТ, СТП, РД, СНиП, СН, НРР, ТПР, ТУ, ТКП, СТБ, СП и др.)", ""]
    lines.append('<div class="normative-workspace">')
    lines.append('<div class="normative-workspace-list">')

    meta: list[str] = []
    if filename:
        meta.append(f"<p><strong>Файл:</strong> {escape(filename)}</p>")
    if page_count > 0:
        meta.append(f"<p><strong>Листов:</strong> {page_count}</p>")
    if page_count > 1:
        proc = pages_processed or page_count
        note = f"<strong>Листов в файле:</strong> {page_count}"
        if proc < page_count:
            note += f" · <strong>обработано:</strong> {proc}"
        if budget_exhausted:
            note += " · <em>не все листы успели прочитаться</em>"
        meta.append(f"<p>{note}</p>")
    elif budget_exhausted:
        meta.append("<p><em>Не все участки листа успели прочитаться — список может быть неполным.</em></p>")
    if merged_fragment_count > 0:
        meta.append(
            f"<p><strong>Уникальных обозначений:</strong> {len(refs)} · "
            f"<strong>Объединено неполных повторов:</strong> {merged_fragment_count}</p>"
        )
    if check_date:
        meta.append(
            f"<p><strong>Дата проверки актуальности:</strong> "
            f"{check_date.strftime('%d.%m.%Y')}</p>"
        )
    if meta:
        lines.append('<div class="normative-workspace-meta">')
        lines.extend(meta)
        lines.append("</div>")

    ref_pages = _pages_by_ref(page_normative_refs)
    found_tnpa = 0
    # Явно False — колонка скрыта (поиск ТНПА выключен). Иначе колонка есть.
    show_tnpa = True if include_tnpa is None else bool(include_tnpa)
    show_stn = bool(include_stn)
    show_normacs = bool(include_normacs or normacs_checks)

    if not refs:
        lines.append(
            "*Нормативные ссылки не найдены. Проверьте качество скана или "
            "используйте полный разбор чертежа.*"
        )
        lines.append("")
    else:
        checks_map = {}
        tnpa_checks_map = {}
        normacs_checks_map = {
            str(c.ref if hasattr(c, "ref") else c.get("ref") or ""): c
            for c in (normacs_checks or [])
        }
        from belener.normacs_lookup import designation_key, normacs_query
        for c in (normacs_checks or []):
            nc_kind = str(c.kind if hasattr(c, "kind") else c.get("kind") or "")
            nc_ref = str(c.ref if hasattr(c, "ref") else c.get("ref") or "")
            normacs_checks_map[designation_key(normacs_query(nc_kind, nc_ref))] = c
        tnpa_base = "https://tnpa.by"
        if stn_checks:
            for c in stn_checks:
                try:
                    ref_val = c.ref if hasattr(c, "ref") else c.get("ref")
                    kind_val = c.kind if hasattr(c, "kind") else c.get("kind")
                    checks_map[ref_val] = c
                    from belener.stn_lookup import _norm_code, search_query

                    norm_key = _norm_code(search_query(str(kind_val or ""), str(ref_val or "")))
                    if norm_key:
                        checks_map[norm_key] = c
                except Exception:
                    pass

        if show_tnpa and tnpa_checks:
            try:
                from belener.tnpa_lookup import tnpa_base_url

                tnpa_base = tnpa_base_url().rstrip("/")
            except Exception:
                tnpa_base = "https://tnpa.by"
            for c in tnpa_checks:
                try:
                    ref_val = c.ref if hasattr(c, "ref") else c.get("ref")
                    kind_val = c.kind if hasattr(c, "kind") else c.get("kind")
                    tnpa_checks_map[ref_val] = c
                    from belener.stn_lookup import _norm_code, search_query

                    norm_key = _norm_code(search_query(str(kind_val or ""), str(ref_val or "")))
                    if norm_key:
                        tnpa_checks_map[norm_key] = c
                except Exception:
                    pass

        found_ips = 0
        found_tnpa = 0
        active_count = 0
        found_normacs = 0
        partial_count = 0
        conflict_count = 0

        lines.append('<div class="normative-table-container">')
        lines.append('<table class="normative-table-normacs">' if show_normacs else "<table>")
        if show_normacs:
            titles = ["Обозначение"]
            if show_stn:
                titles.append(rb_label)
            if show_tnpa:
                titles.append("ТНПА")
            if show_stn:
                titles.extend([f"Введен ({rb_label})", f"Отменен ({rb_label})"])
            titles.extend(["NormaCS", "Статус NormaCS (РФ)"])
            lines.append("<thead><tr>" + "".join(f"<th>{title}</th>" for title in titles))
        elif show_tnpa and show_stn:
            lines.append(
                "<colgroup>"
                "<col style=\"width:280px\">"
                "<col style=\"width:120px\">"
                "<col style=\"width:100px\">"
                "<col style=\"width:218px\">"
                "<col style=\"width:218px\">"
                "</colgroup>"
            )
            lines.append("<thead><tr>")
            lines.append(
                f"<th>Обозначение</th><th>{rb_label}</th><th>ТНПА</th>"
                "<th>Введен</th><th>Отменен</th>"
            )
        elif show_stn:
            lines.append(
                "<colgroup>"
                "<col style=\"width:320px\">"
                "<col style=\"width:140px\">"
                "<col style=\"width:240px\">"
                "<col style=\"width:240px\">"
                "</colgroup>"
            )
            lines.append("<thead><tr>")
            lines.append(
                f"<th>Обозначение</th><th>{rb_label}</th>"
                "<th>Введен</th><th>Отменен</th>"
            )
        elif show_tnpa:
            lines.append('<thead><tr><th>Обозначение</th><th>ТНПА</th>')
        else:
            lines.append("<thead><tr><th>Обозначение</th>")
        lines.append("</tr></thead>")
        lines.append("<tbody>")

        for n in refs:
            kind = n.get('kind') or '—'
            ref = n.get('ref') or '—'
            c = checks_map.get(ref)
            if c is None and kind and ref:
                from belener.stn_lookup import _norm_code, search_query

                c = checks_map.get(_norm_code(search_query(kind, ref)))

            c_tnpa = tnpa_checks_map.get(ref)
            if c_tnpa is None and kind and ref:
                from belener.stn_lookup import _norm_code, search_query

                c_tnpa = tnpa_checks_map.get(_norm_code(search_query(kind, ref)))

            ips_link = "—"
            tnpa_link = "—"

            stn_intro = "—"
            stn_cancel = "—"
            tnpa_intro = "—"
            tnpa_cancel = "—"

            row_class = ""

            stn_found = False
            tnpa_found = False
            stn_status_val = ""
            stn_code_val = ""
            tnpa_status_val = ""

            if c:
                raw_stn_found = c.found if hasattr(c, "found") else c.get("found")
                stn_found = raw_stn_found in (True, "1")
                stn_intro = c.intro_date if hasattr(c, "intro_date") else c.get("intro_date") or "—"
                stn_cancel = c.cancel_date if hasattr(c, "cancel_date") else c.get("cancel_date") or "—"
                stn_status_val = c.status if hasattr(c, "status") else c.get("status") or ""
                stn_code_val = c.stn_code if hasattr(c, "stn_code") else c.get("stn_code") or ""
                if stn_found and (c.doc_id if hasattr(c, "doc_id") else c.get("doc_id")):
                    doc_id = c.doc_id if hasattr(c, "doc_id") else c.get("doc_id")
                    if doc_id:
                        source_url = (c.url if hasattr(c, "url") else c.get("url") or "")
                        if rb_provider == "stroiprofi":
                            source_url = source_url or f"https://стройпрофи.бел/tnpa/document/{doc_id}"
                        else:
                            source_url = source_url or f"https://normy.stn.by/ips.php?{doc_id}"
                        ips_link = (f'<a class="stn-link stn-link-stn" '
                                    f'href="{escape(source_url, quote=True)}" target="_blank" '
                                    f'rel="noopener noreferrer">{rb_label}</a>')
                elif stn_status_val == "ошибка проверки" or (
                    str(c.error if hasattr(c, "error") else c.get("error") or "").strip()
                ):
                    err_stn = str(
                        c.error if hasattr(c, "error") else c.get("error") or f"нет связи с {rb_label}"
                    ).replace('"', "'")
                    ips_link = (
                        f'<span class="stn-link stn-link-muted" title="{err_stn}">нет связи</span>'
                    )

            if show_tnpa and c_tnpa:
                tnpa_found = bool(c_tnpa.found) if hasattr(c_tnpa, "found") else str(c_tnpa.get("found")) == "1"
                tnpa_intro = c_tnpa.intro_date if hasattr(c_tnpa, "intro_date") else c_tnpa.get("intro_date") or "—"
                tnpa_cancel = c_tnpa.cancel_date if hasattr(c_tnpa, "cancel_date") else c_tnpa.get("cancel_date") or "—"
                tnpa_status_val = c_tnpa.status if hasattr(c_tnpa, "status") else c_tnpa.get("status") or ""
                if tnpa_found and getattr(c_tnpa, "doc_id", None):
                    doc_id_tnpa = c_tnpa.doc_id if hasattr(c_tnpa, "doc_id") else c_tnpa.get("doc_id")
                    if doc_id_tnpa:
                        tnpa_link = (
                            f'<a class="stn-link stn-link-tnpa" '
                            f'href="{tnpa_base}/#!/DocumentCard/{doc_id_tnpa}" target="_blank">ТНПА</a>'
                        )
                elif tnpa_status_val == "ошибка проверки" or (
                    str(c_tnpa.error if hasattr(c_tnpa, "error") else c_tnpa.get("error") or "").strip()
                ):
                    err_tip = str(
                        c_tnpa.error if hasattr(c_tnpa, "error") else c_tnpa.get("error") or "нет связи"
                    ).replace('"', "'")
                    tnpa_link = (
                        f'<span class="stn-link stn-link-muted" title="{err_tip}">нет связи</span>'
                    )
                elif (tnpa_status_val or "").startswith("пропущено"):
                    tnpa_link = '<span class="stn-link stn-link-muted" title="бюджет времени">пропуск</span>'

            if stn_found:
                found_ips += 1
            if tnpa_found:
                found_tnpa += 1

            def _pick_date(primary: str, secondary: str) -> str:
                if primary and primary != "—":
                    return primary
                if secondary and secondary != "—":
                    return secondary
                return "—"

            intro = _pick_date(stn_intro, tnpa_intro if show_tnpa else "—")
            cancel = _pick_date(stn_cancel, tnpa_cancel if show_tnpa else "—")

            stn_active = stn_found and stn_status_val == "актуален"
            tnpa_active = show_tnpa and tnpa_found and tnpa_status_val == "актуален"
            nc = normacs_checks_map.get(str(ref)) if show_normacs else None
            if show_normacs and nc is None:
                nc = normacs_checks_map.get(designation_key(normacs_query(str(kind), str(ref))))

            def nc_value(key, default=""):
                return (getattr(nc, key, default) if hasattr(nc, key)
                        else nc.get(key, default) if nc else default)

            def stn_value(key):
                return str(getattr(c, key, "") if hasattr(c, key) else c.get(key, "") if c else "")

            nc_found = nc_value("found") in (True, "1")
            nc_id = str(nc_value("status_id"))
            stn_result = stn_state(enabled=show_stn, found=stn_found,
                                   status=stn_status_val, error=stn_value("error"))
            nc_result = normacs_state(enabled=show_normacs, found=nc_found,
                                      status_id=nc_id, status=str(nc_value("status")),
                                      error=str(nc_value("error")))
            verdict = combined_verdict(stn_result, nc_result, rb_label=rb_label_full)
            if show_tnpa:
                # Старые отчёты ТНПА сохраняют собственную схему.
                if stn_active or tnpa_active:
                    row_class = ' class="row-active"'
                    active_count += 1
                elif show_stn or show_tnpa:
                    row_class = ' class="row-canceled"'
            else:
                row_class = {
                    "active": ' class="row-active"',
                    "inactive": ' class="row-canceled"',
                    "partial": ' class="row-warn"',
                    "upcoming": ' class="row-warn"',
                }.get(verdict.state, "")
                if verdict.state == "unknown" and verdict.tone == "warn":
                    row_class = ' class="row-warn"'
                active_count += verdict.state == "active"
                partial_count += verdict.state == "partial"
                conflict_count += verdict.conflict

            pages_attr = ""
            pages_for_ref = ref_pages.get(str(ref), [])
            if pages_for_ref:
                pages_csv = ",".join(str(p) for p in pages_for_ref)
                title = f' title="Лист {pages_csv}"'
                if not row_class:
                    row_class = ' class="row-jump"'
                elif 'class="' in row_class:
                    row_class = row_class.replace('class="', 'class="row-jump ', 1)
                pages_attr = f' data-preview-page="{pages_for_ref[0]}" data-preview-pages="{pages_csv}"{title}'

            combined_attrs = (
                f' data-stn-state="{stn_result}"'
                f' data-normacs-state="{nc_result}"'
                f' data-overall-label="{escape(verdict.label, quote=True)}"'
                f' data-overall-tone="{verdict.tone}"'
                f' data-overall-note="{escape(verdict.note, quote=True)}"'
                f' data-overall-conflict="{int(verdict.conflict)}"'
            ) if not show_tnpa else ""
            review_attrs = (
                f' data-normative-ref="{escape(str(ref), quote=True)}"'
                f' data-stn-status="{escape(stn_status_val, quote=True)}"'
                f' data-stn-code="{escape(stn_code_val, quote=True)}"'
                f' data-stn-found="{int(stn_found)}"'
                f' data-stn-enabled="{int(show_stn)}"'
                f' data-rb-provider="{escape(rb_provider, quote=True)}"'
                f'{combined_attrs}'
                f' data-normative-title="{escape(stn_value("stn_name"), quote=True)}"'
                f' data-stn-error="{escape(stn_value("error"), quote=True)}"'
            )
            lines.append(f"<tr{row_class}{pages_attr}{review_attrs}>")
            ref_cell = escape(str(ref))
            stn_cell = ips_link
            if show_normacs and not show_tnpa:
                ref_cell += (f'<br><small class="normative-row-verdict">'
                             f'{escape(verdict.label)}</small>')
                if verdict.conflict:
                    ref_cell += '<br><small class="normative-row-conflict">⚠ Реестры расходятся</small>'
                if show_stn:
                    stn_short = {"active": "Действует", "inactive": "Не действует",
                                 "upcoming": "Не введён", "missing": "Не найден",
                                 "error": "Ошибка", "unknown": "Не определён"}.get(stn_result, "")
                    if stn_result == "unknown" and stn_status_val.startswith("неполное обозначение"):
                        stn_short = "Уточните номер и год"
                    stn_cell += f'<br><small title="{escape(stn_status_val, quote=True)}">{stn_short}</small>'
                    if rb_provider == "stroiprofi" and stn_found and stn_code_val:
                        stn_cell += f'<br><small>Последняя редакция: {escape(stn_code_val)}</small>'
            if show_stn and show_tnpa:
                lines.append(
                    f"<td>{escape(str(ref))}</td><td>{ips_link}</td><td>{tnpa_link}</td>"
                    f"<td>{intro}</td><td>{cancel}</td>"
                )
            elif show_stn:
                lines.append(
                    f"<td>{ref_cell}</td><td class=\"stn-source-cell stn-state-{stn_result}\">{stn_cell}</td>"
                    f"<td>{intro}</td><td>{cancel}</td>"
                )
            elif show_tnpa:
                lines.append(f"<td>{escape(str(ref))}</td><td>{tnpa_link}</td>")
            else:
                lines.append(f"<td>{ref_cell}</td>")
            if show_normacs:
                nc_link = "—"
                nc_status = str(nc_value("status") or "Не проверен")
                if nc_found:
                    found_normacs += 1
                    nc_url = str(nc_value("url"))
                    if nc_url.startswith(("http://", "https://")):
                        nc_link = (f'<a class="stn-link normacs-link" href="{escape(nc_url, quote=True)}" '
                                   'target="_blank" rel="noopener noreferrer">NormaCS</a>')
                nc_hint = str(nc_value("error") or nc_value("status_hint") or nc_status)
                history = nc_value("status_history") or []
                if history:
                    nc_hint += "\n" + "\n".join(str(x) for x in history)
                nc_label = escape(nc_status)
                if nc_value("error"):
                    # Причина видна в таблице и попадает в PDF, а не только
                    # в title, который недоступен на телефонах/в экспорте.
                    nc_label += f' <br><small class="normacs-error-detail">{escape(str(nc_value("error")))}</small>'
                if nc_value("intro_date"):
                    nc_label += f' <br><small>Введен: {escape(str(nc_value("intro_date")))}</small>'
                nc_id = str(nc_value("status_id"))
                nc_class = f"normacs-status-{nc_id}" if nc_id in ("0", "1", "2", "4", "8") else ""
                lines.append(f'<td>{nc_link}</td><td class="normacs-status {nc_class}" '
                             f'title="{escape(nc_hint, quote=True)}">{nc_label}</td>')
            lines.append("</tr>")

        lines.append("</tbody></table>")
        lines.append("</div>")
        lines.append("")

        if show_normacs:
            lines.append(f'<p>Найдено в NormaCS: {found_normacs} из {len(refs)}. '
                         'Статусы NormaCS относятся к РФ и показаны по текущей карточке базы. '
                         'История и условия применения доступны при наведении на статус и в карточке.</p>')
            if show_stn:
                lines.append('<p>Зелёный итог означает «действует хотя бы по одному реестру», '
                             f'но не подтверждает применимость в другой стране. {rb_label} относится к РБ '
                             'и проверяется на выбранную дату, NormaCS показывает текущий статус РФ. '
                             'Расхождения отмечены отдельно; «не найден» и ошибка не означают отмену.</p>')
            failed_checks = [c for c in (normacs_checks or [])
                             if (getattr(c, "error", "") if hasattr(c, "error") else c.get("error"))]
            if failed_checks:
                reasons = list(dict.fromkeys(str(getattr(c, "error", "") if hasattr(c, "error")
                                                 else c.get("error")) for c in failed_checks))
                lines.append(f'<p class="normacs-warning">⚠ NormaCS: не удалось проверить '
                             f'{len(failed_checks)} обозначений. Это не означает отсутствие или отмену документов. '
                             f'{escape("; ".join(reasons[:3]))}</p>')
        lines.append(
            '<button type="button" class="btn btn-primary btn-sm normative-table-pdf-btn" '
            'style="margin-top:10px" '
            'onclick="downloadNormativeTablePdf(this); return false;">'
            "Скачать таблицу в PDF</button>"
        )
        lines.append("")

        if show_stn and show_normacs and not show_tnpa:
            lines.append(
                f'<p class="normative-table-summary"><em>Всего в документе: {len(refs)}; '
                f'найдено в {rb_label}: {found_ips}; найдено в NormaCS: {found_normacs};<br>'
                f'действует хотя бы в одном: {active_count}; частично: {partial_count}; '
                f'расхождения: {conflict_count}</em></p>'
            )
        elif not show_stn and show_normacs and not show_tnpa:
            lines.append(
                f'<p class="normative-table-summary"><em>Всего в документе: {len(refs)}; '
                f'найдено в NormaCS: {found_normacs};<br>действует: {active_count}</em></p>'
            )
        elif show_tnpa and show_stn:
            lines.append(
                f'<p class="normative-table-summary"><em>Всего в документе: {len(refs)}; найдено в {rb_label}: {found_ips}; '
                f"найдено в ТНПА: {found_tnpa};<br>актуально: {active_count}</em></p>"
            )
        elif show_tnpa:
            lines.append(
                f'<p class="normative-table-summary"><em>Всего в документе: {len(refs)}; '
                f'найдено в ТНПА: {found_tnpa};<br>актуально: {active_count}</em></p>'
            )
        else:
            lines.append(
                f'<p class="normative-table-summary"><em>Всего в документе: {len(refs)}; найдено в {rb_label}: {found_ips};'
                f"<br>актуально: {active_count}</em></p>"
            )
        lines.append("")

    stn_error = (stn_error or "").strip() if show_stn else ""
    if rb_provider == "stroiprofi" and stn_checks and not stn_error:
        failures = [str(c.error if hasattr(c, "error") else c.get("error") or "")
                    for c in stn_checks]
        if failures and all(failures):
            stn_error = f"Стройпрофи: не удалось проверить документы ({failures[0]})"
    elif stn_checks and not stn_error:
        login_like = sum(
            1
            for c in stn_checks
            if "IPS" in str(c.status if hasattr(c, "status") else c.get("status") or "")
            or "вход" in str(c.status if hasattr(c, "status") else c.get("status") or "").casefold()
        )
        if login_like == len(stn_checks) and login_like > 0:
            first = stn_checks[0]
            stn_error = first.status if hasattr(first, "status") else str(first.get("status") or "")
        skipped = sum(
            1
            for c in stn_checks
            if str(c.status if hasattr(c, "status") else c.get("status") or "").startswith("пропущено")
        )
        if skipped == len(stn_checks) and skipped > 0:
            stn_error = "Проверка ИПС не выполнена — не хватило времени после OCR."
        if not stn_error:
            all_stn_miss = all(
                not (c.found if hasattr(c, "found") else c.get("found"))
                for c in stn_checks
            )
            if all_stn_miss:
                statuses = [
                    str(c.status if hasattr(c, "status") else c.get("status") or "").strip()
                    for c in stn_checks
                ]
                if statuses and all("IPS" in s or "вход" in s.casefold() for s in statuses if s):
                    stn_error = statuses[0]
                elif (
                    statuses
                    and len(set(statuses)) == 1
                    and statuses[0]
                    and statuses[0] != "нет в ИПС"
                ):
                    stn_error = statuses[0]
                elif statuses and all(s == "нет в ИПС" for s in statuses):
                    stn_error = (
                        f"{rb_label}: документы не найдены в каталоге"
                    )
    if stn_error:
        lines.extend(["", f"<p><em>⚠ {stn_error}</em></p>", ""])

    if normacs_error:
        lines.extend(["", f"<p><em>⚠ {escape(normacs_error)}</em></p>", ""])

    tnpa_error = (tnpa_error or "").strip()
    if show_tnpa and not tnpa_error and tnpa_checks:
        failed = [
            c
            for c in tnpa_checks
            if not (c.found if hasattr(c, "found") else c.get("found"))
        ]
        if failed and len(failed) == len(tnpa_checks):
            errs = [
                str(c.error if hasattr(c, "error") else c.get("error") or "").strip()
                for c in failed
            ]
            errs = [e for e in errs if e]
            if errs and len(set(errs)) == 1:
                tnpa_error = errs[0]
    if show_tnpa and tnpa_error:
        lines.extend(["", f"<p><em>⚠ {tnpa_error}</em></p>", ""])

    lines.append("</div>")  # workspace-list

    if preview_pages is None and source_path and os.path.isfile(source_path):
        preview_pages = generate_pdf_preview_pages_with_highlights(
            source_path,
            refs,
            page_normative_refs=page_normative_refs,
            page_preview_words=page_preview_words,
        )

    lines.append('<div class="normative-workspace-preview">')
    if preview_pages:
        usable = [e for e in preview_pages if str(e.get("url") or "").strip()]
        group_id = f"npg-{uuid.uuid4().hex[:8]}"
        if len(usable) == 1:
            lines.append('<h3 class="normative-preview-heading">Предпросмотр листа</h3>')
        else:
            lines.append(
                f'<h3 class="normative-preview-heading">Предпросмотр '
                f'({len(usable)} лист.)</h3>'
            )
        lines.append(f'<div class="normative-preview-shell" data-preview-group="{group_id}">')
        if len(usable) > 1:
            first_page = int(usable[0].get("page") or 1)
            lines.append(
                f'<div class="pdf-preview-tools normative-preview-nav">'
                f'<div class="preview-page-nav">'
                f'<button type="button" class="preview-page-btn" data-group="{group_id}" data-action="prev" aria-label="Предыдущий лист">‹</button>'
                f'<span class="preview-page-label" data-group="{group_id}">1 / {len(usable)} · лист {first_page}</span>'
                f'<button type="button" class="preview-page-btn" data-group="{group_id}" data-action="next" aria-label="Следующий лист">›</button>'
                f'</div></div>'
            )
        for i, entry in enumerate(usable):
            page_no = int(entry.get("page") or 0)
            preview_url = str(entry.get("url") or "")
            plain_url = str(entry.get("plain_url") or preview_url)
            highlights = list(entry.get("highlights") or [])
            preview_id = f"preview-{uuid.uuid4().hex[:8]}"
            active = " is-active" if i == 0 else ""
            hidden = "" if i == 0 else " hidden"
            lines.append(
                f'<div class="normative-preview-page{active}" data-group="{group_id}" '
                f'data-page="{page_no}" data-index="{i}"{hidden}>'
            )
            lines.append(
                f'<div class="pdf-preview-tools">'
                f'<a class="stn-link" href="{preview_url}" target="_blank">Открыть лист {page_no}</a>'
                f'<div class="preview-zoom-buttons">'
                f'<button type="button" class="preview-zoom-btn" data-target="{preview_id}" data-action="out" aria-label="Уменьшить">−</button>'
                f'<button type="button" class="preview-zoom-btn" data-target="{preview_id}" data-action="reset" aria-label="100%">100&#37;</button>'
                f'<button type="button" class="preview-zoom-btn" data-target="{preview_id}" data-action="in" aria-label="Увеличить">+</button>'
                f'</div></div>'
            )
            marks_attr = escape(json.dumps(highlights, ensure_ascii=False), quote=True)
            lines.append(
                f'<div class="pdf-preview-container" data-review-marks="{marks_attr}" '
                f'data-interactive-highlights="{int(bool(highlights and entry.get("plain_url")))}">'
                f'<img id="{preview_id}" src="{escape(plain_url, quote=True)}" '
                f'alt="Предпросмотр листа {page_no}" class="pdf-preview-img" '
                f'loading="{"eager" if i == 0 else "lazy"}" decoding="async" '
                f'data-highlight-url="{escape(preview_url, quote=True)}" data-scale="1"></div>'
            )
            lines.append("</div>")
        lines.append("</div>")  # shell
    else:
        lines.append('<h3 class="normative-preview-heading">Предпросмотр листа</h3>')
        lines.append(
            '<p class="normative-preview-empty">Превью с выделениями пока недоступно для этого файла.</p>'
        )
    lines.append("</div>")  # workspace-preview
    lines.append("</div>")  # workspace
    lines.append("")

    return "\n".join(lines)


def normative_result_to_markdown(
    result: dict[str, Any],
    *,
    include_context: bool = False,
    stn_checks: list | None = None,
    include_stn: bool | None = None,
    tnpa_checks: list | None = None,
    include_tnpa: bool | None = None,
    check_date: date | None = None,
    source_path: str = "",
    preview_pages: list[dict[str, Any]] | None = None,
) -> str:
    checks = stn_checks
    if checks is None:
        checks = result.get("stn_checks")
    tnpa = tnpa_checks
    if tnpa is None:
        tnpa = result.get("tnpa_checks")
    show_tnpa = include_tnpa
    if show_tnpa is None:
        show_tnpa = True
    show_stn = bool(result.get("stn_enabled", True)) if include_stn is None else bool(include_stn)
    return normative_refs_to_markdown(
        list(result.get("normative_refs") or []),
        filename=str(result.get("filename") or ""),
        pipeline=str(result.get("pipeline") or ""),
        include_context=include_context,
        stn_checks=checks,
        include_stn=show_stn,
        rb_provider=str(result.get("rb_provider") or "stn"),
        tnpa_checks=tnpa if show_tnpa else None,
        include_tnpa=bool(show_tnpa),
        normacs_checks=result.get("normacs_checks"),
        include_normacs=bool(result.get("normacs_requested", result.get("normacs_enabled"))),
        normacs_error=str(result.get("normacs_error") or ""),
        check_date=check_date,
        stn_error=str(result.get("stn_error") or "") if show_stn else "",
        tnpa_error=str(result.get("tnpa_error") or "") if show_tnpa else "",
        page_count=int(result.get("page_count") or 0),
        pages_processed=int(result.get("pages_processed") or 0),
        budget_exhausted=bool(result.get("budget_exhausted")),
        merged_fragment_count=int(result.get("merged_fragment_count") or 0),
        source_path=source_path,
        page_normative_refs=list(result.get("page_normative_refs") or []),
        page_preview_words=list(result.get("page_preview_words") or []),
        preview_pages=preview_pages,
    )
