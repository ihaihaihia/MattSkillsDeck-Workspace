#!/usr/bin/env python3
"""跑一次情绪周期采集，落盘 ``data/cycle/latest.json`` 与 ``data/cycle/YYYYMMDD.json``。

用法::

    .venv\\Scripts\\python.exe tools\\cycle_collect.py             # 基准日 = market_stats 最新交易日
    .venv\\Scripts\\python.exe tools\\cycle_collect.py --date 20261021

``--date`` 只影响涨停池取哪一天（历史日期由东财 getTopicZTPool 的 date 参数决定），
指数/成交额/板块/涨跌家数仍是实时快照。

取数全部走 ``longtou/cycle_source.py``，那层保证失败返回 None/[] 不抛异常，
所以本脚本即使外网全挂也能产出结构完整的 JSON（数值为 null）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longtou import cycle_source  # noqa: E402

OUT_DIR = ROOT / "data" / "cycle"
LATEST = OUT_DIR / "latest.json"


def write_payload(payload: dict, date: str, out_dir: Path = OUT_DIR) -> dict[str, Path]:
    """写 latest.json 与 YYYYMMDD.json（先写临时文件再替换，避免半截文件被面板读到）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    dated = out_dir / f"{date}.json"
    targets: dict[str, Path] = {"latest": LATEST if out_dir == OUT_DIR else out_dir / "latest.json", "dated": dated}
    for path in targets.values():
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    return targets


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="情绪周期数据采集（只读外网 + 本地历史）")
    parser.add_argument("--date", default=None, help="涨停池基准日 YYYYMMDD，缺省取 market_stats 最新交易日")
    parser.add_argument("--out", default=None, help="输出目录，缺省 data/cycle")
    args = parser.parse_args(argv)

    out_dir = Path(args.out).resolve() if args.out else OUT_DIR
    payload = cycle_source.collect(args.date)
    paths = write_payload(payload, payload["date"], out_dir)

    size = paths["latest"].stat().st_size
    print(f"date={payload['date']}")
    print(f"latest={paths['latest']} bytes={size}")
    print(f"dated={paths['dated']}")
    print(f"top_keys={list(payload.keys())}")
    print(
        "counts="
        f"indices={len(payload['indices'])} series={len(payload['series'])} "
        f"ladder={len(payload['ladder'])} boards_today={len(payload['boards_today'])} "
        f"sector_gain={len(payload['sector_top']['gain'])} sector_loss={len(payload['sector_top']['loss'])}"
    )
    print(f"temperature={payload['temperature']} breadth={payload['breadth']} amount={payload['amount']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
