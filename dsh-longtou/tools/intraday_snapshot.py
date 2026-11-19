#!/usr/bin/env python3
"""盘中快照：让面板在 09:25 / 11:30 / 13:00 三个时点也显示「当天数据」。

背景与口径
----------
``cycle_collect.py`` 的基准日是 ``market_stats.json`` 的最大交易日，而 ``market_stats``
只由收盘后的 TDX 本地 ``.day`` 重建，所以盘中的 cycle 永远停在 T-1。本模块不走
``market_stats``，改用**东财实时接口**（``longtou/cycle_source.py`` 里已有的封装）取当天
盘中值，落盘 ``data/intraday_snapshot.json``，再把「当日行」并入
``data/cycle/latest.json``：

* ``series`` 追加/替换当日一行（``intraday: true`` + ``asof`` + ``phase``）；
* ``ladder`` 追加/替换当日一行（同日涨停池按连板数 ``lbc`` 分组得到的梯队）；
* 顶层 ``date`` / ``indices`` / ``indices_date`` / ``amount`` / ``breadth`` / ``temperature``
  / ``boards_today`` 换成盘中实时值，并打上 ``intraday / asof / phase`` 标记。

历史行的处理是**只读**的：本模块从不修改、不重排、不裁剪任何非当日行。

15:00 覆盖
----------
close 阶段 ``cycle_collect.py`` 已用正式源（market_stats）重建整份 payload，当日行是正式值；
随后用 ``--strip`` 兜底把残留的 ``intraday / asof / phase`` 标记清掉（正常情况下 cycle_collect
重建后本来就没有标记，``--strip`` 是幂等的安全网）。

**官方行优先**：如果当日行已存在且没有 ``intraday`` 标记（说明收盘重建已经跑过），
merge 直接跳过、不覆盖，避免盘中逻辑把收盘正式值刷成实时值。

阶段口径
--------
* ``open``（09:25 竞价后）：指数/成交额/涨跌家数/涨停跌停家数（快照反算）可用；
  东财涨停池此时通常还没生成，**最高板高度与梯队不可用**（标注 ``ladder: None``）。
* ``noon``（11:30）/``pm``（13:00）：涨停池有当日明细（含连板数 lbc）→ 梯队与最高板高度可算；
  ``height_ex``（最高板里剔除一字板的那层）盘中不可算，留 null。
* F10 天梯盘中无权威值；``suspended_*``（停牌高度）盘中不可算，留 null。

用法::

    .venv\\Scripts\\python.exe tools\\intraday_snapshot.py --phase noon
    .venv\\Scripts\\python.exe tools\\intraday_snapshot.py --phase close --strip
    .venv\\Scripts\\python.exe tools\\intraday_snapshot.py --self-test
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CN = timezone(timedelta(hours=8), "Asia/Shanghai")
CYCLE_LATEST = ROOT / "data" / "cycle" / "latest.json"
SNAPSHOT_PATH = ROOT / "data" / "intraday_snapshot.json"

# 盘中标记字段：merge 时写入、strip 时清除
MARK_KEYS = ("intraday", "asof", "phase")

# 盘中不计入/不覆盖的顶层字段：这些由正式源（cycle_collect）维护
OFFICIAL_TOP_KEYS = ("sector_top",)


# --------------------------------------------------------------------------
# 读写
# --------------------------------------------------------------------------
def read_json(path: Path) -> dict:
    """读 JSON 对象；文件缺失/半截/不是对象一律返回 {}（与面板容错口径一致）。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 缺文件 / 半截 JSON 都不该打断盘中流程
        return {}
    return raw if isinstance(raw, dict) else {}


def write_json_atomic(path: Path, payload: dict) -> None:
    """先写 .tmp 再 os.replace：面板每 60 秒轮询，绝不能读到半截 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# 盘中取数（每个来源独立容错，取不到就记 unavailable，绝不臆造数值）
# --------------------------------------------------------------------------
def _levels_from_pool(pool: list) -> list:
    """当日涨停池 → 梯队：按连板数 boards 分组，高度从高到低。

    池子里每只票都带东财 ``lbc``（连板数），所以盘中的「当前连板高度分层」就是
    同时封住涨停的各高度层；炸板票不在池内，其高度不参与当日梯队（口径与收盘后
    由 F10 天梯给出的梯队不同，见报告）。
    """
    groups: dict[int, list] = {}
    for item in pool or []:
        if not isinstance(item, dict):
            continue
        boards = item.get("boards")
        try:
            boards = int(boards)
        except (TypeError, ValueError):
            continue
        if boards <= 0:
            continue
        groups.setdefault(boards, []).append(
            {
                "code": item.get("code"),
                "name": item.get("name"),
                # 竞价量比相关字段盘中不可得（需要 09:25 竞价快照与昨日成交量），置 None 保持结构稳定
                "auction_yi": None,
                "auction_volume_lots": None,
                "prev_volume": None,
                "volume_ratio": None,
            }
        )
    return [{"boards": boards, "stocks": groups[boards]} for boards in sorted(groups, reverse=True)]


def collect_fields(date: str, phase: str) -> dict:
    """取盘中实时字段。返回 ``{"fields": {...}, "unavailable": [...], "source": {...}}``。"""
    from longtou import cycle_source as cs

    fields: dict = {
        "max_height": None,
        "height_ex": None,
        "limit_up": None,
        "limit_down": None,
        "limit_counts_source": None,
        "breadth": None,
        "amount": None,
        "indices": None,
        "temperature": None,
        "levels": [],
        "boards_today": [],
        "suspended_height": None,
    }
    unavailable: list[str] = []

    # 1) 指数（东财实时）
    try:
        indices = cs.fetch_index_quotes() or []
        if indices:
            fields["indices"] = indices
        else:
            unavailable.append("indices: 东财实时指数返回空")
    except Exception as exc:  # noqa: BLE001 - 单个数据源失败不影响其它字段
        unavailable.append(f"indices: {type(exc).__name__}: {exc}")

    # 2) 成交额（东财实时）
    try:
        amount = cs.fetch_market_amount()
        if isinstance(amount, dict) and amount:
            fields["amount"] = amount
        else:
            unavailable.append("amount: 东财实时成交额返回空")
    except Exception as exc:  # noqa: BLE001
        unavailable.append(f"amount: {type(exc).__name__}: {exc}")

    # 3) 涨跌家数（东财实时快照；09:25 为竞价涨跌家数）
    try:
        breadth = cs.fetch_breadth()
        if isinstance(breadth, dict) and breadth:
            fields["breadth"] = breadth
        else:
            unavailable.append("breadth: 东财实时涨跌家数返回空")
    except Exception as exc:  # noqa: BLE001
        unavailable.append(f"breadth: {type(exc).__name__}: {exc}")

    # 4) 涨停/跌停家数：官方池子优先，池子空则用全市场快照反算（竞价阶段即「竞价涨停家数」）
    try:
        counts = cs.live_limit_counts(date)
        if isinstance(counts, dict):
            fields["limit_up"] = counts.get("limit_up")
            fields["limit_down"] = counts.get("limit_down")
            fields["limit_counts_source"] = counts.get("source")
            fields["limit_counts_verify"] = counts.get("verify")
        else:
            unavailable.append("limit_up/limit_down: 涨停跌停池与全市场快照都取不到")
    except Exception as exc:  # noqa: BLE001
        unavailable.append(f"limit_up/limit_down: {type(exc).__name__}: {exc}")

    # 5) 涨停池 → 当日梯队 + 最高板高度（11:30 / 13:00 可用；09:25 竞价阶段通常为空）
    try:
        pool = cs.fetch_zt_pool(date) or []
        if pool:
            fields["boards_today"] = pool
            fields["levels"] = _levels_from_pool(pool)
            boards = [int(item.get("boards") or 0) for item in pool if isinstance(item, dict)]
            fields["max_height"] = max(boards) if boards else None
        elif phase == "open":
            unavailable.append("levels/max_height: 09:25 竞价阶段东财涨停池尚未生成（11:30 起可得）")
        else:
            unavailable.append("levels/max_height: 东财当日涨停池返回空")
    except Exception as exc:  # noqa: BLE001
        unavailable.append(f"levels/max_height: {type(exc).__name__}: {exc}")

    # 6) 情绪温度：复用 cycle_source.compute_temperature（口径与收盘一致）
    if fields["max_height"] is not None or fields["limit_up"] is not None:
        try:
            row = {
                "max_height": fields["max_height"],
                "limit_up": fields["limit_up"],
                "limit_down": fields["limit_down"],
                "height_ex": fields["height_ex"],
            }
            fields["temperature"] = cs.compute_temperature(row)
        except Exception as exc:  # noqa: BLE001 - 温度只是展示量，失败给 None
            unavailable.append(f"temperature: {type(exc).__name__}: {exc}")

    # 盘中不可算字段：明确列出原因，不填猜测值
    unavailable.append("height_ex: 需「当日最高板里剔除一字板」判定，盘中无权威源，留 null")
    unavailable.append("suspended_height/suspended_codes: 停牌高度依赖收盘后 build_suspended_stats，盘中不可算")
    unavailable.append("F10 天梯: 盘中无当日权威值（梯队改由东财涨停池 lbc 连板数分组）")

    return {
        "fields": fields,
        "unavailable": unavailable,
        "source": {
            "indices": "eastmoney_push2",
            "amount": "eastmoney_push2",
            "breadth": "eastmoney_push2",
            "limit_counts": fields.get("limit_counts_source"),
            "ladder": "eastmoney_getTopicZTPool(lbc)",
        },
    }


def build_snapshot(date: str, asof: str, phase: str) -> dict:
    """组装 ``data/intraday_snapshot.json`` 的内容。"""
    gathered = collect_fields(date, phase)
    return {
        "date": date,
        "asof": asof,
        "phase": phase,
        "fields": gathered["fields"],
        "unavailable": gathered["unavailable"],
        "source": gathered["source"],
    }


# --------------------------------------------------------------------------
# 并入 cycle/latest.json
# --------------------------------------------------------------------------
def _replace_or_append(rows: list, date: str, new_row: dict) -> list:
    """把 date 那一行替换成 new_row；没有就按日期顺序插入。其余行逐字不动。"""
    out = [
        row
        for row in rows
        if not (isinstance(row, dict) and str(row.get("date")) == date and row.get("intraday"))
    ]
    idx = len(out)
    for i, row in enumerate(out):
        if isinstance(row, dict) and str(row.get("date")) == date:
            # 同日正式行：调用方已保证不会走到这里（official 优先），保险起见原位替换
            out[i] = new_row
            return out
        if isinstance(row, dict) and str(row.get("date")) > date:
            idx = i
            break
    out.insert(idx, new_row)
    return out


def _today_official(payload: dict, date: str) -> bool:
    """当日行已存在且没有 intraday 标记 → 认为是收盘正式行。"""
    series = payload.get("series") or []
    for row in reversed(series):
        if isinstance(row, dict) and str(row.get("date")) == date:
            return not row.get("intraday")
        if isinstance(row, dict) and str(row.get("date")) < date:
            break
        # 序列按时间升序，理论上末尾就是最新；继续往前找当日行
    return False


def merge_into_cycle(payload: dict, snapshot: dict) -> tuple[dict, str]:
    """把盘中快照并入 cycle payload。返回 ``(新 payload, 结论)``。

    结论：``merged``（已并入）/ ``keep-official``（当日正式行已存在，不动）/
    ``no-cycle``（cycle 文件缺失）/ ``no-live-data``（一个实时字段都没取到，不覆盖）。
    """
    fields = snapshot.get("fields") or {}
    if not isinstance(payload, dict) or not payload:
        return payload, "no-cycle"
    live = [
        fields.get("indices"),
        fields.get("amount"),
        fields.get("breadth"),
        fields.get("limit_up"),
        fields.get("limit_down"),
        fields.get("max_height"),
    ]
    if all(value in (None, [], {}) for value in live):
        return payload, "no-live-data"

    date = str(snapshot.get("date") or "")
    asof = snapshot.get("asof")
    phase = snapshot.get("phase")

    if _today_official(payload, date):
        return payload, "keep-official"

    new = copy.deepcopy(payload)
    # ---- 顶层实时字段 ----
    new["date"] = date
    new["intraday"] = True
    new["asof"] = asof
    new["phase"] = phase
    if fields.get("indices"):
        new["indices"] = fields["indices"]
        new["indices_date"] = date
    if fields.get("amount") is not None:
        new["amount"] = fields["amount"]
    if fields.get("breadth") is not None:
        new["breadth"] = fields["breadth"]
    if fields.get("temperature") is not None:
        new["temperature"] = fields["temperature"]
    if fields.get("boards_today"):
        new["boards_today"] = fields["boards_today"]

    # ---- series 当日行（最高板折线 + 涨跌停家数）----
    breadth = fields.get("breadth") or {}
    up = breadth.get("up") if isinstance(breadth, dict) else None
    down = breadth.get("down") if isinstance(breadth, dict) else None
    series_row = {
        "date": date,
        "max_height": fields.get("max_height"),
        "height_ex": None,  # 盘中不可算，见 unavailable
        "limit_up": fields.get("limit_up"),
        "limit_down": fields.get("limit_down"),
        "up": up,
        "down": down,
        "suspended_height": None,
        "suspended_codes": None,
        "intraday": True,
        "asof": asof,
        "phase": phase,
    }
    new["series"] = _replace_or_append(list(payload.get("series") or []), date, series_row)

    # ---- ladder 当日行（梯队）：有连板数据才写，09:25 竞价阶段保留 T-1 梯队不动 ----
    levels = fields.get("levels") or []
    if levels:
        ladder_row = {
            "date": date,
            "max_height": fields.get("max_height"),
            "levels": levels,
            "intraday": True,
            "asof": asof,
            "phase": phase,
        }
        new["ladder"] = _replace_or_append(list(payload.get("ladder") or []), date, ladder_row)
    return new, "merged"


# --------------------------------------------------------------------------
# 15:00 覆盖：清掉盘中标记（当日行保留，数值已是正式源）
# --------------------------------------------------------------------------
def strip_intraday(payload: dict) -> tuple[dict, bool]:
    """去掉顶层与当日行里的 intraday/asof/phase 标记。幂等。返回 ``(payload, 是否有改动)``。"""
    if not isinstance(payload, dict) or not payload:
        return payload, False
    changed = False
    for key in MARK_KEYS:
        if key in payload:
            payload.pop(key, None)
            changed = True
    date = str(payload.get("date") or "")
    for coll in ("series", "ladder"):
        rows = payload.get(coll) or []
        for row in rows:
            if not isinstance(row, dict) or str(row.get("date")) != date:
                continue
            for key in MARK_KEYS:
                if key in row:
                    row.pop(key, None)
                    changed = True
    return payload, changed


def close_status(payload: dict) -> tuple[bool, str]:
    """close 阶段自检：当日行是否来自正式源（存在且无 intraday 标记）。"""
    date = str(payload.get("date") or "")
    if not date:
        return False, "cycle payload 缺 date"
    for row in payload.get("series") or []:
        if isinstance(row, dict) and str(row.get("date")) == date:
            if row.get("intraday"):
                return False, "当日行仍带 intraday 标记（正式重建可能没跑成功）"
            return True, f"当日行 {date} 为正式源（max_height={row.get('max_height')} limit_up={row.get('limit_up')}）"
    return False, f"series 里没有当日行 {date}（收盘重建可能停在上一交易日）"


# --------------------------------------------------------------------------
# 自测：全部在临时目录里的「真实形状副本」上做，绝不碰线上数据文件
# --------------------------------------------------------------------------
def self_test(out_dir: Path | None = None) -> int:
    import hashlib

    out_dir = out_dir or (ROOT / ".dsh-switchman" / "tmp" / "selftest_intraday")
    out_dir.mkdir(parents=True, exist_ok=True)
    real_path = CYCLE_LATEST
    real_bytes = real_path.read_bytes() if real_path.exists() else b""
    real_hash = hashlib.sha256(real_bytes).hexdigest()
    real = read_json(real_path)
    if not real:
        print("FAIL: 没有可用的 data/cycle/latest.json 做形状基准")
        return 1

    today = str(real.get("date") or "")
    series_all = list(real.get("series") or [])
    ladder_all = list(real.get("ladder") or [])
    # 造「盘中视角」基准：把当日行摘掉（模拟收盘重建还没跑的 T-1 状态）
    series_base = [r for r in series_all if str(r.get("date")) != today]
    ladder_base = [r for r in ladder_all if str(r.get("date")) != today]
    if not series_base:
        print("FAIL: latest.json 的 series 只有一行，没法构造 T-1 基准")
        return 1
    base = copy.deepcopy(real)
    base["date"] = str(series_base[-1]["date"])
    base["series"] = series_base
    base["ladder"] = ladder_base

    snap = {
        "date": today,
        "asof": "2026-11-17T11:30:05+08:00",
        "phase": "noon",
        "fields": {
            "max_height": 2,
            "height_ex": None,
            "limit_up": 19,
            "limit_down": 3,
            "breadth": {"up": 2100, "down": 2800, "flat": 80},
            "amount": {"total": 8000.1, "prev": 14844.9, "pct": -3.1},
            "indices": [{"name": "上证指数", "point": 3550.0, "pct": -1.0}],
            "temperature": 41.2,
            "levels": [{"boards": 2, "stocks": [{"code": "600001", "name": "测试二板"}]}],
            "boards_today": [{"code": "600001", "name": "测试二板", "boards": 2}],
        },
    }

    ok = 0
    checks: list[tuple[str, bool, str]] = []

    # ① 写入后当日行存在且 intraday=true / asof 正确，无重复行
    merged, reason = merge_into_cycle(base, snap)
    series = merged.get("series") or []
    ladder = merged.get("ladder") or []
    row = next((r for r in series if str(r.get("date")) == today), None)
    lrow = next((r for r in ladder if str(r.get("date")) == today), None)
    checks.append(("①merge 结论", reason == "merged", f"reason={reason}"))
    checks.append(
        (
            "①当日 series 行",
            bool(row) and row.get("intraday") is True and row.get("asof") == snap["asof"] and row.get("phase") == "noon",
            f"intraday={row.get('intraday') if row else None} asof={row.get('asof') if row else None} max_height={row.get('max_height') if row else None} limit_up={row.get('limit_up') if row else None}",
        )
    )
    checks.append(
        (
            "①当日 ladder 行",
            bool(lrow) and lrow.get("intraday") is True and (lrow.get("levels") or [{}])[0].get("boards") == 2,
            f"levels={len((lrow or {}).get('levels') or [])}",
        )
    )
    checks.append(
        (
            "①行的唯一性",
            len(series) == len(series_base) + 1 and len(ladder) == len(ladder_base) + 1,
            f"series {len(series_base)}→{len(series)} ladder {len(ladder_base)}→{len(ladder)}",
        )
    )
    checks.append(("①顶层标记与 date", merged.get("date") == today and merged.get("intraday") is True, f"date={merged.get('date')} intraday={merged.get('intraday')}"))

    # ③ 历史行逐字不动（series/ladder 前缀深比较 + 未触碰的顶层字段）
    hist_ok = series[: len(series_base)] == series_base and ladder[: len(ladder_base)] == ladder_base
    top_ok = merged.get("sector_top") == base.get("sector_top")
    checks.append(("③历史行与历史顶层字段未变", hist_ok and top_ok, f"series_hist={series[: len(series_base)] == series_base} ladder_hist={ladder[: len(ladder_base)] == ladder_base} sector_top={top_ok}"))

    # ④ 连跑两次幂等：再次 merge 结果与第一次完全一致
    again, reason2 = merge_into_cycle(merged, snap)
    checks.append(("④二次 merge 幂等", reason2 == "merged" and again == merged and len(again.get("series") or []) == len(series), f"reason={reason2} equal={again == merged} series={len(again.get('series') or [])}"))

    # 落盘 → 读回（原子写 + 形状保持）
    p = out_dir / "cycle_test.json"
    write_json_atomic(p, merged)
    checks.append(("④落盘读回一致", read_json(p) == merged, f"path={p}"))

    # ② close 覆盖：正式源重建后（当日行是正式值、无标记）→ strip 后标记消失、数值来自正式源
    official_row = next((r for r in series_all if str(r.get("date")) == today), None)
    official_lrow = next((r for r in ladder_all if str(r.get("date")) == today), None)
    if not official_row:
        print("FAIL: latest.json 当日行缺失，无法构造正式覆盖基准")
        return 1
    replaced = copy.deepcopy(merged)
    replaced["date"] = today
    replaced["series"] = [r for r in replaced["series"] if str(r.get("date")) != today] + [copy.deepcopy(official_row)]
    if official_lrow:
        replaced["ladder"] = [r for r in replaced["ladder"] if str(r.get("date")) != today] + [copy.deepcopy(official_lrow)]
    stripped, changed = strip_intraday(replaced)
    srow = next((r for r in stripped["series"] if str(r.get("date")) == today), None)
    marks_gone = not any(k in stripped for k in MARK_KEYS) and not any(k in (srow or {}) for k in MARK_KEYS)
    checks.append(("②strip 去标记", changed and marks_gone, f"changed={changed} top_marks={[k for k in MARK_KEYS if k in stripped]}"))
    official_fields = {k: official_row.get(k) for k in ("max_height", "limit_up", "limit_down", "up", "down")}
    cur_fields = {k: (srow or {}).get(k) for k in official_fields}
    checks.append(("②数值来自正式源", official_fields == cur_fields, f"official={official_fields} current={cur_fields}"))
    checks.append(("②不产生重复行", len(stripped["series"]) == len(series) and len(stripped["ladder"]) == len(ladder), f"series={len(stripped['series'])} ladder={len(stripped['ladder'])}"))
    checks.append(("②历史行仍逐字不动", stripped["series"][: len(series_base)] == series_base, "series_hist_ok"))

    # ②补：官方行优先——当日已是正式行时 merge 必须跳过、一字不改
    keep, keep_reason = merge_into_cycle(copy.deepcopy(stripped), snap)
    checks.append(("②正式行不被盘中覆盖", keep_reason == "keep-official" and keep == stripped, f"reason={keep_reason}"))

    # ②再补：strip 幂等
    twice, changed2 = strip_intraday(copy.deepcopy(stripped))
    checks.append(("②strip 幂等", changed2 is False and twice == stripped, f"changed={changed2}"))

    # 空数据保护：一个实时字段都没取到时不覆盖
    empty_snap = {"date": today, "asof": "x", "phase": "noon", "fields": {}}
    untouched, empty_reason = merge_into_cycle(copy.deepcopy(base), empty_snap)
    checks.append(("空数据不覆盖", empty_reason == "no-live-data" and untouched == base, f"reason={empty_reason}"))

    # 线上文件未被自测触碰
    checks.append(("线上 latest.json 未被改动", hashlib.sha256(real_path.read_bytes()).hexdigest() == real_hash, f"sha256={real_hash[:16]}…"))

    for name, passed, detail in checks:
        print(f"{'PASS' if passed else 'FAIL'} {name} | {detail}")
        ok += 1 if passed else 0
    print(f"== self-test {ok}/{len(checks)} passed (基准 date={today}，基准行数 series={len(series_base)} ladder={len(ladder_base)})")
    return 0 if ok == len(checks) else 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="盘中快照：让面板在 09:25/11:30/13:00 也显示当天数据")
    parser.add_argument("--phase", choices=["open", "noon", "pm", "close", "manual"], default="manual")
    parser.add_argument("--strip", action="store_true", help="close 覆盖：清掉盘中标记（幂等）")
    parser.add_argument("--date", help="快照对应交易日 YYYYMMDD（默认今天，按 Asia/Shanghai）")
    parser.add_argument("--asof", help="快照时点 ISO 字符串（默认当前时间）")
    parser.add_argument("--from-file", help="从 JSON 夹具读快照（离网自测用，不调实时接口）")
    parser.add_argument("--cycle", help="cycle payload 路径（默认 data/cycle/latest.json，自测用副本）")
    parser.add_argument("--snapshot", help="快照落盘路径（默认 data/intraday_snapshot.json）")
    parser.add_argument("--snapshot-only", action="store_true", help="只写快照，不并入 cycle")
    parser.add_argument("--self-test", action="store_true", help="在临时副本上跑自测，不碰线上文件")
    parser.add_argument("--self-test-dir", help="自测临时目录")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test(Path(args.self_test_dir) if args.self_test_dir else None)

    cycle_path = Path(args.cycle) if args.cycle else CYCLE_LATEST
    snap_path = Path(args.snapshot) if args.snapshot else SNAPSHOT_PATH
    now = datetime.now(CN)

    # close 覆盖：只清标记，不取数
    if args.strip or args.phase == "close":
        payload = read_json(cycle_path)
        if not payload:
            print(f"intraday: cycle 文件不存在 {cycle_path}，无需 strip")
            return 0
        _payload, changed = strip_intraday(payload)
        if changed:
            write_json_atomic(cycle_path, _payload)
        good, detail = close_status(_payload)
        print(f"intraday strip phase=close changed={changed} official={good} | {detail}")
        return 0 if good else 1

    date = args.date or now.strftime("%Y%m%d")
    asof = args.asof or now.isoformat(timespec="seconds")
    if args.from_file:
        snapshot = read_json(Path(args.from_file))
        if not snapshot:
            print(f"intraday: 快照夹具读不到 {args.from_file}")
            return 1
        snapshot["date"] = snapshot.get("date") or date
        snapshot["asof"] = snapshot.get("asof") or asof
        snapshot["phase"] = snapshot.get("phase") or args.phase
    else:
        snapshot = build_snapshot(date, asof, args.phase)
        write_json_atomic(snap_path, snapshot)
        flags = [f"{k}={snapshot['fields'].get(k)}" for k in ("max_height", "limit_up", "limit_down")]
        print(
            f"intraday snapshot date={date} phase={args.phase} asof={asof} {' '.join(flags)} "
            f"levels={len(snapshot['fields'].get('levels') or [])} unavailable={len(snapshot['unavailable'])}",
            flush=True,
        )
        for reason in snapshot["unavailable"]:
            print(f"[snapshot:unavailable] {reason}")

    if args.snapshot_only:
        print("intraday: --snapshot-only，未并入 cycle")
        return 0

    payload = read_json(cycle_path)
    merged, reason = merge_into_cycle(payload, snapshot)
    if reason == "merged":
        write_json_atomic(cycle_path, merged)
    row = next((r for r in (merged.get("series") or []) if str(r.get("date")) == date), None)
    print(
        f"intraday merge reason={reason} date={date} series_row={row} "
        f"written={'yes' if reason == 'merged' else 'no'}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
