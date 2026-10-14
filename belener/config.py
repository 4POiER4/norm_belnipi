"""Настройки OCR зон."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


def ensure_upload_temp_dir() -> str:
    """Каталог для загруженных файлов — не переполненный том /ssd/tmp."""
    explicit = (os.environ.get("BELENER_UPLOAD_TMP") or "").strip()
    if explicit:
        path = Path(explicit)
    else:
        data_tmp = Path("/app/data/tmp")
        if Path("/app/data").exists():
            path = data_tmp
        else:
            path = Path(os.environ.get("TMPDIR") or tempfile.gettempdir())
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def upload_temp_dir() -> str:
    return ensure_upload_temp_dir()


def preview_store_dir() -> str:
    """Каталог превью листов — volume data/, переживает рестарт контейнера."""
    explicit = (os.environ.get("BELENER_PREVIEW_DIR") or "").strip()
    if explicit:
        path = Path(explicit)
    elif Path("/app/data").exists():
        path = Path("/app/data/previews")
    else:
        path = Path(ensure_upload_temp_dir()) / "previews"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def preview_max_age_days() -> int:
    try:
        return max(1, min(int(os.environ.get("BELENER_PREVIEW_MAX_AGE_DAYS", "14").strip()), 90))
    except ValueError:
        return 14


def prune_preview_store(*, max_age_days: int | None = None) -> int:
    """Удаляет старые JPEG превью. Возвращает число удалённых файлов."""
    import time

    age_days = preview_max_age_days() if max_age_days is None else max(1, int(max_age_days))
    cutoff = time.time() - age_days * 86400
    removed = 0
    try:
        root = Path(preview_store_dir())
        for path in root.glob("preview_*.jpg"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
    except OSError:
        return removed
    return removed


def extract_mode() -> str:
    """accuracy — медленно (multiview, CV-ячейки); fast — зоны + OCR, рекомендуется в вебе."""
    return (os.environ.get("PDF_EXTRACT_MODE") or "fast").strip().lower()


def accuracy_mode() -> bool:
    return extract_mode() in ("accuracy", "max", "high", "1", "true")


def ocr_lang() -> str:
    # rus — меньше «cmpoumenbcmba»; eng добавляйте только если нужны латинские метки
    return (os.environ.get("PDF_OCR_LANG") or "rus").strip() or "rus"


def ocr_psm_for_zone(zone: str) -> int:
    """Tesseract page segmentation mode по типу зоны."""
    key = zone.lower()
    if key in ("stamp_frame", "stamp_block"):
        default = "6"
    elif key == "stamp_cell":
        default = "7"
    elif key.startswith("stamp"):
        default = "4"
    elif key in (
        "explication",
        "legend",
        "right_column",
        "tables_block",
        "spec_right",
        "spec_left",
        "legend_table",
    ):
        default = "4"
    elif key in ("body", "sheet_notes", "notes"):
        default = "6"
    else:
        default = "6"
    env_key = f"PDF_OCR_PSM_{key.upper()}" if key else "PDF_OCR_PSM"
    raw = (os.environ.get(env_key) or os.environ.get("PDF_OCR_PSM") or default).strip()
    try:
        return max(3, min(int(raw), 13))
    except ValueError:
        return int(default)


def stamp_frac() -> float:
    try:
        v = float(os.environ.get("PDF_STAMP_FRAC", "0.30").strip())
        return max(0.14, min(v, 0.42))
    except ValueError:
        return 0.30


def right_column_frac() -> float:
    try:
        v = float(os.environ.get("PDF_RIGHT_FRAC", "0.44").strip())
        return max(0.32, min(v, 0.52))
    except ValueError:
        return 0.44


def expl_split_frac() -> float:
    try:
        v = float(os.environ.get("PDF_EXPL_FRAC", "0.34").strip())
        return max(0.22, min(v, 0.48))
    except ValueError:
        return 0.34


def stamp_dpi() -> int:
    try:
        return max(560, min(int(os.environ.get("PDF_STAMP_DPI", "720").strip()), 1200))
    except ValueError:
        return 720


def stamp_grid_enabled() -> bool:
    """OCR штампа по ячейкам сетки ГОСТ (fallback если блок OCR «грязный»)."""
    raw = os.environ.get("PDF_STAMP_GRID")
    if raw is not None and str(raw).strip():
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    return accuracy_mode()


def stamp_block_dpi() -> int:
    default = "480" if accuracy_mode() else "380"
    try:
        return max(280, min(int(os.environ.get("PDF_STAMP_BLOCK_DPI", default).strip()), 600))
    except ValueError:
        return int(default)


def stamp_block_width_frac() -> float:
    try:
        v = float(os.environ.get("PDF_STAMP_BLOCK_W", "0.52").strip())
        return max(0.38, min(v, 0.62))
    except ValueError:
        return 0.52


def stamp_block_height_frac() -> float:
    try:
        v = float(os.environ.get("PDF_STAMP_BLOCK_H", "0.40").strip())
        return max(0.28, min(v, 0.48))
    except ValueError:
        return 0.40


def table_dpi() -> int:
    default = "560" if accuracy_mode() else "400"
    try:
        return max(280, min(int(os.environ.get("PDF_TABLE_DPI", default).strip()), 720))
    except ValueError:
        return int(default)


def ocr_fitz_fallback() -> bool:
    """Fitz OCR fallback очень медленный на CPU — по умолчанию выкл."""
    return (os.environ.get("PDF_OCR_FITZ_FALLBACK") or "0").strip().lower() in ("1", "true", "yes", "on")


def local_only_mode() -> bool:
    """Строго локально: без vision/LLM-отчёта (чертежи не уходят в облако)."""
    return (os.environ.get("PDF_LOCAL_ONLY") or "0").strip().lower() in ("1", "true", "yes", "on")


def ocr_engine() -> str:
    """tesseract | surya | deepseek | paddle | auto (surya → deepseek → tesseract)."""
    raw = (os.environ.get("PDF_OCR_ENGINE") or "tesseract").strip().casefold()
    if raw in ("deepseek", "auto", "tesseract", "surya", "paddle"):
        return raw
    return "tesseract"


def remote_ocr_url_configured() -> bool:
    """Есть ли внешний OCR-сервис (Surya / DeepSeek / Paddle)."""
    if (os.environ.get("SURYA_OCR_URL") or "").strip():
        return True
    if (os.environ.get("DEEPSEEK_OCR_URL") or "").strip():
        return True
    if paddle_ocr_url():
        return True
    return False


_REMOTE_GPU_CACHE: tuple[float, bool] | None = None


def remote_ocr_is_gpu(*, ttl_sec: float = 60.0) -> bool:
    """Surya/Paddle на CUDA (VRAM) — кэш health, чтобы не дёргать на каждый тайл."""
    global _REMOTE_GPU_CACHE
    import time

    now = time.monotonic()
    if _REMOTE_GPU_CACHE is not None and now - _REMOTE_GPU_CACHE[0] < ttl_sec:
        return _REMOTE_GPU_CACHE[1]

    gpu = False
    try:
        from belener.ocr_http import health_get_json

        surya = (os.environ.get("SURYA_OCR_URL") or "").strip()
        if surya:
            info = health_get_json(surya, timeout=2.5) or {}
            device = str(info.get("device") or "").casefold()
            g = info.get("gpu")
            if device.startswith("cuda") or (isinstance(g, dict) and g.get("cuda")):
                gpu = True
        if not gpu and paddle_ocr_url():
            info = health_get_json(paddle_ocr_url(), timeout=2.5) or {}
            if info.get("gpu") is True or str(info.get("device") or "").casefold().startswith("cuda"):
                gpu = True
    except Exception:
        gpu = False

    _REMOTE_GPU_CACHE = (now, gpu)
    return gpu


def tile_ocr_offload_remote() -> bool:
    """
    Offload тайлов с web/CPU на OCR-сервис (Surya/Paddle).

    - PDF_TILE_OCR_OFFLOAD=1/0 — жёстко
    - auto (по умолчанию): GPU-сервис → да; engine=surya/paddle/… → да;
      engine=tesseract + только CPU-Surya → нет (иначе медленнее и грузит CPU зря)
    """
    raw = (os.environ.get("PDF_TILE_OCR_OFFLOAD") or "auto").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return remote_ocr_url_configured()
    # auto
    eng = ocr_engine()
    if eng in ("surya", "deepseek", "paddle"):
        return remote_ocr_url_configured()
    if eng == "auto":
        return remote_ocr_url_configured()
    # tesseract: только если удалённый OCR реально на GPU (как у Владика для ИИ)
    return remote_ocr_url_configured() and remote_ocr_is_gpu()


def paddle_ocr_url() -> str:
    return (os.environ.get("PADDLE_OCR_URL") or "").strip().rstrip("/")


def paddle_ocr_zones_enabled() -> bool:
    """Paddle HTTP только для spec_* / stamp_* (остальное — Tesseract/Surya)."""
    if not paddle_ocr_url():
        return False
    return (os.environ.get("PDF_OCR_PADDLE_ZONES") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def yolo_zones_enabled() -> bool:
    return (os.environ.get("PDF_YOLO_ZONES") or "0").strip().lower() in ("1", "true", "yes", "on")


def yolo_zones_model_path() -> str:
    raw = (os.environ.get("PDF_YOLO_MODEL") or "").strip()
    if raw:
        return raw
    for p in (
        Path("/app/data/training/yolo_zones/runs/train/weights/best.pt"),
        Path("data/training/yolo_zones/runs/train/weights/best.pt"),
        Path("/app/data/training/yolo_zones/runs/detect/train/weights/best.pt"),
        Path("data/training/yolo_zones/runs/detect/train/weights/best.pt"),
    ):
        if p.is_file():
            return str(p)
    return ""


def yolo_zones_conf() -> float:
    try:
        return max(0.1, min(float(os.environ.get("PDF_YOLO_CONF", "0.25").strip()), 0.95))
    except ValueError:
        return 0.25


def ocr_fallback_tesseract() -> bool:
    """При surya/deepseek — дозаполнение Tesseract, если сервис не ответил."""
    return (os.environ.get("PDF_OCR_FALLBACK_TESS") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def ocr_min_rect_pt() -> float:
    try:
        return max(24.0, min(float(os.environ.get("PDF_OCR_MIN_RECT_PT", "48").strip()), 200.0))
    except ValueError:
        return 48.0


def ollama_host() -> str:
    return (os.environ.get("OLLAMA_HOST") or "http://localhost:11434").strip()


def discover_zones_enabled() -> bool:
    """Полный перебор зон — медленно (~10+ мин). По умолчанию выкл, геометрия build_zones."""
    return (os.environ.get("PDF_DISCOVER_ZONES") or "0").strip().lower() in ("1", "true", "yes", "on")


def discover_zones_fast() -> bool:
    """Уточнение зон по якорям OCR (штамп, экспликация) — быстрее полного discover."""
    default = "1" if accuracy_mode() else "0"
    return (os.environ.get("PDF_DISCOVER_ZONES_FAST") or default).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def table_search_dpi() -> int:
    try:
        return max(200, min(int(os.environ.get("PDF_TABLE_SEARCH_DPI", "300").strip()), 480))
    except ValueError:
        return 300


def stamp_llm_model() -> str:
    return (os.environ.get("PDF_STAMP_LLM_MODEL") or os.environ.get("MODEL_DEFAULT") or "gemma3:4b").strip()


def model_drawing() -> str:
    """Текстовая модель: ответы по отчёту чертежа (после зонного OCR)."""
    return (os.environ.get("MODEL_DRAWING") or "").strip()


def model_scan() -> str:
    """Текстовая модель: ответы по OCR обычного скана (не САПР-чертёж)."""
    return (os.environ.get("MODEL_SCAN") or os.environ.get("MODEL_SCANNED_PDF") or "").strip()


def scan_dpi() -> int:
    try:
        return max(150, min(int(os.environ.get("PDF_SCAN_DPI", "300").strip()), 400))
    except ValueError:
        return 300


def scan_as_drawing() -> bool:
    """Скан инженерного листа → зонный OCR/vision (штамп, таблицы)."""
    return (os.environ.get("PDF_SCAN_AS_DRAWING") or "1").strip().lower() in ("1", "true", "yes", "on")


def drawing_aspect_min() -> float:
    """Мин. ширина/высота для зон штампа и таблиц (сканы А3×2, А4×3 ≈ 1.35–1.5)."""
    try:
        return max(1.2, min(float(os.environ.get("PDF_DRAWING_ASPECT_MIN", "1.35").strip()), 2.5))
    except ValueError:
        return 1.35


def drawing_page_min_pt() -> float:
    """Мин. размер стороны листа (pt) для скана-чертежа без текстового слоя."""
    try:
        return max(400.0, min(float(os.environ.get("PDF_DRAWING_PAGE_MIN_PT", "750").strip()), 5000.0))
    except ValueError:
        return 750.0


def vision_scan_first() -> bool:
    """Скан: сразу vision без OCR (медленно). По умолчанию выкл — сначала Tesseract, vision дозаполняет."""
    return (os.environ.get("PDF_SCAN_VISION_FIRST") or "0").strip().lower() in ("1", "true", "yes", "on")


def vision_postprocess() -> bool:
    """Vision только после OCR (штамп/таблицы/текст листа), не в середине пайплайна."""
    default = "1" if accuracy_mode() else "0"
    return (os.environ.get("PDF_VISION_POST") or default).strip().lower() in ("1", "true", "yes", "on")


def sheet_text_enabled() -> bool:
    """Искать и выводить текст вне таблиц и штампа."""
    return (os.environ.get("PDF_SHEET_TEXT") or "1").strip().lower() in ("1", "true", "yes", "on")


def sheet_text_vision_always_scan() -> bool:
    """На сканах: vision для текста вне таблиц, если OCR слабый."""
    default = "1" if accuracy_mode() else "0"
    return (os.environ.get("PDF_SHEET_TEXT_VISION") or default).strip().lower() in ("1", "true", "yes", "on")


def vision_combined() -> bool:
    """Штамп + таблицы одним запросом к VL (≈2× быстрее, чем два подряд)."""
    return (os.environ.get("PDF_VISION_COMBINED") or "1").strip().lower() in ("1", "true", "yes", "on")


def layout_vision_enabled() -> bool:
    """Cropped vision per detected block. Disabled by default for CPU/low-RAM PCs."""
    return (os.environ.get("PDF_LAYOUT_VISION") or "0").strip().lower() in ("1", "true", "yes", "on")


def stamp_universal_enabled() -> bool:
    """Штамп только из рамки (vision), подписи полей как на чертеже — без шаблона ГОСТ."""
    default = "1" if accuracy_mode() else "0"
    return (os.environ.get("PDF_STAMP_UNIVERSAL") or default).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def report_stamp_frame_only() -> bool:
    """В отчёт — только рамка (без таблиц). По умолчанию выкл.: нужны таблицы и ТТ с листа."""
    return (os.environ.get("PDF_REPORT_STAMP_ONLY") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def blueprint_extract_enabled() -> bool:
    """Алгоритм engineering-drawing-extractor: отделение таблиц от поля чертежа."""
    default = "1" if accuracy_mode() else "0"
    return (os.environ.get("PDF_BLUEPRINT_EXTRACT") or default).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def cv_tables_enabled() -> bool:
    """OpenCV: отделение таблиц от чертежа (локально, без облака)."""
    return (os.environ.get("PDF_CV_TABLES") or "1").strip().lower() in ("1", "true", "yes", "on")


def cv_tables_always() -> bool:
    """В режиме accuracy — CV на каждом скане, не только при слабом OCR."""
    if accuracy_mode():
        return (os.environ.get("PDF_CV_TABLES_ALWAYS") or "1").strip().lower() in ("1", "true", "yes", "on")
    return (os.environ.get("PDF_CV_TABLES_ALWAYS") or "0").strip().lower() in ("1", "true", "yes", "on")


def cv_cells_enabled() -> bool:
    """OCR по ячейкам внутри каждой таблицы (точнее, медленнее)."""
    if unified_sheet_ocr_enabled() and not accuracy_mode():
        default = "0"
    else:
        default = "1" if accuracy_mode() else "0"
    return (os.environ.get("PDF_CV_CELLS") or default).strip().lower() in ("1", "true", "yes", "on")


def cv_tables_dpi() -> int:
    default = "420" if accuracy_mode() else "360"
    try:
        cap = 520 if accuracy_mode() else 480
        return max(280, min(int(os.environ.get("PDF_CV_TABLES_DPI", default).strip()), cap))
    except ValueError:
        return int(default)


def edocr_enabled() -> bool:
    return bool(edocr_url())


def edocr_url() -> str:
    return (os.environ.get("EDOCR_URL") or "").strip().rstrip("/")


def edocr_timeout_sec() -> int:
    try:
        return max(30, min(int(os.environ.get("EDOCR_TIMEOUT", "300").strip()), 900))
    except ValueError:
        return 300


def layout_ocr_enabled() -> bool:
    """Доп. OCR по layout-блокам. По умолчанию выкл — достаточно геометрических зон."""
    return (os.environ.get("PDF_LAYOUT_OCR") or "0").strip().lower() in ("1", "true", "yes", "on")


def vision_timeout_sec() -> int:
    """Таймаут одного vision-запроса к Ollama (штамп/таблицы). В accuracy — выше по умолчанию."""
    cap = 900 if accuracy_mode() else 300
    default = 300 if accuracy_mode() else 75
    raw = (os.environ.get("PDF_VISION_TIMEOUT") or "").strip()
    try:
        v = int(raw) if raw else default
    except ValueError:
        v = default
    return max(30, min(v, cap))


def vision_layout_max_blocks() -> int:
    try:
        return max(1, min(int(os.environ.get("PDF_VISION_LAYOUT_MAX_BLOCKS", "2").strip()), 5))
    except ValueError:
        return 2


def vision_num_predict() -> int:
    try:
        return max(256, min(int(os.environ.get("PDF_VISION_NUM_PREDICT", "900").strip()), 3000))
    except ValueError:
        return 900


def vision_stamp_dpi() -> int:
    try:
        return max(240, min(int(os.environ.get("PDF_VISION_STAMP_DPI", "384").strip()), 600))
    except ValueError:
        return 384


def vision_table_dpi() -> int:
    try:
        return max(280, min(int(os.environ.get("PDF_VISION_TABLE_DPI", "480").strip()), 720))
    except ValueError:
        return 480


def vision_stamp_max_side() -> int:
    try:
        return max(640, min(int(os.environ.get("PDF_VISION_STAMP_MAX_SIDE", "1024").strip()), 1600))
    except ValueError:
        return 1024


def vision_table_max_side() -> int:
    try:
        return max(960, min(int(os.environ.get("PDF_VISION_TABLE_MAX_SIDE", "1400").strip()), 2048))
    except ValueError:
        return 1400


def unified_sheet_ocr_enabled() -> bool:
    """Один OCR правой колонки (все таблицы) — быстрее и полнее."""
    default = "1" if not accuracy_mode() else "0"
    return (os.environ.get("PDF_UNIFIED_OCR") or default).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def report_normative_compact() -> bool:
    """Таблица нормативов без колонки «контекст»."""
    return (os.environ.get("PDF_REPORT_NORMATIVE_COMPACT") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def body_dpi() -> int:
    default = "420" if accuracy_mode() else "220"
    try:
        return max(160, min(int(os.environ.get("PDF_BODY_DPI", default).strip()), 720))
    except ValueError:
        return int(default)


def img2table_enabled() -> bool:
    """Таблицы через img2table — по умолчанию только если зонный OCR слабый."""
    default = "0"
    return (os.environ.get("PDF_IMG2TABLE") or default).strip().lower() in ("1", "true", "yes", "on")


def img2table_when_weak() -> bool:
    """Не дублировать img2table, если зонный OCR уже дал структуру."""
    return (os.environ.get("PDF_IMG2TABLE_WHEN_WEAK") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def img2table_zone_enabled() -> bool:
    """img2table по прямоугольнику зоны, если OCR+CV дали слабую структуру (локально)."""
    default = "1" if accuracy_mode() else "0"
    return (os.environ.get("PDF_IMG2TABLE_ZONE") or default).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def table_clahe_enabled() -> bool:
    """CLAHE для табличных зон перед Tesseract."""
    default = "1" if accuracy_mode() else "0"
    return (os.environ.get("PDF_TABLE_CLAHE") or default).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def ocr_deskew_enabled() -> bool:
    """Deskew скана перед Tesseract (как deskew в промышленных пайплайнах)."""
    default = "1" if accuracy_mode() else "0"
    return (os.environ.get("PDF_OCR_DESKEW") or default).strip().lower() in ("1", "true", "yes", "on")


def ocr_multiview_enabled() -> bool:
    """Несколько углов поворота кропа перед OCR (0°/90°/270° или полный набор)."""
    default = "1" if accuracy_mode() else "0"
    return (os.environ.get("PDF_OCR_MULTIVIEW") or default).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def ocr_multiview_fast() -> bool:
    """Только 3 угла — быстрее на CPU (Surya/Tesseract)."""
    return (os.environ.get("PDF_OCR_MULTIVIEW_FAST") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def ocr_multiview_for_surya() -> bool:
    """Многоракурсный OCR и для Surya (медленно; по умолчанию выкл.)."""
    return (os.environ.get("PDF_OCR_MULTIVIEW_SURYA") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def discover_multidpi_enabled() -> bool:
    """Поиск зон по якорям на двух DPI (грубо + точно)."""
    default = "1" if accuracy_mode() else "0"
    return (os.environ.get("PDF_DISCOVER_MULTIDPI") or default).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def cv_zone_refine_enabled() -> bool:
    """Подогнать spec/legend/explication по контурам таблиц OpenCV (разные размеры на листах)."""
    default = "1" if accuracy_mode() else "0"
    return (os.environ.get("PDF_CV_ZONE_REFINE") or default).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def report_faithful() -> bool:
    """Только OCR/парсер, без LLM-полировки отчёта (нет подгонки текста)."""
    return (os.environ.get("PDF_REPORT_FAITHFUL") or "0").strip().lower() in ("1", "true", "yes", "on")


def report_include_body_text() -> bool:
    """Сырой OCR поля чертежа в отчёт (на схемах часто нечитаем)."""
    default = "1" if unified_sheet_ocr_enabled() else "0"
    return (os.environ.get("PDF_REPORT_BODY") or default).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def report_include_full_text_layer() -> bool:
    """Дамп всего текстового слоя PDF в конец отчёта (обычно не нужен)."""
    raw = os.environ.get("PDF_REPORT_FULL_TEXT")
    if raw is not None and str(raw).strip():
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    return not report_faithful() and not local_only_mode()


def report_include_quality() -> bool:
    """Блок «Проверка чертежа» и таблица шрифтов."""
    raw = os.environ.get("PDF_REPORT_QUALITY")
    if raw is not None and str(raw).strip():
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    return not report_faithful()


def body_ocr_enabled() -> bool:
    """OCR поля чертежа (схема, подписи, нормативы на поле) — один проход."""
    default = "1" if not accuracy_mode() else "0"
    return (os.environ.get("PDF_BODY_OCR") or default).strip().lower() in ("1", "true", "yes", "on")


def body_ocr_tiled() -> bool:
    return (os.environ.get("PDF_BODY_OCR_TILED") or "1").strip().lower() in ("1", "true", "yes", "on")


def normative_table_dpi() -> int:
    return tile_ocr_dpi()


def tile_ocr_dpi() -> int:
    """DPI OCR по тайлам листа."""
    try:
        default = str(min(table_dpi(), 320))
        raw = os.environ.get("PDF_TILE_OCR_DPI") or os.environ.get("PDF_NORMATIVE_TABLE_DPI") or default
        return max(200, min(int(str(raw).strip()), 480))
    except ValueError:
        return min(table_dpi(), 320)


def normative_time_budget_sec() -> float:
    return tile_ocr_time_budget_sec()


def tile_ocr_time_budget_sec() -> float:
    """Общий бюджет OCR по тайлам (нормативы и текст), сек."""
    try:
        raw = (
            os.environ.get("PDF_TILE_OCR_TIME_BUDGET")
            or os.environ.get("PDF_NORMATIVE_TIME_BUDGET")
            or "280"
        )
        return max(30.0, min(float(str(raw).strip()), 600.0))
    except ValueError:
        return 280.0


def gost_check_extra_per_page_sec() -> float:
    """Базовая добавка с/лист для коротких/средних томов.

    Минимум 20 с: иначе длинные тома обрываются на OCR. По умолчанию 28 с —
    запас на полный лист (сетка 1×1) + хвост проверки Стройдока.
    """
    try:
        return max(20.0, float(os.environ.get("PDF_GOST_EXTRA_PER_PAGE_SEC", "28").strip()))
    except ValueError:
        return 28.0


def gost_check_total_budget_max_sec() -> float:
    """Верхний предел общего времени (тома 200+ листов — до нескольких часов)."""
    try:
        return max(600.0, float(os.environ.get("PDF_GOST_CHECK_BUDGET_MAX", "14400").strip()))
    except ValueError:
        return 14400.0


def gost_check_total_budget_sec(page_count: int = 1) -> float:
    """Основной лимит OCR на один запрос.

    Ступенчато по объёму (точность на коротких, покрытие на длинных):
    - листы 2–40: полный rate (env / 28 с)
    - 41–120: ~85% rate, не ниже 22 с/лист
    - 121+: ~72% rate, не ниже 20 с/лист (хватает на full-page OCR при сниженном DPI)
    """
    try:
        base = max(60.0, float(os.environ.get("PDF_GOST_CHECK_BUDGET", "600").strip()))
    except ValueError:
        base = 600.0
    pages = max(1, int(page_count))
    if pages <= 1:
        return min(base, gost_check_total_budget_max_sec())

    rate = gost_check_extra_per_page_sec()
    mid = max(rate * 0.85, 22.0)
    lo = max(rate * 0.72, 20.0)
    extra_pages = pages - 1
    n_hi = min(extra_pages, 39)  # pages 2..40
    n_mid = min(max(0, extra_pages - 39), 80)  # pages 41..120
    n_lo = max(0, extra_pages - 119)  # pages 121+
    extra = n_hi * rate + n_mid * mid + n_lo * lo
    return min(base + extra, gost_check_total_budget_max_sec())


def gost_check_budget_human(page_count: int = 1) -> str:
    """Верхняя оценка времени (фактически часто быстрее)."""
    sec = int(round(gost_check_total_budget_sec(page_count)))
    if sec < 90:
        return f"до ~{sec} с"
    minutes = max(1, int(round(sec / 60)))
    if minutes < 60:
        return f"до ~{minutes} мин"
    hours, mins = divmod(minutes, 60)
    if mins:
        return f"до ~{hours} ч {mins} мин"
    return f"до ~{hours} ч"


def stn_pipeline_reserve_sec(page_count: int = 1, refs_count: int = 0) -> float:
    """Пост-OCR бюджет STN (масштабируется с числом листов и нормативов)."""
    total = gost_check_total_budget_sec(page_count)
    pages = max(1, int(page_count))
    refs = max(int(refs_count), 0)
    if pages == 1:
        return min(total * 0.4, max(75.0, stn_batch_budget_sec() + max(refs, 1) * 3.5))
    reserve = stn_batch_budget_sec() + max(refs, 1) * 3.0 + pages * 2.0
    return min(total * 0.32, max(50.0, reserve))


def pipeline_stn_deadline(*, pipeline_t0: float, page_count: int = 1, refs_count: int = 0) -> float:
    """Отдельное окно STN, которое начинается после завершения OCR.

    На больших PDF OCR может занять почти весь общий бюджет запроса. Если считать
    дедлайн STN от начала пайплайна, проверка normy.stn.by стартует уже с
    истёкшим окном и все нормативы помечаются как пропущенные. Поэтому время
    старта пайплайна оставлено в сигнатуре для совместимости, но окно проверки
    всегда отсчитывается от текущего момента.
    """
    import time

    del pipeline_t0
    now = time.monotonic()
    reserve = stn_pipeline_reserve_sec(page_count, refs_count)
    return now + max(60.0, reserve)


def pipeline_preview_deadline(*, pipeline_t0: float, page_count: int = 1) -> float:
    """Жёсткий конец превью — внутри общего лимита на лист."""
    return pipeline_t0 + gost_check_total_budget_sec(page_count)


def pipeline_post_ocr_deadline(*, pipeline_t0: float, page_count: int = 1) -> float:
    """Окно после OCR: STN и превью параллельно до конца бюджета."""
    return pipeline_preview_deadline(pipeline_t0=pipeline_t0, page_count=page_count)


def stn_batch_budget_sec() -> float:
    """Резерв времени на проверку normy.stn.by внутри общего бюджета."""
    try:
        return max(20.0, min(float(os.environ.get("PDF_STN_BATCH_BUDGET", "60").strip()), 180.0))
    except ValueError:
        return 60.0


def normative_force_tile_ocr() -> bool:
    """Всегда OCR на тайлах (даже при текстовом слое). По умолчанию выкл —

    searchable PDF уже читаются слоем + merge; принудительный OCR жрёт GPU/CPU
    и раньше обрывал бюджет. Для плохих сканов: PDF_NORMATIVE_FORCE_OCR=1.
    """
    return (os.environ.get("PDF_NORMATIVE_FORCE_OCR") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def normative_wide_right_dpi_boost() -> float:
    """DPI-множитель для правых тайлов и spec_right на широких листах."""
    try:
        return max(1.0, min(float(os.environ.get("PDF_NORMATIVE_WIDE_DPI_BOOST", "1.15").strip()), 1.35))
    except ValueError:
        return 1.15


def normative_supplement_budget_sec() -> float:
    """Резерв на доп. OCR spec_right на широких листах."""
    try:
        return max(16.0, min(float(os.environ.get("PDF_NORMATIVE_SUPPLEMENT_SEC", "28").strip()), 45.0))
    except ValueError:
        return 28.0


def normative_ocr_budget_sec(page_count: int = 1, *, doc: Any | None = None) -> float:
    """Бюджет OCR в общем лимите; для 1 листа OCR идёт до STN."""
    total = gost_check_total_budget_sec(page_count)
    pages = max(1, int(page_count))
    if pages == 1:
        post_ocr_tail = max(75.0, stn_batch_budget_sec() + 22.0)
        ocr_cap = max(165.0, min(total * 0.72, total - post_ocr_tail))
    else:
        min_stn_tail = min(55.0, stn_batch_budget_sec())
        ocr_cap = max(60.0, total - min_stn_tail)

    from belener.tile_ocr import page_tile_jobs, supplements_for_page_scan

    cols, rows = tile_grid_for_page_count(page_count)
    tiles_total = 0
    if doc is not None and getattr(doc, "page_count", 0):
        scan_pages = min(int(doc.page_count), pages)
        for i in range(scan_pages):
            rect = doc[i].rect
            tiles_total += len(page_tile_jobs(rect, cols=cols, rows=rows))
            tiles_total += len(supplements_for_page_scan(rect, pages))
    else:
        if pages <= 4:
            sup_per_page = 8
        elif pages <= 12:
            sup_per_page = 1
        else:
            sup_per_page = 0
        tiles_total = pages * (cols * rows + sup_per_page)

    full_page = cols * rows <= 1
    if pages == 1:
        per_tile = 11.0
    elif full_page:
        # A4 скан целиком: планируем с запасом под весь том (согласовано со ступенями бюджета).
        if pages <= 50:
            per_tile = 24.0
        elif pages <= 120:
            per_tile = 22.0
        else:
            per_tile = 20.0
    elif pages <= 12:
        per_tile = 9.5
    else:
        per_tile = 8.0
    min_needed = tiles_total * per_tile + (normative_supplement_budget_sec() if pages <= 4 else 10.0)
    single = tile_ocr_time_budget_sec()
    # На длинных томах не упираемся в PDF_TILE_OCR_TIME_BUDGET (обычно 280 с).
    floor = single if pages <= 8 else max(single, min_needed * 0.95)
    # Почти весь multipage-бюджет OCR (STN хвост уже вычтен в ocr_cap).
    return max(45.0, min(max(floor, min_needed), ocr_cap, total * 0.99))


def tile_ocr_max_pages() -> int:
    try:
        raw = os.environ.get("PDF_TILE_OCR_MAX_PAGES", "0")
        v = int(str(raw or "0").strip())
        return 0 if v <= 0 else min(v, 100)
    except ValueError:
        return 0


def tile_grid_for_page_count(page_count: int) -> tuple[int, int]:
    """Сетка OCR: с 13+ листов — целая страница, иначе не успеваем весь том."""
    n = max(1, int(page_count))
    if n <= 1:
        return 4, 2
    if n <= 5:
        return 3, 2
    if n <= 12:
        return 2, 2
    return 1, 1


def tile_ocr_dpi_for_pages(page_count: int) -> int:
    base = tile_ocr_dpi()
    n = max(1, int(page_count))
    if n <= 3:
        return base
    if n <= 8:
        return max(260, min(base, 300))
    if n <= 20:
        return max(240, min(base, 280))
    if n <= 40:
        return max(210, min(base, 230))
    if n <= 60:
        return max(200, min(base, 220))
    return max(180, min(base, 200))


def ocr_budget_for_gost_check(*, pipeline_deadline: float | None = None, page_count: int = 1) -> float:
    """Сколько секунд отдать OCR, не съедая резерв STN."""
    tile_cap = normative_ocr_budget_sec(page_count)
    if pipeline_deadline is None:
        return tile_cap
    import time

    left = pipeline_deadline - time.monotonic() - stn_batch_budget_sec()
    return max(25.0, min(tile_cap, left))


def tile_ocr_parallel_workers() -> int:
    """Параллель страниц при remote OCR. На GPU держим низко — VRAM не thrash."""
    try:
        default = "2" if tile_ocr_offload_remote() else "1"
        return max(1, min(int(os.environ.get("PDF_TILE_OCR_PARALLEL", default).strip()), 4))
    except ValueError:
        return 2


def normative_tile_overlap_frac() -> float:
    return tile_ocr_overlap_frac()


def tile_ocr_overlap_frac() -> float:
    """Наложение соседних тайлов (доля шага сетки)."""
    try:
        raw = os.environ.get("PDF_TILE_OCR_OVERLAP") or os.environ.get("PDF_NORMATIVE_TILE_OVERLAP") or "0.12"
        return max(0.06, min(float(str(raw).strip()), 0.25))
    except ValueError:
        return 0.12


def body_min_chars() -> int:
    """Меньше символов в зоне body — дозаполнение vision (ТТ, примечания)."""
    try:
        return max(80, min(int(os.environ.get("PDF_BODY_MIN_CHARS", "400").strip()), 8000))
    except ValueError:
        return 400


def vision_body_dpi() -> int:
    try:
        return max(280, min(int(os.environ.get("PDF_VISION_BODY_DPI", "420").strip()), 600))
    except ValueError:
        return 420


def vision_body_max_side() -> int:
    try:
        return max(960, min(int(os.environ.get("PDF_VISION_BODY_MAX_SIDE", "1536").strip()), 2048))
    except ValueError:
        return 1536


def notes_column_frac() -> float:
    try:
        v = float(os.environ.get("PDF_NOTES_FRAC", "0.30").strip())
        return max(0.18, min(v, 0.42))
    except ValueError:
        return 0.30


def extract_dpi() -> int:
    try:
        return max(200, min(int(os.environ.get("PDF_EXTRACT_DPI", "360").strip()), 600))
    except ValueError:
        return 360


def extract_tile_px() -> int:
    try:
        return max(1200, min(int(os.environ.get("PDF_EXTRACT_TILE_PX", "2400").strip()), 4000))
    except ValueError:
        return 2400


def extract_tile_overlap() -> int:
    try:
        return max(80, min(int(os.environ.get("PDF_EXTRACT_TILE_OVERLAP", "240").strip()), 600))
    except ValueError:
        return 240


def extract_text_layer_min() -> int:
    """Меньше символов на странице — нужен OCR всего листа."""
    try:
        return max(0, min(int(os.environ.get("PDF_EXTRACT_TEXT_MIN", "120").strip()), 2000))
    except ValueError:
        return 120


def extract_text_layer_fast_min() -> int:
    """Экспорт nanoCAD: достаточно текстового слоя — без полностраничного OCR."""
    try:
        v = int(os.environ.get("PDF_TEXT_LAYER_FAST_MIN", "280").strip())
        if v <= 0:
            return 99999999
        return max(80, min(v, 99999999))
    except ValueError:
        return 280


def extract_parallel_pages() -> int:
    try:
        return max(1, min(int(os.environ.get("PDF_EXTRACT_PARALLEL", "1").strip()), 4))
    except ValueError:
        return 1


def ocr_timeout_sec() -> int:
    try:
        return max(60, min(int(os.environ.get("PDF_OCR_TIMEOUT", "180").strip()), 600))
    except ValueError:
        return 180


def vision_mode() -> str:
    """auto — vision только если OCR неполный; always — всегда; off — только OCR."""
    if local_only_mode():
        return "off"
    default = "auto" if accuracy_mode() else "off"
    return (os.environ.get("PDF_VISION_MODE") or default).strip().lower()


def vision_zones_enabled() -> bool:
    if vision_mode() == "off":
        return False
    default = "1" if accuracy_mode() else "1"
    return (os.environ.get("PDF_VISION_ZONES") or default).strip().lower() in ("1", "true", "yes", "on")


def vision_tables_enabled() -> bool:
    """Vision для таблиц (перечень, легенда) — по умолчанию выкл: модель часто «додумывает» строки."""
    if vision_mode() == "off":
        return False
    raw = os.environ.get("PDF_VISION_TABLES")
    if raw is not None and str(raw).strip():
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if report_faithful():
        return False
    return False


def vision_stamp_enabled() -> bool:
    """Vision для штампа (медленно на CPU). По умолчанию выкл — штамп через OCR."""
    if report_faithful():
        return False
    raw = os.environ.get("PDF_VISION_STAMP")
    if raw is None or not str(raw).strip():
        return False
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def img2table_spec_primary() -> bool:
    """Сначала img2table для зон перечня. Выкл. при Surya/DeepSeek — они точнее на сканах."""
    from belener.config import ocr_engine

    if ocr_engine() in ("surya", "deepseek"):
        return False
    default = "0"
    return (os.environ.get("PDF_IMG2TABLE_SPEC") or default).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def ocr_max_pixels() -> int:
    try:
        return max(8_000_000, min(int(os.environ.get("PDF_OCR_MAX_PIXELS", "45000000").strip()), 120_000_000))
    except ValueError:
        return 45_000_000


def vision_zones_model() -> str:
    return (
        os.environ.get("PDF_VISION_MODEL")
        or os.environ.get("PDF_VISION_ZONES_MODEL")
        or "qwen2.5vl:7b"
    ).strip()


def vision_zone_dpi() -> int:
    try:
        return max(240, min(int(os.environ.get("PDF_VISION_DPI", "420").strip()), 720))
    except ValueError:
        return 420


def vision_max_side() -> int:
    try:
        return max(800, min(int(os.environ.get("PDF_VISION_MAX_SIDE", "1280").strip()), 1600))
    except ValueError:
        return 1280


def stamp_llm_enabled() -> bool:
    if vision_zones_enabled():
        return False
    return (os.environ.get("PDF_STAMP_LLM") or "1").strip().lower() in ("1", "true", "yes", "on")


def report_markdown_tables() -> bool:
    """Таблицы в отчёте — markdown | col | (как в чате), не ASCII +---+."""
    return (os.environ.get("PDF_REPORT_MARKDOWN") or "1").strip().lower() in ("1", "true", "yes", "on")


def report_llm_enabled() -> bool:
    """Локальная Ollama — не облако; работает даже при PDF_LOCAL_ONLY=1."""
    return (os.environ.get("PDF_REPORT_LLM") or "1").strip().lower() in ("1", "true", "yes", "on")


def report_llm_mode() -> str:
    """polish — правка готового markdown; json — сборка из JSON (без мусора черновика)."""
    default = "json" if accuracy_mode() else "polish"
    return (os.environ.get("PDF_REPORT_LLM_MODE") or default).strip().lower()


def report_llm_timeout_sec() -> int:
    try:
        return max(30, min(int(os.environ.get("PDF_REPORT_LLM_TIMEOUT", "90").strip()), 300))
    except ValueError:
        return 90


def report_llm_num_predict() -> int:
    try:
        return max(512, min(int(os.environ.get("PDF_REPORT_LLM_NUM_PREDICT", "2048").strip()), 4096))
    except ValueError:
        return 2048


def report_llm_model() -> str:
    return (
        os.environ.get("PDF_REPORT_LLM_MODEL")
        or model_drawing()
        or os.environ.get("MODEL_DEFAULT")
        or "gemma3:4b"
    ).strip()


def stn_lookup_enabled() -> bool:
    return (os.environ.get("PDF_STN_LOOKUP") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def stn_base_url() -> str:
    try:
        from belener.integration_store import get_stn_credentials

        creds = get_stn_credentials()
        if creds.get("base_url"):
            return creds["base_url"].rstrip("/")
    except Exception:
        pass
    try:
        from belener.settings_store import get_setting

        db = get_setting("stn.base_url")
        if db:
            return db.rstrip("/")
    except Exception:
        pass
    return (os.environ.get("PDF_STN_BASE_URL") or "https://normy.stn.by").strip().rstrip("/")


def stn_login() -> str:
    try:
        from belener.integration_store import get_stn_credentials

        creds = get_stn_credentials()
        if creds.get("login"):
            return creds["login"]
    except Exception:
        pass
    try:
        from belener.settings_store import get_setting

        db = get_setting("stn.login")
        if db:
            return db
    except Exception:
        pass
    return (os.environ.get("PDF_STN_LOGIN") or os.environ.get("STN_LOGIN") or "").strip()


def stn_password() -> str:
    try:
        from belener.integration_store import get_stn_credentials

        creds = get_stn_credentials()
        if creds.get("password"):
            return creds["password"]
    except Exception:
        pass
    try:
        from belener.settings_store import get_setting

        db = get_setting("stn.password")
        if db:
            return db
    except Exception:
        pass
    return (os.environ.get("PDF_STN_PASSWORD") or os.environ.get("STN_PASSWORD") or "").strip()


def normacs_credentials() -> dict[str, str]:
    from belener.integration_store import get_normacs_credentials

    return get_normacs_credentials()


def normacs_lookup_enabled() -> bool:
    raw = (os.environ.get("PDF_NORMACS_LOOKUP") or "auto").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    creds = normacs_credentials()
    return bool(creds.get("login") and creds.get("password"))


def normacs_timeout_sec() -> int:
    try:
        return max(5, min(int(os.environ.get("PDF_NORMACS_TIMEOUT", "30")), 60))
    except ValueError:
        return 30


def normacs_retries() -> int:
    try:
        return max(0, min(int(os.environ.get("PDF_NORMACS_RETRIES", "1")), 2))
    except ValueError:
        return 1


def normacs_document_budget_sec() -> float:
    return normacs_timeout_sec() * (normacs_retries() + 1) + 2


def normacs_batch_budget_sec(refs_count: int) -> float:
    try:
        cap = max(60, min(int(os.environ.get("PDF_NORMACS_BUDGET_MAX", "3600")), 14400))
    except ValueError:
        cap = 3600
    # timeout относится к целому документу; оставляем окно для каждого обозначения.
    return min(cap, max(120, refs_count * normacs_document_budget_sec() + 30))


def stn_timeout_sec() -> int:
    try:
        return max(8, min(int(os.environ.get("PDF_STN_TIMEOUT", "15").strip()), 90))
    except ValueError:
        return 15

def ignore_ssl_errors() -> bool:
    """Отключение проверки SSL-сертификатов (для работы за корпоративными прокси)."""
    return (os.environ.get("BELENER_IGNORE_SSL") or "0").strip().lower() in ("1", "true", "yes", "on")


def tnpa_timeout_sec() -> int:
    """tnpa.by на медленных каналах часто 30–70 с на запрос."""
    try:
        return max(25, min(int(os.environ.get("PDF_TNPA_TIMEOUT", "70").strip()), 120))
    except ValueError:
        return 70


def tnpa_connect_timeout_sec() -> float:
    """Быстрый отказ, если до tnpa.by нет маршрута (VPN, Docker)."""
    try:
        return max(5.0, min(float(os.environ.get("PDF_TNPA_CONNECT_TIMEOUT", "12").strip()), 30.0))
    except ValueError:
        return 12.0


def tnpa_parallel_workers() -> int:
    """1 воркер по умолчанию: 3 параллели на сервере валят SSL handshake."""
    try:
        raw = (os.environ.get("PDF_TNPA_PARALLEL") or "1").strip()
        return max(1, min(int(raw), 3))
    except ValueError:
        return 1


def tnpa_max_queries() -> int:
    """Один короткий SearchParam — меньше HTTP на медленном tnpa.by."""
    try:
        return max(1, min(int(os.environ.get("PDF_TNPA_MAX_QUERIES", "1").strip()), 3))
    except ValueError:
        return 1


def tnpa_budget_max_sec() -> float:
    """Верхний предел окна ТНПА (независимо от OCR-бюджета)."""
    try:
        return max(300.0, float(os.environ.get("PDF_TNPA_BUDGET_MAX", "1800").strip()))
    except ValueError:
        return 1800.0


def tnpa_batch_budget_sec(page_count: int = 1, refs_count: int = 0) -> float:
    """Резерв на пакетную проверку tnpa.by (параллельно со STN).

    Не режем 55% OCR-бюджета: на медленном канале 11 refs × ~55–70 с
    иначе обрываются после ~4 найденных (как в логах сервера).
    """
    pages = max(1, int(page_count))
    refs = max(int(refs_count), 0)
    workers = max(1, tnpa_parallel_workers())
    # Полный timeout на «волну» (ceil(refs/workers)), плюс запас на handshake/retry.
    per_wave = max(25.0, float(tnpa_timeout_sec()) + 12.0)
    waves = max(1, (refs + workers - 1) // workers) if refs else 1
    base = max(180.0, waves * per_wave + pages * 2.0)
    return min(base, tnpa_budget_max_sec())


def pipeline_tnpa_deadline(
    *, pipeline_t0: float, page_count: int = 1, refs_count: int = 0
) -> float:
    """Окно ТНПА от текущего момента — не режем остатком OCR."""
    import time

    del pipeline_t0
    now = time.monotonic()
    reserve = tnpa_batch_budget_sec(page_count, refs_count)
    return now + max(reserve, 180.0)


def stn_parallel_workers() -> int:
    try:
        return max(1, min(int(os.environ.get("PDF_STN_PARALLEL", "1").strip()), 4))
    except ValueError:
        return 1


def stn_max_queries() -> int:
    """Сколько вариантов запроса пробовать на STN (остальные — только при OCR-вариантах)."""
    try:
        return max(1, min(int(os.environ.get("PDF_STN_MAX_QUERIES", "3").strip()), 8))
    except ValueError:
        return 3


def stn_ocr_variant_limit() -> int:
    try:
        return max(0, min(int(os.environ.get("PDF_STN_OCR_VARIANTS", "4").strip()), 20))
    except ValueError:
        return 4


def stn_max_refs() -> int:
    """Устарело: обрезка списка нормативов отключена — всегда проверяем все.

    PDF_STN_MAX_REFS игнорируется (раньше на серверах оставалось 50 в .env
    и контейнер продолжал skip даже после дефолта 0).
    """
    return 0


def normative_skip_tiles_min_refs() -> int:
    return 0


def tile_ocr_psm_modes() -> tuple[int, ...]:
    raw = (os.environ.get("PDF_TILE_OCR_PSM") or "6").strip()
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(max(3, min(int(part), 13)))
        except ValueError:
            continue
    return tuple(out) if out else (6, 3)


def tile_text_skip_ocr_min_chars() -> int:
    """В тайле достаточно текста из PDF — Tesseract не вызываем."""
    try:
        return max(0, min(int(os.environ.get("PDF_TILE_TEXT_SKIP_OCR_MIN", "100").strip()), 2000))
    except ValueError:
        return 100
