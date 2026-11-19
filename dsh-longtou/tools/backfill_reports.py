#!/usr/bin/env python3
"""按区间补齐每日日报（output/reports/<YYYYMMDD>.md），不逐日重建 latest.md。

为什么不用 tools/build_history.py：它走 history.build_one → save_report →
rebuild_latest，每写一天都要把全部日报重新拼一遍（当前 latest.md 已 8.9MB），
补 2025 年 240+ 天相当于读写数 GB、耗时数小时。这里只在收尾时重建一次。

用法：
    python tools/backfill_reports.py --from 20250101 --to 20260102
    python tools/backfill_reports.py --from 20250101 --to 20260102 --force
    python tools/backfill_reports.py --from 20250101 --to 20260102 --dry-run

语义：对交易日历中落在 [from, to] 的每一天，若无日报或 --force，则用
「前一交易日收盘 + 当日 09:25 集合竞价」重算该日报告（与 build_one 同口径）。
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longtou.cli import render_markdown  # noqa: E402
from longtou.engine import build_report  # noqa: E402
from longtou.history import REPORT_DIR, rebuild_latest, trade_calendar  # noqa: E402
from longtou.tdx import date_int_to_str  # noqa: E402


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="date_from", type=int, required=True)
    parser.add_argument("--to", dest="date_to", type=int, required=True)
    parser.add_argument("--force", action="store_true", help="已存在的日报也重算")
    parser.add_argument("--dry-run", action="store_true", help="只列出待补日期，不生成")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 天（0=不限）")
    args = parser.parse_args()

    calendar = trade_calendar()
    if not calendar:
        print("market stats calendar is empty", file=sys.stderr)
        return 1
    targets = [day for day in calendar if args.date_from <= day <= args.date_to]
    if args.limit > 0:
        targets = targets[: args.limit]
    pending = [day for day in targets if args.force or not (REPORT_DIR / f"{day}.md").exists()]
    print(f"区间内交易日 {len(targets)} 天，待生成 {len(pending)} 天", flush=True)
    if args.dry_run:
        print(" ".join(str(day) for day in pending))
        return 0

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ok = failed = skipped = 0
    started = time.time()
    for number, trade_date in enumerate(pending, 1):
        path = REPORT_DIR / f"{trade_date}.md"
        if path.exists() and not args.force:
            skipped += 1
            continue
        index = calendar.index(trade_date)
        if index == 0:
            print(f"[{number}/{len(pending)}] {trade_date} SKIP 无前一交易日", flush=True)
            skipped += 1
            continue
        try:
            report = build_report(
                calendar[index - 1],
                [2, 3, 4],
                next_date_override=trade_date,
                trade_date=trade_date,
                display_date=trade_date,
            )
            report["next_date_text"] = date_int_to_str(trade_date)
            markdown = render_markdown(report)
            path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
            ok += 1
            used = time.time() - started
            print(
                f"[{number}/{len(pending)}] {trade_date} OK 已用 {used:.0f}s "
                f"均 {used / max(ok, 1):.1f}s/天",
                flush=True,
            )
        except Exception as exc:  # 单日失败不中断整批
            failed += 1
            print(f"[{number}/{len(pending)}] {trade_date} ERROR {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc()

    rebuild_latest()
    print(f"完成：成功 {ok} 天，失败 {failed} 天，跳过 {skipped} 天，总耗时 {time.time() - started:.0f}s", flush=True)
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
