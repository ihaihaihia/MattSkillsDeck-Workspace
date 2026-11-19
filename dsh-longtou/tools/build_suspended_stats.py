#!/usr/bin/env python3
"""Build a per-date suspended space-board cache for dsh-longtou.

For each of the last N market dates, record stocks that did not trade that day
but whose last bar ended a run of at least two consecutive limit-ups.  The
engine consults this cache instead of reading all 5k .day files on every report.
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longtou.rules import one_word_run  # noqa: E402
from longtou.tdx import TDX_VIPDOC, Bar, limit_pct, limit_price, list_stocks, read_day_file  # noqa: E402

OUT = ROOT / "data" / "suspended_space.json"
BACKUP_DIR = ROOT / ".dsh-switchman" / "backup"


def _read_existing(path: Path) -> dict[str, list[dict]]:
    """读现有缓存：不存在当空，损坏则先备份再当空，绝不静默丢弃旧数据。"""
    if not path.exists():
        return {}
    reason = "内容是 JSON null"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 损坏也要能继续，但必须先备份
        data = None
        reason = f"{type(exc).__name__}: {exc}"
    if not isinstance(data, dict):
        if data is not None:
            reason = f"顶层是 {type(data).__name__}，不是 dict"
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup = BACKUP_DIR / f"{path.name}.corrupt-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(path, backup)
        print(f"WARN {path.name} 损坏（{reason}），旧文件已备份到 {backup}，按空缓存继续", file=sys.stderr)
        return {}
    return {str(key): value for key, value in data.items()}


def _write_atomic(path: Path, data: dict) -> None:
    """先写同目录临时文件再 os.replace：中途被打断也不会留下半截 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    text = json.dumps({key: data[key] for key in sorted(data)}, ensure_ascii=False, indent=1)
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _merge_and_write(path: Path, window: dict[str, list[dict]], *, merge: bool) -> None:
    """窗口数据合并覆盖进现有缓存，保留窗口外的全部旧 key（默认行为）。"""
    window_keys = {str(key) for key in window}
    existing = _read_existing(path) if merge else {}
    merged = dict(existing)
    kept_stale = 0
    for key in sorted(window_keys):
        fresh = window[key]
        # 空列表可能来自抓取失败：不拿空值盖掉已有的非空历史，避免二次截断
        if merge and not fresh and merged.get(key):
            kept_stale += 1
            continue
        merged[key] = fresh
    _write_atomic(path, merged)
    print(
        f"summary window_days={len(window_keys)} keys_before={len(existing)} keys_after={len(merged)} "
        f"added={len(window_keys - set(existing))} "
        f"preserved_outside={len(set(merged) - window_keys)}"
        + (f" kept_stale={kept_stale}" if kept_stale else "")
        + ("" if merge else " merge=off"),
        flush=True,
    )


def calendar_days(left: int, right: int) -> int:
    return (
        datetime.strptime(str(left), "%Y%m%d").date()
        - datetime.strptime(str(right), "%Y%m%d").date()
    ).days


def heights_for(bars: list[Bar], code: str) -> list[int]:
    heights = [0] * len(bars)
    pct = limit_pct(code)
    prev_close = bars[0].close
    height = 0
    for i in range(1, len(bars)):
        if bars[i].close >= limit_price(prev_close, pct) - 0.005:
            height += 1
        else:
            height = 0
        heights[i] = height
        prev_close = bars[i].close
    return heights


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=80)
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="只写窗口内日期、丢弃窗口外旧 key（旧的覆盖行为，默认关闭）。",
    )
    args = parser.parse_args()

    stats = json.loads((ROOT / "data" / "market_stats.json").read_text(encoding="utf-8"))
    market_dates = sorted(int(d) for d in stats)
    calendar = market_dates[-max(args.days, 1) :]
    result: dict[str, list[dict]] = {str(d): [] for d in calendar}

    stocks = list_stocks(TDX_VIPDOC)
    for stock in stocks:
        try:
            bars = read_day_file(stock.path)
        except Exception:
            continue
        if len(bars) < 3:
            continue
        dates = [bar.date for bar in bars]
        heights = heights_for(bars, stock.code)
        for target in calendar:
            idx = bisect.bisect_left(dates, target)
            if idx < len(bars) and bars[idx].date == target:
                continue
            last_idx = idx - 1
            if last_idx < 1:
                continue
            last = bars[last_idx]
            gap = calendar_days(target, last.date)
            if gap <= 0 or gap > 30:
                continue
            height = heights[last_idx]
            if height < 2:
                continue
            result[str(target)].append(
                {
                    "code": stock.code,
                    "height": height,
                    "last_date": last.date,
                    "one_word_ratio": round(one_word_run(bars, stock.code, last_idx).ratio, 4),
                }
            )

    _merge_and_write(OUT, result, merge=not args.no_merge)
    counts = {key: len(value) for key, value in result.items()}
    print(f"wrote {OUT} days={len(calendar)} candidates={sum(counts.values())}")
    print("sample", {k: counts[k] for k in sorted(counts)[-5:]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
