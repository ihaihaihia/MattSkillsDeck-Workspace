#!/usr/bin/env python3
"""JSON/Markdown bridge used by the DSH host plugin.

Keep stdout clean: the Node host treats it as the tool result.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longtou.cli import render_markdown  # noqa: E402
from longtou.engine import build_report  # noqa: E402
from longtou.history import save_report  # noqa: E402


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(prog="dsh-longtou-bridge")
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("report")
    report.add_argument("--date", type=int, required=True)
    report.add_argument("--stages", default="2,3,4")
    report.add_argument("--json", action="store_true")
    args = parser.parse_args()

    stages = sorted({int(part) for part in args.stages.split(",") if part.strip()})
    if any(stage < 2 for stage in stages):
        print("stages must be >= 2", file=sys.stderr)
        return 2

    try:
        result = build_report(args.date, stages)
    except Exception as exc:  # noqa: BLE001 - bridge must return a clean error
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0

    markdown = render_markdown(result)
    trade_text = str(result.get("next_date_text") or "").replace("-", "")
    if trade_text.isdigit():
        save_report(int(trade_text), markdown)
    else:
        latest = ROOT / "output" / "latest.md"
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_text(markdown, encoding="utf-8")
    print(markdown, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
