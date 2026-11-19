#!/usr/bin/env python3
"""回补「120 天连板梯队」的历史 09:25 集合竞价成交额。

数据流：``data/cycle/latest.json`` 的 ``ladder`` -> 收集 ``(date, code)`` 对 ->
逐对取 ``AuctionSource().fetch(code, date)`` -> 同时落盘两个文件：

* ``data/auction_history.json``：竞价成交额，单位为亿元 float（6 位小数），
  ``longtou/cycle_source.py`` 的 ``build_ladder`` 读它补 ``auction_yi``。
* ``data/auction_volume_history.json``：09:25 竞价成交量，单位为手 float（3 位小数），
  给面板算「竞价量比」。

落盘结构（两份同形）::

    {"20261105": {"600206": 0.3254, ...}, ...}   # 金额（亿元）
    {"20261105": {"600206": 1234.5, ...}, ...}   # 成交量（手）

只用标准库 + 项目内 ``longtou.auction``。

用法::

    .venv\\Scripts\\python.exe tools\\auction_backfill.py --days 3     # 试跑最近 3 个交易日
    .venv\\Scripts\\python.exe tools\\auction_backfill.py              # 全量（断点续跑）

断点续跑默认开启：启动时读两个已有文件，某 ``(date, code)`` 两份都已采过才跳过；
只缺成交量（比如升级前只补过金额）也会重采并只补成交量那一侧。
``--no-resume`` 才从头重采。每采完一个交易日（或每 200 对）两份文件各原子写一次，
中途被杀不会毁掉已经落盘的数据。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longtou.auction import AuctionSource  # noqa: E402

LADDER = ROOT / "data" / "cycle" / "latest.json"
OUT = ROOT / "data" / "auction_history.json"  # 竞价成交额（亿元）
VOL = ROOT / "data" / "auction_volume_history.json"  # 09:25 竞价成交量（手）
LOG = ROOT / ".dsh-switchman" / "auction_backfill.log"

DUMP_EVERY = 200  # 每采到这么多对就落一次盘（跨日兜底）
RETRY_TIMES = 2  # AuctionSource.fetch 内部已重试一轮，这里再加 2 次兜底

_log_lock = threading.Lock()
_stdout_lock = threading.Lock()


def log(message: str) -> None:
    """追加一行带时间戳的日志到 ``.dsh-switchman/auction_backfill.log``（线程安全）。"""
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    with _log_lock:
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:  # noqa: BLE001 - 日志失败不能影响采集
            pass


def say(message: str) -> None:
    print(message, flush=True)
    log(message)


def load_ladder_pairs(days: int | None, codes: set[str] | None) -> tuple[list[str], list[tuple[str, str]]]:
    """从 ``data/cycle/latest.json`` 的 ladder 收集 ``(date, code)`` 对（按日期升序）。"""
    payload = json.loads(LADDER.read_text(encoding="utf-8"))
    ladder = payload.get("ladder") if isinstance(payload, dict) else None
    if not isinstance(ladder, list):
        raise SystemExit(f"ladder 缺失或不是数组：{LADDER}")

    by_date: dict[str, list[str]] = {}
    for col in ladder:
        if not isinstance(col, dict):
            continue
        date = str(col.get("date") or "").strip()
        if len(date) != 8 or not date.isdigit():
            continue
        bucket = by_date.setdefault(date, [])
        for level in col.get("levels") or []:
            if not isinstance(level, dict):
                continue
            for stock in level.get("stocks") or []:
                if not isinstance(stock, dict):
                    continue
                code = str(stock.get("code") or "").strip()
                if len(code) == 6 and code.isdigit() and code not in bucket:
                    bucket.append(code)

    dates = sorted(by_date)
    if days is not None and days > 0:
        dates = dates[-days:]
    pairs: list[tuple[str, str]] = []
    for date in dates:
        for code in by_date[date]:
            if codes and code not in codes:
                continue
            pairs.append((date, code))
    return dates, pairs


def load_existing(path: Path, ndigits: int = 6) -> dict[str, dict[str, float]]:
    """读已有结果；不存在/损坏 → {}（从头开始，不抛异常）。``ndigits`` 按文件量纲取整。"""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log(f"已有文件解析失败，按空表重来：{exc}")
        return {}
    if not isinstance(payload, dict):
        log("已有文件顶层不是对象，按空表重来")
        return {}
    cleaned: dict[str, dict[str, float]] = {}
    for date, row in payload.items():
        if not isinstance(row, dict):
            continue
        keep: dict[str, float] = {}
        for code, value in row.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                keep[str(code)] = round(float(value), ndigits)
        if keep:
            cleaned[str(date)] = keep
    return cleaned


def dump_atomic(data: dict[str, dict[str, float]], path: Path) -> None:
    """先写临时文件再 ``os.replace``：中途被杀不会留下半截 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{threading.get_ident()}")
    text = json.dumps({d: data[d] for d in sorted(data)}, ensure_ascii=False, sort_keys=True)
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
            except Exception:  # noqa: BLE001
                pass


def fetch_one(source: "AuctionSource", code: str, date: str) -> tuple[float, float] | None:
    """取单只股票的 ``(竞价成交额亿元, 竞价成交量手)``；失败返回 None。

    成交额保留 6 位小数（与既有 auction_history 一致），成交量保留 3 位小数。
    """
    last_error: Exception | None = None
    for attempt in range(RETRY_TIMES + 1):
        try:
            auction = source.fetch(code, date)
            if auction is None:
                raise RuntimeError("no opening-match tick")
            amount_yi = round(float(auction.amount_yuan) / 1e8, 6)
            volume_lots = round(float(auction.volume_lots), 3)
            return amount_yi, volume_lots
        except Exception as exc:  # noqa: BLE001 - 单对失败不打断整批
            last_error = exc
            if attempt < RETRY_TIMES:
                time.sleep(0.5 + attempt * 0.5)
    log(f"失败 {date} {code}: {last_error!r}")
    return None


def new_source() -> "AuctionSource":
    """每个线程各自的 AuctionSource（各自一个 TdxClient，不共享 client）。"""
    return AuctionSource()


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    parser = argparse.ArgumentParser(description="回补 120 天连板梯队的 09:25 竞价成交额")
    parser.add_argument("--resume", action="store_true", default=True, help="断点续跑（默认开启）")
    parser.add_argument("--no-resume", dest="resume", action="store_false", help="忽略已有文件从头采")
    parser.add_argument("--days", type=int, default=None, help="只跑最近 N 个交易日（试跑用）")
    parser.add_argument("--codes", default=None, help="只跑这些 6 位代码，逗号分隔（调试用）")
    parser.add_argument("--workers", type=int, default=4, help="并发线程数，默认 4")
    parser.add_argument("--out", default=None, help="成交额输出文件，缺省 data/auction_history.json")
    parser.add_argument(
        "--out-volume", default=None, help="成交量输出文件，缺省 data/auction_volume_history.json"
    )
    args = parser.parse_args(argv)

    out_path = Path(args.out).resolve() if args.out else OUT
    vol_path = Path(args.out_volume).resolve() if args.out_volume else VOL
    codes = {c.strip() for c in (args.codes or "").split(",") if c.strip()} or None
    workers = max(1, min(int(args.workers or 1), 16))

    started = time.time()
    say(
        f"启动：ladder={LADDER} out={out_path} vol={vol_path} days={args.days} "
        f"codes={args.codes} workers={workers} resume={args.resume}"
    )

    dates, pairs = load_ladder_pairs(args.days, codes)
    data = load_existing(out_path) if args.resume else {}
    vol_data = load_existing(vol_path, 3) if args.resume else {}

    pending: dict[str, list[str]] = {}
    skipped = 0
    for date, code in pairs:
        # 两份都已采过才跳过；只缺成交量也要重采（升级前只补过金额的存量场景）
        if code in data.get(date, {}) and code in vol_data.get(date, {}):
            skipped += 1
            continue
        pending.setdefault(date, []).append(code)

    todo = sum(len(v) for v in pending.values())
    miss_amount = sum(1 for date, code in pairs if code not in data.get(date, {}))
    miss_volume = sum(1 for date, code in pairs if code not in vol_data.get(date, {}))
    say(f"目标交易日 {len(dates)} 天，总对数 {len(pairs)}，已有跳过 {skipped}，待采 {todo}")
    say(f"缺口：成交额 {miss_amount} 条，成交量 {miss_volume} 条")

    collected = 0
    vol_collected = 0
    failed: list[tuple[str, str]] = []
    state_lock = threading.Lock()

    def dump() -> None:
        with state_lock:
            dump_atomic(data, out_path)
            dump_atomic(vol_data, vol_path)

    def run_chunk(date: str, chunk: list[str], slot: int) -> None:
        nonlocal collected, vol_collected
        source = new_source()
        broken = 0  # 连续失败计数：客户端疑似坏掉时整条重连
        try:
            for code in chunk:
                got = fetch_one(source, code, date)
                with state_lock:
                    if got is None:
                        failed.append((date, code))
                    else:
                        amount_yi, volume_lots = got
                        # 金额：既有内容绝不覆盖（行为与旧版一致）
                        if code not in data.get(date, {}):
                            data.setdefault(date, {})[code] = amount_yi
                            collected += 1
                        # 成交量：已有值就跳过写入
                        if code not in vol_data.get(date, {}):
                            vol_data.setdefault(date, {})[code] = volume_lots
                            vol_collected += 1
                    done = collected + vol_collected
                    need_dump = done > 0 and done % DUMP_EVERY == 0
                broken = broken + 1 if got is None else 0
                time.sleep(random.uniform(0.05, 0.1))  # 限速：每线程每请求后小睡
                if broken >= 3:
                    try:
                        source.close()
                    except Exception:  # noqa: BLE001
                        pass
                    source = new_source()
                    broken = 0
                if need_dump:
                    dump()
        finally:
            try:
                source.close()
            except Exception:  # noqa: BLE001
                pass

    for date in sorted(pending):
        chunk_list = pending[date]
        if not chunk_list:
            continue
        say(f"采集 {date}：{len(chunk_list)} 只")
        size = max(1, min(workers, len(chunk_list)))
        chunks = [chunk_list[i::size] for i in range(size)]
        threads = [
            threading.Thread(target=run_chunk, args=(date, chunk, index), daemon=True)
            for index, chunk in enumerate(chunks)
            if chunk
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        dump()  # 每个交易日结束都落一次盘

    dump()
    elapsed = time.time() - started

    on_disk = load_existing(out_path)
    vol_on_disk = load_existing(vol_path, 3)
    code_count = len({code for row in on_disk.values() for code in row})
    vol_total = sum(len(row) for row in vol_on_disk.values())
    say("———— 汇总 ————")
    say(
        f"总对数={len(pairs)} 成交额成功={collected} 成交量新增={vol_collected} "
        f"跳过={skipped} 失败={len(failed)}"
    )
    if failed:
        for date, code in failed[:20]:
            say(f"  失败 {date} {code}")
        if len(failed) > 20:
            say(f"  …其余 {len(failed) - 20} 条见日志 {LOG}")
    say(f"成交额文件：日期数={len(on_disk)} 代码数={code_count} 文件={out_path}")
    say(
        f"成交量：本次新增={vol_collected} 条 / 总条数={vol_total} 条 "
        f"日期数={len(vol_on_disk)} 文件={vol_path}"
    )
    say(f"总耗时={elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
