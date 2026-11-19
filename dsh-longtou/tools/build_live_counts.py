#!/usr/bin/env python3
"""当日涨停/跌停家数实时统计，落盘 ``data/cycle/live_counts.json``。

数据来源（优先级从高到低，全部走 ``longtou/cycle_source.py`` 的双通路 ``_fetch_json``，
本脚本自己不发起裸 urllib 请求）：

1. **东财官方涨停池** ``getTopicZTPool`` / **跌停池** ``getTopicDTPool`` 的 ``tc`` 条数；
2. **全市场快照自算**（``push2/api/qt/clist/get`` + 涨停价反算，口径 B）作为交叉校验。

为什么不用「自算家数」当主口径：池子是东财对外一致口径，家数不随本地幅度规则猜；
自算口径按「昨收 × (1 ± 幅度) 四舍五入到分」反算，两份都写进 ``verify``，
不一致时原样输出差异清单，**不强行对齐**。

口径 B 的判定细节：

* 幅度：科创/创业（688/689/300/301/302）20%，北交所（920/43/83/87/88）30%，
  主板 ST（名称含 ``ST``，含 ``*ST``）5%，其余 10%；
* 涨停价 ``zt = round(昨收 × (1 + 幅度), 2)``，``|最新价 - zt| < 0.005`` 记涨停；
  跌停同理用 ``(1 - 幅度)``；
* 四舍五入用 ``Decimal ROUND_HALF_UP``（交易所口径）。Python 内建 ``round()`` 是银行家
  舍入，``round(11.055, 2)`` → 11.05，会把涨停价算低一分 → 这类股整批漏判。原型用
  ``+1e-9`` 打补丁只能治一部分，Decimal 是根治；
* 停牌（最新价/昨收缺失或 ≤0）跳过，不计入任何一侧。

已知口径差（20261106 实测，`--check 20261106` 可复现）：TDX 基准 33 / 东财涨停池 32 /
快照自算 32。差的那一只是 **600537 \\*ST亿晶**：

* 基准侧 ``tools/build_market_stats.py`` 走 ``longtou/tdx.py`` 的 ``limit_pct()``，
  那里只有 300/301/688/689→20%、其余→10%，**没有 ST 5% 概念**，且北交所根本不进样本；
* 600537 名称带 ``*ST``，但本地日线显示它连续多日按 ±10% 幅度成交
  （1.40 → 1.54 → 1.69 → 1.86 → 2.05，都是 10% 幅度四舍五入后的涨停价），
  即当日适用 10% 幅度而不是 ST 的 5%（退市整理类标的仍按 10% 幅度交易）；
* 东财涨停池也不收录该股 → 池子 32；按「主板 ST→5%」判定的快照也算不到它 → 32。
* 结论：这一只属于**口径差**（基准更宽），不是漏拉、不是漏股（快照 5927/5927 全取到）、
  也不是 ±1 分四舍五入误差。按 Lead 要求不为了让 ``--check`` 凑成 33 而改判定规则。

用法::

    .venv\\Scripts\\python.exe tools\\build_live_counts.py                  # 写 data/cycle/live_counts.json
    .venv\\Scripts\\python.exe tools\\build_live_counts.py --date 20261106  # 指定日期
    .venv\\Scripts\\python.exe tools\\build_live_counts.py --check 20261106 # 只校准、不写盘

退出码：0 正常；1 取数失败（**失败不写文件**，绝不落脏数据）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longtou import cycle_source  # noqa: E402

OUT = ROOT / "data" / "cycle" / "live_counts.json"
MARKET_STATS = ROOT / "data" / "market_stats.json"


def build_counts(date: str | None) -> dict | None:
    """取一次当日家数；取数失败（连一天的家数都没有）返回 None。"""
    return cycle_source.live_limit_counts(date)


def write_counts(payload: dict) -> Path:
    """原子落盘（先写 .tmp 再替换），避免面板读到半截文件。"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = OUT.with_suffix(OUT.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(OUT)
    return OUT


def _pct_text(value) -> str:
    return f"{value:+.2f}%" if isinstance(value, (int, float)) else "—"


def _secid(code: str) -> str:
    """代码 → 东财 secid（1=沪，0=深/北）。北交所用 0 前缀，不能按首位是 9 判沪。"""
    code = str(code)
    if code.startswith(("920", "43", "83", "87", "88")):
        return "0." + code
    return ("1." if code.startswith("6") else "0.") + code


def _quote_detail(code: str) -> dict:
    """查单只股票的现价/昨收/涨跌幅，用于给差异个股补明细。

    **只在 ``--check`` 校准模式里调用**（差异通常 1 只，多一条请求无妨）；
    日常写盘路径不会走这里。取不到就返回空字段，不抛异常。
    """
    url = (
        "https://push2.eastmoney.com/api/qt/stock/get"
        f"?secid={_secid(code)}&fltt=2&invt=2&fields=f43,f58,f60,f170"
    )
    data = (cycle_source._fetch_json(url) or {}).get("data") or {}
    cycle_source._sleep()
    return {
        "code": code,
        "name": data.get("f58"),
        "price": data.get("f43"),
        "prev": data.get("f60"),
        "pct": data.get("f170"),
    }


def report_check(payload: dict, date: str) -> int:
    """``--check``：与 market_stats 基准对比，打印差异明细（不做对齐）。"""
    try:
        stats = json.loads(MARKET_STATS.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] 读不了 market_stats.json：{exc}")
        return 1
    row = (stats or {}).get(date) or {}
    base_up = row.get("limit_up")
    base_down = row.get("limit_down")
    base_codes = set()
    for codes in (row.get("height_stocks") or {}).values():
        base_codes.update(str(c) for c in codes)

    verify = payload.get("verify") or {}
    pool_up, pool_down = verify.get("zt_pool_count"), verify.get("dt_pool_count")
    snap_up, snap_down = verify.get("snapshot_up"), verify.get("snapshot_down")
    got_up = set(payload.get("up_codes") or [])
    got_down = set(payload.get("down_codes") or [])

    print(f"日期 {date}（fetched_at {payload.get('fetched_at')}，source {payload.get('source')}）")
    print(f"  快照拉取：{verify.get('snapshot_rows')} / total={verify.get('snapshot_total')}")
    print(f"  涨停：东财涨停池 {pool_up} / 快照自算 {snap_up} / 落盘值 {payload.get('limit_up')}")
    print(f"  跌停：东财跌停池 {pool_down} / 快照自算 {snap_down} / 落盘值 {payload.get('limit_down')}")
    print(f"  基准（market_stats）：涨停 {base_up} / 跌停 {base_down}")
    print()

    # 差异明细优先查池子清单，池子里没有（例如只在快照里出现的股）再查快照自算清单
    detail = {}
    for source in ("snapshot_down_detail", "snapshot_up_detail"):
        for item in (verify.get(source) or []):
            detail[str(item.get("code"))] = item
    for item in (payload.get("up_detail") or []) + (payload.get("down_detail") or []):
        detail[str(item.get("code"))] = item

    up_diff = sorted(base_codes - got_up)
    same_up = payload.get("limit_up") == base_up
    same_down = payload.get("limit_down") == base_down
    print(f"涨停：落盘值 vs 基准 → {'一致' if same_up else '相差 %+d' % ((payload.get('limit_up') or 0) - (base_up or 0))}")
    print(f"跌停：落盘值 vs 基准 → {'一致' if same_down else '相差 %+d' % ((payload.get('limit_down') or 0) - (base_down or 0))}")
    if up_diff:
        print("  基准有、本次没有（口径差，待 Lead 判定，不强行对齐）：")
        for code in up_diff:
            item = detail.get(code) or _quote_detail(code)
            print(f"    {code} {item.get('name') or '?'} 涨幅 {_pct_text(item.get('pct'))}"
                  f" 现价 {item.get('price')} 昨收 {item.get('prev')}")
    else:
        print("  涨停集合与基准代码完全一致")
    if got_down:
        print(f"  跌停清单 {len(got_down)} 只：{'、'.join(sorted(got_down))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    parser = argparse.ArgumentParser(description="当日涨停/跌停家数（东财池子 + 快照自算交叉校验）")
    parser.add_argument("--date", default=None, help="交易日 YYYYMMDD，缺省取 market_stats 最新交易日")
    parser.add_argument("--check", metavar="YYYYMMDD", default=None, help="只做校准：与 market_stats 对比并打印差异，不写文件")
    args = parser.parse_args(argv)

    if args.check and (len(args.check) != 8 or not args.check.isdigit()):
        print("--check 需要 YYYYMMDD（8 位数字）")
        return 1
    date = args.check or args.date
    payload = build_counts(date)
    if payload is None or payload.get("limit_up") is None and payload.get("limit_down") is None:
        print("[错误] 取数失败：涨停池/跌停池/快照全部没返回有效数据，不写文件（避免脏数据）")
        return 1
    payload["fetched_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    payload["source"] = payload.get("source") or "eastmoney_push2"

    if args.check:
        return report_check(payload, args.check)

    path = write_counts(payload)
    verify = payload.get("verify") or {}
    print(f"date={payload['date']} 涨停={payload['limit_up']} 跌停={payload['limit_down']} "
          f"source={payload['source']}")
    print(f"校验：涨停池 {verify.get('zt_pool_count')} / 快照自算 {verify.get('snapshot_up')}；"
          f"跌停池 {verify.get('dt_pool_count')} / 快照自算 {verify.get('snapshot_down')}；"
          f"快照 {verify.get('snapshot_rows')}/{verify.get('snapshot_total')}")
    print(f"明细：涨停 {len(payload.get('up_codes') or [])} 只、跌停 {len(payload.get('down_codes') or [])} 只")
    print(f"latest={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
