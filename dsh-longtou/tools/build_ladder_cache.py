#!/usr/bin/env python3
"""Cache F10 limit-board ladder rows (industry/concept tags) per market date."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eltdx import TdxClient  # noqa: E402

OUT = ROOT / "data" / "limit_ladder.json"
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
        # 分块失败会留下空列表：不拿空值盖掉已有的非空历史，避免二次截断
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=40)
    parser.add_argument(
        "--chunk",
        type=int,
        default=40,
        help="单次 f10 请求覆盖的交易日数；一次要 448 天会被原生层直接打断（exit 1、无 traceback），所以分块拉取再合并。",
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="只写窗口内日期、丢弃窗口外旧 key（旧的覆盖行为，默认关闭）。",
    )
    args = parser.parse_args()

    stats = json.loads((ROOT / "data" / "market_stats.json").read_text(encoding="utf-8"))
    calendar = sorted(int(key) for key in stats)
    selected = calendar[-max(args.days, 1) :]
    if not selected:
        print("empty market calendar", file=sys.stderr)
        return 1

    # 分块拉取再合并：单块失败只记日志继续，最后回报失败块清单（幂等，可重跑）
    chunk_size = max(int(args.chunk or 40), 1)
    chunks = [selected[i : i + chunk_size] for i in range(0, len(selected), chunk_size)]
    rows_by_date: dict[str, list[dict]] = {str(day): [] for day in selected}
    failed: list[str] = []
    with TdxClient(timeout=20) as client:
        for index, chunk in enumerate(chunks, 1):
            try:
                ladder = client.f10.limit_board_ladder(
                    str(chunk[0]), str(chunk[-1]), include_summary=True
                )
            except Exception as exc:  # noqa: BLE001 - 单块失败不放弃整轮
                failed.append(f"{chunk[0]}~{chunk[-1]}")
                print(
                    f"chunk {index}/{len(chunks)} {chunk[0]}~{chunk[-1]} FAILED: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                continue
            kept = 0
            for row in ladder.rows:
                day = str(getattr(row, "trading_date_value", "") or "")
                if day not in rows_by_date:
                    continue
                level = getattr(row, "board_level", None)
                if level is None or int(level) < 1:
                    continue
                rows_by_date[day].append(
                    {
                        "code": str(getattr(row, "code", "") or ""),
                        "name": str(getattr(row, "name", "") or ""),
                        "level": int(level),
                        "industry": str(getattr(row, "industry", "") or ""),
                        "reason": str(getattr(row, "limit_reason", "") or ""),
                        "extra": str(getattr(row, "limit_reason_extra", "") or ""),
                    }
                )
                kept += 1
            print(f"chunk {index}/{len(chunks)} {chunk[0]}~{chunk[-1]} rows={len(ladder.rows)} kept={kept}", flush=True)
    if failed:
        print(f"failed_chunks={len(failed)}: {failed}", file=sys.stderr)

    _merge_and_write(OUT, rows_by_date, merge=not args.no_merge)
    counts = {key: len(value) for key, value in rows_by_date.items()}
    print(f"wrote {OUT} days={len(selected)} rows={sum(counts.values())}")
    print("sample", {key: counts[key] for key in sorted(counts)[-5:]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
