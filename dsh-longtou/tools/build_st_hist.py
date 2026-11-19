#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从本地 TDX 日线反解「当日板限档位」，生成 ``data/st_hist.json``。

背景：TDX ``.day`` 是**原始不复权**价，涨跌停档位（5%/10%/20%/30%）只能靠
「当日收盘是否正好收在某个档位的涨跌停价上」反解。但朴素的逐日
``close == limit_price(prev, pct)`` 会大量假命中：一只 10% 档股票某天恰好
收在 +5.00% 且收在当日最高价时，会被误判成「ST 5% 档」。实测 300 只随机票
2025 年就有 143 条 5% 档假命中，远超真实 ST 命中，不能直接用。

因此本脚本只承认**成段**的 5% 档证据（ST 时段），规则：

1. 逐 bar 判「宽幅」：当日振幅超出 5% 档物理上限即 ``wide``——因为个股当日
   最高/最低价不可能越过自己的涨跌停价，故 ``wide`` 当日**不可能**是 5% 档
   （低价股 5% 价按分取整可显示到 5.5%+，阈值按 ``0.05 + 0.005/prev + 0.002``
   自适应放宽）。
2. 连续非 ``wide`` 的 bar 构成一个「候选段」；段内 ``close == high == 5% 涨停价``
   或 ``close == low == 5% 跌停价`` 记一次 5% 档摸板（只认收在当日极值的情况）。
3. 只有同时满足「段长 ≥ 20 个交易日」「段内 5% 档摸板 ≥ 2 次」「摸板密度
   ≥ 2%」的段才认定为 ST 时段，段内**每一天**都记 ``band=5``。
   真正的 ST 时段摸板非常密集（样本里 13%/10% 密度），而 10% 档股票的
   巧合命中段密度约 0.4%，两者可分；单点巧合不会成段。
4. 对「当前名称含 ST」的主板票，**未被 ST 段覆盖的每一个交易日**都记
   ``band=10``：这是「当日非 ST（按 10% 档）」的硬证据。不能只写宽幅日
   （振幅 > 5% 档上限）——普通交易日一旦留下 ``band=None`` 空洞，下游
   ``build_market_stats._is_st_today`` 就会回退到「当前名称」判 ST，把现名带
   ST 的票的历史普通日整段剔除（2026-07 600491 的梯队从 3 板起跳就是这个洞）。
   ``band=5`` 的日期优先，不会被 ``band=10`` 覆盖。

不判档的情况（不猜、跳过）：段长/密度不达标的 5% 档命中、段外的普通日、
无法解释的除权缺口。创业/科创（300/301/302/688/689）ST 仍是 20%，档位无法
用 5% 档区分，本文件不收录，由消费方沿用名称口径。

输出格式（O(1) 查询「某日某票档位」）::

    {"meta": {...}, "days": {"20250103": {"600289": 5}, ...}}

``band`` 单位是百分数（5/10/20/30）。``band=5`` 即当日 ST；``band=10`` 是
主板当日非 ST 的硬证据（**含普通交易日**，不是只写宽幅日）。

Run:
    python tools/build_st_hist.py                      # 全量 2021-01-01..2026-12-31
    python tools/build_st_hist.py --codes 600289,000010  # 可行性样本（只分析这些票）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longtou.tdx import TDX_VIPDOC, list_stocks, read_day_file  # noqa: E402

OUT_PATH = ROOT / "data" / "st_hist.json"
NAMES_PATH = ROOT / "data" / "stock_names.json"

TIERS = (0.05, 0.10, 0.20, 0.30)
# 创业/科创：ST 仍是 20%，5% 档判据不适用 ⇒ 本文件不收录。
GROWTH_PREFIX = ("300", "301", "302", "688", "689")
HIT_TOL = 1e-6
# ST 时段判据：段长下限 / 段内 5% 档摸板次数下限 / 摸板密度下限。
ST_RUN_MIN_LEN = 20
ST_RUN_MIN_HITS = 2
ST_RUN_MIN_DENSITY = 0.02


def _wide_tol(prev: float) -> float:
    """当日「不可能处于 5% 档」的振幅阈值（按分取整自适应，见模块注释）。"""
    if prev <= 0:
        return 0.056
    return 0.05 + 0.005 / prev + 0.002


def _is_st_name(name: str | None) -> bool:
    """名称含 ST / *ST（含退市整理期）即视为 ST 个股；空名不算。"""
    return "ST" in "".join(str(name or "").split()).upper()


def _hit_tiers(bar, prev: float) -> tuple[int, ...]:
    """当日「收在档位涨/跌停价上」的档位集合（只认收在当日极值）。"""
    hits: set[int] = set()
    if abs(bar.high - bar.close) < HIT_TOL:
        for pct in TIERS:
            if abs(bar.close - round(prev * (1.0 + pct) + 1e-9, 2)) < HIT_TOL:
                hits.add(int(round(pct * 100)))
    if abs(bar.low - bar.close) < HIT_TOL:
        for pct in TIERS:
            if abs(bar.close - round(prev * (1.0 - pct) + 1e-9, 2)) < HIT_TOL:
                hits.add(int(round(pct * 100)))
    return tuple(sorted(hits))


def _is_s_not_st(name: str | None) -> bool:
    """S 股（未股改，如 600182 S佳通）：5% 涨跌幅档但不是 ST。

    用户定稿口径是「剔当日 ST」，S 股必须保留；名称含 ST 的一律不享受本豁免
    （'ST'、'*ST'、'SST'、'S*ST' 都含 ST 子串，统一仍按 ST 处理）。
    """
    text = "".join(str(name or "").split()).upper()
    return text.startswith("S") and "ST" not in text


def analyze_stock(code: str, bars, name: str | None, start: int, end: int) -> dict:
    """单票反解：返回 {date_str: band} 与诊断计数。"""
    growth = code.startswith(GROWTH_PREFIX)
    recs: list[dict] = []
    prev = None
    n_ambiguous = 0
    for bar in bars:
        if prev is None or prev <= 0:
            prev = bar.close
            continue
        rng = max(abs(bar.high / prev - 1.0), abs(bar.low / prev - 1.0))
        hits = _hit_tiers(bar, prev)
        if len(hits) > 1:
            # 低价股按分取整会让两档同价（罕见）：不猜，丢弃该 bar 的档位证据。
            n_ambiguous += 1
            hits = ()
        recs.append(
            {
                "date": bar.date,
                "wide": rng > _wide_tol(prev),
                "hits": hits,
                "rng": rng,
            }
        )
        prev = bar.close

    out: dict[str, int] = {}
    diag = {
        "ambiguous": n_ambiguous,
        "hits": defaultdict(int),       # 档位 -> 命中次数（全历史）
        "hit5_untagged": 0,             # 5% 档命中未落入合格 ST 时段（噪声/短段）
        "runs": [],                     # 合格 ST 时段（诊断用）
        "band10_days": 0,               # band=10 条数（非 ST 段覆盖的全部交易日）
        "band5_days": 0,
        "growth_skipped": growth,
    }
    if growth:
        return {"days": out, "diag": diag}

    for rec in recs:
        for tier in rec["hits"]:
            diag["hits"][tier] += 1

    # 连续非 wide 段（跨停牌按相邻 bar 处理）。
    segments: list[tuple[int, int]] = []
    i = 0
    n = len(recs)
    while i < n:
        if recs[i]["wide"]:
            i += 1
            continue
        j = i
        while j < n and not recs[j]["wide"]:
            j += 1
        segments.append((i, j))
        i = j

    # S 股（如 600182 S佳通）同样是 5% 涨跌幅档，但不是 ST；定稿口径只剔「当日 ST」，
    # 故名称以 S 开头且不含 ST 的票不做 band=5 标记（交给名称口径按非 ST 放行）。
    s_not_st = _is_s_not_st(name)
    st_dates: set[int] = set()
    for lo, hi in segments:
        seg = recs[lo:hi]
        n5 = sum(1 for rec in seg if 5 in rec["hits"])
        length = hi - lo
        density = n5 / length if length else 0.0
        qualified = (
            length >= ST_RUN_MIN_LEN
            and n5 >= ST_RUN_MIN_HITS
            and density >= ST_RUN_MIN_DENSITY
        )
        if not qualified:
            diag["hit5_untagged"] += n5
            continue
        for rec in seg:
            st_dates.add(rec["date"])
            if start <= rec["date"] <= end and not s_not_st:
                out[str(rec["date"])] = 5
        diag["runs"].append(
            {
                "start": seg[0]["date"],
                "end": seg[-1]["date"],
                "len": length,
                "n5": n5,
                "density": round(density, 3),
            }
        )

    # 现名 ST 的主板票：band=10 是「当日非 ST（10% 档）」的硬证据。
    # 必须**每个未被 ST 段覆盖的交易日**都写，不能只写宽幅日：普通交易日一旦留
    # band=None 空洞，下游 build_market_stats._is_st_today 就会回退「当前名称」判 ST，
    # 把现名带 ST 的票在历史普通日全部当 ST 剔除（600491 在 20260706~0709 的
    # 1/2 板缺失、梯队从 3 板起跳就是这个洞）。band=5 优先：st_dates 里的日期一律
    # 不覆盖，S 股豁免（_is_s_not_st）的日期同样落在 st_dates 里，不会被 10 盖掉。
    name_st = _is_st_name(name)
    if name_st:
        for rec in recs:
            date_i = rec["date"]
            if not (start <= date_i <= end) or date_i in st_dates:
                continue
            out[str(date_i)] = 10
            diag["band10_days"] += 1
    diag["band5_days"] = sum(1 for value in out.values() if value == 5)
    return {"days": out, "diag": diag}


def _print_sample(diag_by_code: dict[str, dict], names: dict[str, str]) -> None:
    """样本票逐只诊断：每年档位分布、ST 时段明细、段外噪声、相邻日档位跳变。"""
    print("=" * 88, flush=True)
    print("样本票逐只诊断（档位=收在档位极值价上的次数）", flush=True)
    for code in sorted(diag_by_code):
        diag = diag_by_code[code]
        name = names.get(code, "")
        if diag.get("growth_skipped"):
            print(f"{code} {name}: 创业/科创，5% 档判据不适用，跳过", flush=True)
            continue
        print(f"\n{code} {name}  命中={dict(diag['hits'])}  ambiguous={diag['ambiguous']}", flush=True)
        print(f"  合格 ST 段={len(diag['runs'])}  band5 天数={diag['band5_days']}  "
              f"band10(非ST日)={diag['band10_days']}  段外 5% 噪声={diag['hit5_untagged']}", flush=True)
        for run in diag["runs"]:
            print(f"  ST 段 {run['start']}..{run['end']} len={run['len']} "
                  f"n5={run['n5']} density={run['density']}", flush=True)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=20210101)
    parser.add_argument("--end", type=int, default=20261231)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    parser.add_argument(
        "--codes",
        default="",
        help="只分析这些代码（逗号分隔），用于可行性样本核对。",
    )
    args = parser.parse_args()

    try:
        names = json.loads(NAMES_PATH.read_text(encoding="utf-8"))
    except Exception:
        names = {}
    if not isinstance(names, dict):
        names = {}

    stocks = list_stocks(TDX_VIPDOC)
    if args.codes:
        # 代码一律按字符串处理并补足 6 位（避免 CLI/解析把 000010 这类代码丢前导 0）。
        wanted = {c.strip().zfill(6) for c in args.codes.split(",") if c.strip()}
        stocks = [s for s in stocks if s.code in wanted]
        missing = wanted - {s.code for s in stocks}
        if missing:
            print(f"[warn] 这些代码不在 list_stocks 里：{sorted(missing)}", flush=True)

    days: dict[str, dict[str, int]] = defaultdict(dict)
    band_counts = {5: 0, 10: 0, 20: 0, 30: 0}
    per_year_5 = defaultdict(int)          # 年份 -> band5 明细条数
    per_year_5_codes: dict[int, set] = defaultdict(set)
    diag = {
        "codes": 0,
        "growth_skipped": 0,
        "read_fail": 0,
        "ambiguous": 0,
        "hits": defaultdict(int),
        "hit5_untagged": 0,
        "runs": 0,
        "band10_days": 0,
    }
    sample_diag: dict[str, dict] = {}
    t0 = time.time()
    stocks = list(stocks)
    for index, stock in enumerate(stocks, 1):
        try:
            bars = read_day_file(stock.path)
        except Exception as exc:  # noqa: BLE001 - 单票坏文件不阻断全量
            diag["read_fail"] += 1
            print(f"[warn] 读日线失败 {stock.full_code}: {exc}", flush=True)
            continue
        name = names.get(stock.code)
        result = analyze_stock(stock.code, bars, name, args.start, args.end)
        diag["codes"] += 1
        if result["diag"]["growth_skipped"]:
            diag["growth_skipped"] += 1
        for code_date, band in result["days"].items():
            days[code_date][stock.code] = band
            band_counts[band] = band_counts.get(band, 0) + 1
            if band == 5:
                year = int(code_date[:4])
                per_year_5[year] += 1
                per_year_5_codes[year].add(stock.code)
        sub = result["diag"]
        diag["ambiguous"] += sub["ambiguous"]
        diag["hit5_untagged"] += sub["hit5_untagged"]
        diag["runs"] += len(sub["runs"])
        diag["band10_days"] += sub["band10_days"]
        for tier, count in sub["hits"].items():
            diag["hits"][tier] = diag["hits"].get(tier, 0) + count
        if args.codes:
            sample_diag[stock.code] = sub
        if index % 500 == 0:
            print(f"... 已处理 {index}/{len(stocks)} 只 ({time.time()-t0:.1f}s)", flush=True)

    if args.codes:
        _print_sample(sample_diag, names)
        return 0

    days_sorted = {key: dict(sorted(value.items())) for key, value in sorted(days.items())}
    payload = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "start": args.start,
            "end": args.end,
            "method": "close==档位极值价 反解；5% 档只认成段证据（len>=20, n5>=2, density>=2%）",
            "note": "band=5 即当日 ST；band=10 为现名 ST 主板票当日非 ST 的硬证据（含普通交易日）；创业/科创不收",
            "days": len(days_sorted),
            "entries": sum(len(v) for v in days_sorted.values()),
            "band_counts": {str(k): v for k, v in sorted(band_counts.items()) if v},
            "st_codes_total": len({code for values in days_sorted.values() for code in values}),
            "st_runs": diag["runs"],
            "hit5_untagged": diag["hit5_untagged"],
            "ambiguous": diag["ambiguous"],
        },
        "days": days_sorted,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_name(f"{args.out.name}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(args.out)
    size_mb = args.out.stat().st_size / 1e6
    print(
        f"st_hist 写出 {args.out} {size_mb:.2f}MB  days={payload['meta']['days']} "
        f"entries={payload['meta']['entries']} band_counts={payload['meta']['band_counts']} "
        f"st_codes={payload['meta']['st_codes_total']}",
        flush=True,
    )
    print(
        f"扫描 {diag['codes']} 只（跳过创业/科创 {diag['growth_skipped']}，读失败 {diag['read_fail']}）"
        f" 全历史档位命中={dict(diag['hits'])} ambiguous={diag['ambiguous']} "
        f"段外5%噪声={diag['hit5_untagged']} ST时段数={diag['runs']} band10条={diag['band10_days']} "
        f"耗时={time.time()-t0:.1f}s",
        flush=True,
    )
    print("每年 band5：", flush=True)
    for year in sorted(per_year_5):
        print(f"  {year}: 条数={per_year_5[year]} 股票数={len(per_year_5_codes[year])}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
