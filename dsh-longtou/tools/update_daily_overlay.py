#!/usr/bin/env python3
"""Fetch recent daily bars from eltdx and write a local overlay.

The TDX ``.day`` files on this machine currently end at 2026-10-13.  This script
pulls the latest daily bars over the TDX protocol and stores only the missing
dates in ``data/daily_overlay.json``.  ``longtou.tdx.read_day_file`` merges the
overlay into local history, so the report can use the latest trading day without
rewriting the vendor's files.

Run:
    python tools/update_daily_overlay.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eltdx import TdxClient  # noqa: E402

from longtou.tdx import TDX_VIPDOC, list_stocks  # noqa: E402


def fetch_batch(client: TdxClient, codes: list[str], days: int) -> dict:
    try:
        return client.bars.get(codes, period="day", start=0, count=days, batch_size=len(codes)) or {}
    except Exception:
        # A single bad code should not discard the whole batch.
        out = {}
        for code in codes:
            try:
                part = client.bars.get([code], period="day", start=0, count=days, batch_size=1) or {}
                out.update(part)
            except Exception:
                continue
        return out


def default_since(today: date | None = None, back_days: int = 120) -> int:
    """默认起点：今天往前 120 天，保证 20 日窗口有足够余量。"""
    base = today or datetime.now().date()
    return int((base - timedelta(days=back_days)).strftime("%Y%m%d"))


def load_old_overlay(path: Path) -> dict[str, list[dict]]:
    """读取已有 overlay 的 ``bars``；文件不存在或不可解析时返回空字典。"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"警告: 旧 overlay 无法解析，跳过合并: {path} ({type(exc).__name__}: {exc})", flush=True)
        return {}
    bars = data.get("bars") if isinstance(data, dict) else None
    if not isinstance(bars, dict):
        return {}
    return {str(code): rows for code, rows in bars.items() if isinstance(rows, list)}


def merge_overlay(new_bars: dict[str, list[dict]], old_bars: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """按 (code, date) 合并：新数据优先，旧文件里新结果缺失的日期保留。"""
    merged: dict[str, dict[int, dict]] = {}
    for source in (old_bars, new_bars):
        for code, rows in source.items():
            by_date = merged.setdefault(code, {})
            for row in rows:
                if not isinstance(row, dict) or "date" not in row:
                    continue
                try:
                    day = int(row["date"])
                except (TypeError, ValueError):
                    continue
                by_date[day] = row
    return {code: [by_date[day] for day in sorted(by_date)] for code, by_date in merged.items() if by_date}


def write_overlay_atomic(path: Path, payload: str) -> None:
    """先写 ``path.tmp`` 再 os.replace，避免半截文件覆盖有效数据。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=80)
    parser.add_argument("--since", type=int, default=None, help="默认 (今天 - 120 天)；显式传入可覆盖")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "daily_overlay.json")
    args = parser.parse_args()
    if args.since is None:
        args.since = default_since()

    stocks = list_stocks(TDX_VIPDOC)
    if args.limit:
        stocks = stocks[: args.limit]
    groups = [stocks[i : i + args.batch_size] for i in range(0, len(stocks), args.batch_size)]
    overlay: dict[str, list[dict]] = {}
    fetched = 0
    failed = 0

    with TdxClient(timeout=10) as client:
        for number, group in enumerate(groups, 1):
            codes = [stock.code for stock in group]
            result = fetch_batch(client, codes, args.days)
            for stock in group:
                series = result.get(stock.full_code) or result.get(stock.code)
                if series is None:
                    failed += 1
                    continue
                rows = []
                for bar in series.bars:
                    day = int(bar.time.strftime("%Y%m%d"))
                    if day < args.since:
                        continue
                    volume = bar.volume_wire_value
                    if volume is None:
                        volume = float(bar.volume_lots or 0) * 100.0
                    rows.append(
                        {
                            "date": day,
                            "open": round(float(bar.open), 2),
                            "high": round(float(bar.high), 2),
                            "low": round(float(bar.low), 2),
                            "close": round(float(bar.close), 2),
                            "volume": float(volume),
                            "amount": float(bar.amount or 0.0),
                        }
                    )
                if rows:
                    overlay[stock.full_code] = rows
            fetched += len(group)
            if number % 10 == 0 or number == len(groups):
                print(f"[{number}/{len(groups)}] stocks={fetched} with_overlay={len(overlay)} failed={failed}", flush=True)

    old_bars = load_old_overlay(args.out)
    old_codes = len(old_bars)
    new_codes = len(overlay)
    # 安全阀：新结果为空，或 code 数不足旧文件一半时视为取数失败，不动旧文件。
    if new_codes == 0:
        print(
            f"警告: 本次未取到任何有效 bar（since={args.since}），不写文件，保留原文件: {args.out}",
            flush=True,
        )
        return 1
    if new_codes * 2 < old_codes:
        print(
            f"警告: 新结果 code 数 {new_codes} 不足旧文件 {old_codes} 的 50%，疑似取数异常，"
            f"不写文件，保留原文件: {args.out}",
            flush=True,
        )
        return 1

    merged = merge_overlay(overlay, old_bars)
    output = {
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "eltdx bars.get(period=day)",
        "since": args.since,
        "bars": merged,
    }
    write_overlay_atomic(args.out, json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    dates = sorted({row["date"] for rows in merged.values() for row in rows})
    print(
        f"saved={args.out} stocks_new={new_codes} stocks_merged={len(merged)} "
        f"dates={dates} bytes={args.out.stat().st_size}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
