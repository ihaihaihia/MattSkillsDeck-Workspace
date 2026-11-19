#!/usr/bin/env python3
"""Refresh data/stock_names.json from eltdx's live security list.

The old map had only ~2,390 names and left codes such as 601811/600831/601231
blank in the panel.  eltdx returns the current name for every security.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eltdx import TdxClient  # noqa: E402

OUT = ROOT / "data" / "stock_names.json"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    with TdxClient(timeout=10) as client:
        items = client.codes.latest_stock_list() or []
    names: dict[str, str] = {}
    for item in items:
        code = str(getattr(item, "code", "") or "")
        name = str(getattr(item, "name", "") or "").strip()
        if len(code) == 6 and code.isdigit() and name:
            names[code] = name
    OUT.write_text(json.dumps(dict(sorted(names.items())), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"saved={OUT} names={len(names)}")
    for code in ("601811", "600831", "601231", "600683", "605189"):
        print(code, repr(names.get(code, "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
