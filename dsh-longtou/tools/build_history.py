#!/usr/bin/env python3
"""Pre-generate report history for panel previous/next browsing.

Run:
    python tools/build_history.py --days 40
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longtou.history import build_one, trade_calendar  # noqa: E402


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=40)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    calendar = trade_calendar()
    if len(calendar) < 2:
        print("market stats calendar is empty", file=sys.stderr)
        return 1
    dates = calendar[-args.days :]
    for number, trade_date in enumerate(dates, 1):
        try:
            markdown = build_one(trade_date, force=args.force)
            first = markdown.splitlines()[0] if markdown else ""
            print(f"[{number}/{len(dates)}] {trade_date} {first}", flush=True)
        except Exception as exc:
            print(f"[{number}/{len(dates)}] {trade_date} ERROR {type(exc).__name__}: {exc}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
