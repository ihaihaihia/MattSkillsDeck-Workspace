#!/usr/bin/env python3
"""Fill blank stock names in existing panel report tables."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longtou.history import REPORT_DIR, rebuild_latest  # noqa: E402


def main() -> int:
    names = json.loads((ROOT / "data" / "stock_names.json").read_text(encoding="utf-8"))
    changed = 0

    def top_board(match: re.Match) -> str:
        codes = [code for code in re.split(r"[,，\s]+", match.group(2)) if code.isdigit()]
        labeled = [f"{code} {names.get(code, '')}".strip() for code in codes]
        return f"最高板 **{match.group(1)}** ({', '.join(labeled) or '-'})"

    for path in sorted(REPORT_DIR.glob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        output: list[str] = []
        for line in lines:
            if line.startswith("|") and line.count("|") >= 5:
                cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                if len(cells) >= 3 and cells[1].isdigit() and len(cells[1]) == 6:
                    name = names.get(cells[1], "")
                    if name and cells[2] != name:
                        cells[2] = name
                        line = "| " + " | ".join(cells) + " |"
                        changed += 1
            line = re.sub(r"最高板 \*\*(\d+)\*\* \(([^)]*)\)", top_board, line)
            line = re.sub(
                r"\*\*(\d{6})\*\*",
                lambda match: f"**{match.group(1)} {names.get(match.group(1), '')}**".replace(" ****", "**"),
                line,
            )
            output.append(line)
        path.write_text("\n".join(output) + "\n", encoding="utf-8")
    rebuild_latest()
    print(f"updated_rows={changed} reports={len(list(REPORT_DIR.glob('*.md')))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
