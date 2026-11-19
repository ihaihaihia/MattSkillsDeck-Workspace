"""Local stock-name map and ST filter.

The map is a snapshot from the user's research project. It is intentionally
small and replaceable; the plugin never treats a missing name as investable
evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

_NAMES_PATH = Path(__file__).resolve().parents[1] / "data" / "stock_names.json"
_cache: dict[str, str] | None = None


def load_names() -> dict[str, str]:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_NAMES_PATH.read_text(encoding="utf-8"))
        except Exception:
            _cache = {}
    return _cache


def name_for(code: str) -> str:
    return load_names().get(code, "")


def is_st_name(name: str) -> bool:
    text = (name or "").strip().upper()
    return text.startswith("ST") or text.startswith("*ST")
