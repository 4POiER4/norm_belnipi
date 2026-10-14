#!/usr/bin/env python3
"""Проверка настроек tile OCR; Стройдок временно отключён по умолчанию."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

COMPOSE_STN = {
    "docker-compose.yml": {"PDF_STN_LOOKUP", "PDF_STN_BASE_URL", "PDF_STN_LOGIN", "PDF_STN_PASSWORD", "PDF_STN_TIMEOUT"},
    "docker-compose.fast.yml": {
        "PDF_TILE_OCR_DPI",
        "PDF_TILE_OCR_TIME_BUDGET",
        "PDF_TILE_OCR_OVERLAP",
        "PDF_NORMATIVE_SKIP_TILES_MIN_REFS",
        "PDF_STN_LOOKUP",
        "PDF_STN_PARALLEL",
        "PDF_STN_MAX_REFS",
    },
}

ENV_EXAMPLES = sorted(ROOT.glob(".env*.example"))
RECOMMENDED = {
    "PDF_STN_LOOKUP": "0",
    "PDF_STN_BASE_URL": "https://normy.stn.by",
    "PDF_TILE_OCR_DPI": "320",
    "PDF_TILE_OCR_TIME_BUDGET": "180",
    "PDF_NORMATIVE_SKIP_TILES_MIN_REFS": "0",
    "PDF_STN_PARALLEL": "1",
    "PDF_STN_TIMEOUT": "12-15",
    "PDF_GOST_CHECK_BUDGET": "210",
}


def _grep_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.is_file():
        return keys
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        keys.add(s.split("=", 1)[0].strip())
    return keys


def main() -> int:
    print("=== belener normative / STN setup ===\n")
    ok = True

    for fname, required in COMPOSE_STN.items():
        path = ROOT / fname
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        missing = [k for k in required if k not in text]
        status = "OK" if not missing else f"MISSING in file: {missing}"
        print(f"[compose] {fname}: {status}")
        ok = ok and not missing

    print("\n[env examples] STN / tile OCR keys:")
    for ex in ENV_EXAMPLES:
        keys = _grep_keys(ex)
        has_stn = "PDF_STN_LOOKUP" in keys
        has_tile = "PDF_TILE_OCR_DPI" in keys or "PDF_TILE_OCR_TIME_BUDGET" in keys
        note = []
        if not has_stn:
            note.append("no PDF_STN_*")
        if not has_tile:
            note.append("no PDF_TILE_*")
        flag = "OK" if has_stn and has_tile else ("partial" if has_stn or has_tile else "no normative block")
        if note:
            flag += f" ({', '.join(note)})"
        print(f"  {ex.name}: {flag}")

    env_path = ROOT / ".env"
    if env_path.is_file():
        keys = _grep_keys(env_path)
        print("\n[.env] active (values hidden):")
        for k in sorted(keys):
            if k.startswith(("PDF_STN", "PDF_TILE", "PDF_NORMATIVE", "PDF_GOST")):
                print(f"  {k}=…")
        if int(os.environ.get("PDF_STN_PARALLEL") or keys and "PDF_STN_PARALLEL" in keys or 0):
            par = None
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("PDF_STN_PARALLEL="):
                    par = line.split("=", 1)[1].strip()
            if par and par.isdigit() and int(par) > 2:
                print(f"  WARN PDF_STN_PARALLEL={par} — для IPS лучше 1-2 (см. docker-compose.fast.yml)")
    else:
        print("\n[.env] not found — используйте .env.example + docker-compose.fast.yml")

    print("\n[recommended for GOST check on scans]")
    for k, v in RECOMMENDED.items():
        print(f"  {k}={v}")

    print("\n[tests] run: python -m pytest tests/test_normative_refs.py tests/test_stn_lookup.py tests/test_normative_crops.py -q")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
