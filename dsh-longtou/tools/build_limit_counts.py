#!/usr/bin/env python3
"""把 F10 连板天梯的 summary（市场总览：上涨/下跌/涨停/跌停家数）落盘为 data/limit_counts.json。

``tools/build_ladder_cache.py`` 拉 F10 天梯时带 ``include_summary=True``，但只存了 ``ladder.rows``
（连板个股），把 summary（含每日涨停/跌停家数）丢掉了。本脚本补存这一份，供
``tools/build_market_stats.py`` 以 F10 官方家数作为 limit_up/limit_down 主口径、本地日线兜底。

用法:
    python tools/build_limit_counts.py --start 20250501 --end 20250701
    python tools/build_limit_counts.py                     # 缺省取 market_stats 最近 40 个交易日
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eltdx import TdxClient  # noqa: E402

OUT = ROOT / "data" / "limit_counts.json"


def _read_existing(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({k: data[k] for k in sorted(data)}, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=None)
    parser.add_argument("--end", type=int, default=None)
    parser.add_argument("--days", type=int, default=40)
    parser.add_argument("--chunk", type=int, default=40)
    args = parser.parse_args()

    stats = json.loads((ROOT / "data" / "market_stats.json").read_text(encoding="utf-8"))
    calendar = sorted(int(k) for k in stats)
    if args.start is None:
        selected = calendar[-max(args.days, 1):]
    else:
        selected = [d for d in calendar if args.start <= d <= (args.end or 99999999)]

    counts: dict[str, dict] = {}
    failed: list[str] = []
    with TdxClient(timeout=20) as client:
        chunk_size = max(args.chunk, 1)
        for i in range(0, len(selected), chunk_size):
            chunk = selected[i:i + chunk_size]
            try:
                lad = client.f10.limit_board_ladder(str(chunk[0]), str(chunk[-1]), include_summary=True)
            except Exception as exc:
                failed.append(f"{chunk[0]}~{chunk[-1]}")
                print(f"chunk {chunk[0]}~{chunk[-1]} FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
                continue
            for m in lad.market_overview:
                # F10 的 trade_date 形如 "2025-06-03"（带横杠），先归一化成 YYYYMMDD
                day = "".join(ch for ch in str(m.get("trade_date") or "") if ch.isdigit())
                if len(day) != 8:
                    continue
                up = m.get("limit_up_count")
                down = m.get("limit_down_count")
                if isinstance(up, int) and isinstance(down, int):
                    counts[day] = {
                        "limit_up": up,
                        "limit_down": down,
                        "advancing": m.get("advancing_count"),
                        "declining": m.get("declining_count"),
                        "flat": m.get("flat_count"),
                    }
            print(f"chunk {chunk[0]}~{chunk[-1]} summary_rows={len(lad.market_overview)}", flush=True)

    merged = _read_existing(OUT)
    merged.update(counts)
    _write_atomic(OUT, merged)
    print(f"wrote {OUT} days={len(counts)} total_keys={len(merged)} failed_chunks={len(failed)}")
    if counts:
        for k in sorted(counts)[:5]:
            print(k, counts[k])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
