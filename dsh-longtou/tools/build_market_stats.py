#!/usr/bin/env python3
"""Build one-pass market statistics from local TDX daily bars.

Outputs ``data/market_stats.json`` keyed by YYYYMMDD with the fields the v0.5
engine needs: limit-up/down counts, 炸板, one-word limit-down, high-board
limit-down, consecutive limit-down, max height, ladder, and leader one-word
ratios.

连板高度口径（2026-11 修订）：
- 以 F10 连板天梯 ``data/limit_ladder.json`` 为准（``level`` 即当日连板数）；
- 本地日线只在「F10 缺该股 / 缺该日」时兜底，且先经过除权/错值护栏；
- ``max_height_ex_one_word`` 与 ``max_height`` 同源：F10 可用时用同一份天梯重算
  （逐票用本地日线判一字），拿不到足够本地一字信息时退化为等于 ``max_height``。
涨跌停家数口径：只算沪深 A 股（含 ST、次新），北交所暂不纳入；ST 按 5%、主板
10%、创业板/科创板 20% 阈值判定。
有效板限（2026-11 修订）：不再只信「现名档位」。先按当日实际涨跌幅反推它落入的
档位 ``[0.05, 0.10, 0.20, 0.30]``（分档容差：5%⇒5.6%、10%⇒10.5%、20%⇒21.0%、
30%⇒31.5%，兼顾低价股按分位取整），推出的档位宽于现名档位时按该档位正常判
涨停/跌停与一字板，只有「所有档位都解释不了」的缺口或落在两档之间却摸不到涨跌停
的缺口才继续记入除权/错值异常清单。
``suspected_st_hist`` 语义（2026-11 修订）：只表示「现名 ST 但当日实测档位更宽、
连板被正常计入」的真实历史 10% 板，因此它只出现在**已按更宽档位判成涨跌停**的
条目上（这类条目本身不进异常清单）。落入安全阀（档位抬高但没摸到涨跌停）的条目
一律 ``suspected_st_hist=False`` 并附 ``note="st_name_band_uncertain"``，
``effective_limit_pct`` 仍保留以便事后筛。

Run:
    python tools/build_market_stats.py
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longtou.tdx import (  # noqa: E402
    TDX_VIPDOC,
    limit_pct,
    limit_price,
    list_stocks,
    read_day_file,
)

LADDER_PATH = ROOT / "data" / "limit_ladder.json"
NAMES_PATH = ROOT / "data" / "stock_names.json"
ANOMALY_PATH = ROOT / "data" / "anomaly_days.json"
# F10 连板天梯的 summary（市场总览：上涨/下跌/涨停/跌停家数），由 tools/build_limit_counts.py 落盘；
# 有则作为 limit_up/limit_down 主口径，本地日线降为兜底。文件缺失/损坏返回 {}。
LIMIT_COUNTS_PATH = ROOT / "data" / "limit_counts.json"
# st_hist 历史档位（tools/build_st_hist.py 生成）：{date:{code:band}}，band=5 即当日 ST。
ST_HIST_PATH = ROOT / "data" / "st_hist.json"

# 板限档位（ST 5% / 主板 10% / 创业板科创板 20% / 北交所 30%）与各档容差上界。
# B 项按实际涨跌幅反推档位时用：容差必须分档给，不能统一按 5% 相对放宽——
# 5% 档相对放宽只有 5.25%，低价 ST（≈¥1–2）真实 5% 涨停因取整会显示 5.3–5.5%，
# 会被错分到 10% 档再落进安全阀；20% 档同理，低价 20% 板按分位取整可显示到
# 20.5%–21%，上界给 20.5% 会把真实涨停误踢成除权异常，故取 21.0%；
# 真正的 21%–31.5% 缺口仍留给异常护栏。
TIER_CAPS = (
    (0.05, 0.056),
    (0.10, 0.105),
    (0.20, 0.21),
    (0.30, 0.315),
)
# st_hist 已知档位（百分数）→ 该档涨跌幅容差上界；已知档位直接用，不再反推放宽。
TIER_CAPS_BY_BAND = {int(tier * 100): cap for tier, cap in TIER_CAPS}
# F10 家数表条目数下限：低于此值视为表被截断/解析失败，直接退出而不是静默降级。
MIN_LIMIT_COUNT_KEYS = 100
# F10 家数表被拒条目（负数/非有限值/结构不符）占比上限：超过即视为上游污染，直接退出。
MAX_REJECTED_RATIO = 0.05

# 运行期计数：供 main() 打印审计数字，不进 JSON 输出。
_F10_EX_STATS = {"full": 0, "degraded": 0}  # D 项：F10 重算成功的日数 / 退化为 max_height 的日数
_LIMIT_STATS = {"rescued": 0, "kept_anomaly": 0, "band_beyond": 0}  # B 项：救回真实涨停数 / 仍记异常缺口数 / 档位已知但涨跌幅超该档容差的天数


def _hs_a_share(code: str) -> bool:
    """6 位代码是否沪深 A 股（含 302 创业板；不含北交所 920/43/83/87/88）。"""
    return code.startswith(
        ("600", "601", "603", "605", "688", "689",
         "000", "001", "002", "003", "300", "301", "302")
    )


def _load_ladder() -> dict[str, dict[str, int]]:
    """F10 连板天梯 → {date_str: {code: level}}；缺失/损坏返回 {}。"""
    try:
        raw = json.loads(LADDER_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, dict[str, int]] = {}
    if isinstance(raw, dict):
        for date_str, rows in raw.items():
            if not isinstance(rows, list):
                continue
            m: dict[str, int] = {}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                code = str(row.get("code") or "")
                level = row.get("level")
                if not _hs_a_share(code) or level is None:
                    continue
                try:
                    m[code] = int(level)
                except (TypeError, ValueError):
                    continue
            if m:
                out[str(date_str)] = m
    return out


def _load_names() -> dict[str, str]:
    try:
        payload = json.loads(NAMES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _is_st_name(name: str | None) -> bool:
    """名称含 ST 或 *ST（含退市整理期）即视为 ST 个股。

    口径与 build_live_counts.py 的「主板 ST（名称含 ST，含 *ST）5%」一致：
    取「名称含 ST」子串判定，而不是前缀判定，这样 'ST'、'*ST'、'S*ST'、
    小写 'st'、全角星号 '＊ST' 都能命中；空名 / '—' 等占位名不算 ST。
    判定只依赖名称本身，同一名称反复判定结果恒定（幂等）。
    """
    text = "".join(str(name or "").split()).upper()
    return "ST" in text


def _is_s_not_st(name: str | None) -> bool:
    """S 股（未股改）也是 5% 涨跌幅档，但不是 ST；判 ST 时须排除。
    仅当名称以 S 开头且不含 ST 子串时返回 True（ST/*ST/SST/S*ST 都含 ST）。
    """
    text = "".join(str(name or "").split()).upper()
    return text.startswith("S") and "ST" not in text


def _load_st_hist() -> dict[int, dict[str, int]]:
    """st_hist.json → {date:int: {code: band}}；缺失/损坏只告警并返回 {}（不 SystemExit）。"""
    try:
        raw = json.loads(ST_HIST_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 缺文件时退回名称口径，不能阻断重算
        print(f"[warn] 无法读取 {ST_HIST_PATH}（{exc}）：当日 ST 退回名称口径", flush=True)
        return {}
    out: dict[int, dict[str, int]] = {}
    rows_by_day = raw.get("days") if isinstance(raw, dict) else None
    if not isinstance(rows_by_day, dict):
        print(f"[warn] {ST_HIST_PATH} 结构不符：当日 ST 退回名称口径", flush=True)
        return {}
    for date_str, rows in rows_by_day.items():
        try:
            date_i = int(date_str)
        except (TypeError, ValueError):
            continue
        m: dict[str, int] = {}
        for code, band in (rows or {}).items():
            try:
                m[str(code).zfill(6)] = int(band)
            except (TypeError, ValueError):
                continue
        if m:
            out[date_i] = m
    return out


def _is_st_today(
    st_hist: dict[int, dict[str, int]],
    date_i: int,
    code: str,
    name_st: bool,
    name: str | None = None,
) -> bool:
    """统一「当日 ST」判定：有历史档位→band==5 且非 S 股；无档位→名称口径（创业/科创 ST 仍 20%）。"""
    band = (st_hist.get(date_i) or {}).get(code)
    if band is None:
        return name_st
    return band == 5 and not _is_s_not_st(name)


def _as_count(value) -> int | None:
    """宽容解析一个家数：int / float / 数字字符串都吃，四舍五入取整。

    非法值返回 None，由调用方计入 rejected 并丢弃：布尔、空串、非数字串、
    非有限值（nan/inf，含 "inf"/"nan" 字符串）以及**负数**——家数不可能为负，
    负数是上游符号错误或错位取值的信号，绝不能当成真实家数写进缓存。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        count = int(round(value))
        return count if count >= 0 else None
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
        if not math.isfinite(number):
            return None
        count = int(round(number))
        return count if count >= 0 else None
    return None


def _load_limit_counts() -> dict[str, dict]:
    """F10 summary 家数 → {date_str: {"limit_up": int, "limit_down": int}}。

    宽容解析：``limit_up``/``limit_down`` 是 int、float 还是数字字符串都收（四舍五入取整），
    不再用严格 ``isinstance(x, int)`` 判定——那种写法一旦上游改成 float/str 就会把整张表
    静默丢光、家数退回本地推断且不报警（历史上 2025 全错就是这个同类脆弱点）。

    文件**不存在** ⇒ 返回 {}（回退本地推断并打 warn）；
    文件存在但解析失败 / 顶层不是对象 / 有效条目 < ``MIN_LIMIT_COUNT_KEYS`` /
    被拒条目占比 > ``MAX_REJECTED_RATIO`` ⇒ 直接抛 SystemExit（退出码非 0），绝不静默降级。
    """
    if not LIMIT_COUNTS_PATH.exists():
        print(f"[warn] 缺少 {LIMIT_COUNTS_PATH}，limit_up/limit_down 回退本地日线推断", flush=True)
        return {}
    try:
        payload = json.loads(LIMIT_COUNTS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 文件在但读不出就得炸，不能静默
        raise SystemExit(f"[fatal] {LIMIT_COUNTS_PATH} 解析失败：{exc}")
    if not isinstance(payload, dict):
        raise SystemExit(f"[fatal] {LIMIT_COUNTS_PATH} 顶层不是 JSON 对象")

    out: dict[str, dict] = {}
    rejected = 0
    total = len(payload)
    for date_str, row in payload.items():
        if not isinstance(row, dict):
            rejected += 1
            continue
        up = _as_count(row.get("limit_up"))
        down = _as_count(row.get("limit_down"))
        if up is None or down is None:
            rejected += 1
            continue
        out[str(date_str)] = {"limit_up": up, "limit_down": down}
    if rejected:
        ratio = rejected / total if total else 1.0
        print(
            f"[warn] {LIMIT_COUNTS_PATH} 有 {rejected}/{total} 条家数条目被拒"
            f"（负数/非有限值/结构不符），已全部丢弃",
            flush=True,
        )
        if ratio > MAX_REJECTED_RATIO:
            raise SystemExit(
                f"[fatal] {LIMIT_COUNTS_PATH} 被拒 {rejected}/{total} 条"
                f"（> {MAX_REJECTED_RATIO:.0%}），拒绝带病写缓存"
            )
    if len(out) < MIN_LIMIT_COUNT_KEYS:
        raise SystemExit(
            f"[fatal] {LIMIT_COUNTS_PATH} 只解析出 {len(out)} 条有效家数"
            f"（< {MIN_LIMIT_COUNT_KEYS}），拒绝静默降级为本地推断"
        )
    keys = sorted(out)
    print(
        f"limit_counts keys={len(out)} {keys[0]}..{keys[-1]} rejected={rejected}",
        flush=True,
    )
    return out


def _anomaly_date(entry) -> int | None:
    """异常条目里的日期（YYYYMMDD）：容忍 int / 数字字符串，其余返回 None。"""
    if not isinstance(entry, dict):
        return None
    value = entry.get("date")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _load_anomaly_list(path: Path) -> list[dict]:
    """读回现有异常清单（--merge 合并用）。

    文件不存在 ⇒ []（首次生成）；文件在但解析失败 / 顶层不是数组 ⇒ 抛 SystemExit，
    避免把 1 万多条审计历史静默覆盖成空。
    """
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 审计历史不能被静默覆盖
        raise SystemExit(f"[fatal] 现有异常清单 {path} 解析失败，拒绝合并：{exc}")
    if not isinstance(raw, list):
        raise SystemExit(f"[fatal] 现有异常清单 {path} 顶层不是数组，拒绝合并")
    return [row for row in raw if isinstance(row, dict)]



def _grade_limit(abs_chg: float, name_pct: float) -> tuple[float | None, bool]:
    """按当日实际涨跌幅反推它落入的板限档位（B 项）。

    返回 ``(tier, upgraded)``：

    * ``tier`` 是 ``TIER_CAPS`` 里最小的、满足 ``abs_chg <= cap`` 的档位；
      四档都不够（``abs_chg > 0.315``）⇒ 返回 ``(None, False)``，
      调用方仍按除权/错值异常处理（原护栏行为不变）。
    * ``upgraded`` 表示该档位宽于「现名档位」——典型场景：现名含 ST（5%）、
      历史上并非 ST（10%），真实 10% 涨停不能再被 5% 护栏打成除权。

    容差**分档**给（见 ``TIER_CAPS``）：统一相对放宽 5% 时 5% 档只有 5.25%，
    低价 ST 真实 5% 涨停（取整后 5.3–5.5%）会被错分到 10% 档再落进安全阀；
    10% 档放宽到 10.5% 才容得下 9.6% / 10.3% 这类两位小数取整。
    """
    for tier, cap in TIER_CAPS:
        if abs_chg <= cap:
            return tier, tier > name_pct
    return None, False


def build(start: int, end: int) -> tuple[dict[int, dict], list[dict]]:
    days: dict[int, dict] = defaultdict(
        lambda: {
            "limit_up": 0,
            "limit_down": 0,
            "touched_limit": 0,
            "broken_limit": 0,
            "one_word_limit_down": 0,
            "high_board_limit_down": 0,
            "consecutive_limit_down": 0,
            "max_height": 0,
            "any_limit_up": 0,
            "leader_codes": [],
            "height_counts": defaultdict(int),
            "height_stocks": defaultdict(list),
            "non_one_word_heights": set(),
            "one_word_ratio_by_code": {},
            # D 项：F10 路径下重算的「剔除一字后最高板」；None = 当日无 F10 天梯，走本地口径。
            "ex_one_word": None,
        }
    )

    names = _load_names()
    st_hist = _load_st_hist()
    anomalies: list[dict] = []
    # D 项：日期 -> {代码: 当日是否一字板}，只收「本地判为涨停」的票，供 F10 天梯重算 ex_one_word。
    one_word_by_date: dict[int, dict[str, bool]] = defaultdict(dict)
    _F10_EX_STATS["full"] = 0
    _F10_EX_STATS["degraded"] = 0
    _LIMIT_STATS["rescued"] = 0
    _LIMIT_STATS["kept_anomaly"] = 0
    _LIMIT_STATS["band_beyond"] = 0

    stocks = list_stocks(TDX_VIPDOC)
    for stock in stocks:
        try:
            bars = read_day_file(stock.path)
        except Exception:
            continue
        # 次新不再剔除：照常参与统计；无前置 bar 时首根 bar 走 prev_close=None 降级路径。
        # 现名档位只作下限参考：真实涨跌幅落到更宽档位时以实测档位为准（见 _grade_limit）。
        name_pct = limit_pct(stock.code)
        name_st = _is_st_name(names.get(stock.code))
        s_not_st = _is_s_not_st(names.get(stock.code))
        prev_close = None
        height = 0
        down_streak = 0
        run_length = 0
        one_word_run = 0

        def _reset_state() -> None:
            """除权/错值日：连板与连跌口径全部清零，不留跨缺口的假连板。"""
            nonlocal height, run_length, one_word_run, down_streak
            height = 0
            run_length = 0
            one_word_run = 0
            down_streak = 0

        for bar in bars:
            if prev_close is None:
                prev_close = bar.close
                continue

            # 有效板限：st_hist 有当日档位就直接用（5%=当日 ST、10%=历史非 ST 硬证据），
            # 涨跌幅超出该档容差 ⇒ 除权/错值，记 beyond_known_tier 异常并清零；
            # 无档位时退回「按实际涨跌幅反推 + 放宽」兜底（保留原行为）。
            chg = (bar.close - prev_close) / prev_close if prev_close else 0.0
            band = (st_hist.get(bar.date) or {}).get(stock.code)
            st_today = (band == 5 and not s_not_st) if band is not None else name_st
            if band is not None:
                tier = band / 100.0
                upgraded = False
                if abs(chg) > TIER_CAPS_BY_BAND.get(band, 0.315):
                    if start <= bar.date <= end:
                        anomalies.append(
                            {
                                "date": bar.date,
                                "code": stock.code,
                                "name": names.get(stock.code),
                                "close": bar.close,
                                "prev_close": prev_close,
                                "chg_pct": round(chg * 100.0, 2),
                                "limit_pct": name_pct,
                                "effective_limit_pct": tier,
                                "suspected_st_hist": True,
                                "note": "beyond_known_tier",
                                "market": stock.market,
                            }
                        )
                        _LIMIT_STATS["band_beyond"] += 1
                    # 除权日（如 20261023 002731 萃华 -59.74%）触发后当日该票 reset+continue，
                    # 不入梯队/家数、也不参与 ST 剔除，安全侧偏保守。
                    _reset_state()
                    prev_close = bar.close
                    continue
                pct = tier
            else:
                tier, upgraded = _grade_limit(abs(chg), name_pct)
                if tier is None:
                    # |涨跌幅| 超过最宽档容差（31.5%）⇒ 除权/错值，维持原护栏：记异常 + 清零。
                    if start <= bar.date <= end:
                        anomalies.append(
                            {
                                "date": bar.date,
                                "code": stock.code,
                                "name": names.get(stock.code),
                                "close": bar.close,
                                "prev_close": prev_close,
                                "chg_pct": round(chg * 100.0, 2),
                                "limit_pct": name_pct,
                                "effective_limit_pct": None,
                                "suspected_st_hist": False,
                                "note": "beyond_all_tiers",
                                "market": stock.market,
                            }
                        )
                    _reset_state()
                    prev_close = bar.close
                    continue
                pct = tier if upgraded else name_pct

            up = limit_price(prev_close, pct)
            down = round(prev_close * (1.0 - pct) + 1e-9, 2)
            is_up = bar.close >= up - 0.005
            is_down = bar.close <= down + 0.005
            if upgraded and not (is_up or is_down):
                # 档位比现名更宽，却没摸到该档位的涨跌停 ⇒ 这个涨跌幅在两档之间「无板可解释」，
                # 仍按除权/错值记异常（避免把真实除权日放过）。
                # 标签诚实原则：这类条目**没有**被判成涨跌停、连板也没被计入，
                # 所以 suspected_st_hist 必须为 False，只加 note 说明是「档位不确定」。
                # 它们继续留在异常清单里（旧护栏的行为，不因标签修正而改变总量），
                # effective_limit_pct 保留以便事后按档位筛选。
                if start <= bar.date <= end:
                    anomalies.append(
                        {
                            "date": bar.date,
                            "code": stock.code,
                            "name": names.get(stock.code),
                            "close": bar.close,
                            "prev_close": prev_close,
                            "chg_pct": round(chg * 100.0, 2),
                            "limit_pct": name_pct,
                            "effective_limit_pct": tier,
                            "suspected_st_hist": False,
                            "note": "st_name_band_uncertain",
                            "market": stock.market,
                        }
                    )
                    _LIMIT_STATS["kept_anomaly"] += 1
                _reset_state()
                prev_close = bar.close
                continue
            if upgraded and start <= bar.date <= end:
                # 只有走到这里（按更宽档位真的判成涨停/跌停、连板被正常计入）才算
                # 「现名 ST 但历史非 ST」的真实宽档板，计入 rescued 审计数。
                _LIMIT_STATS["rescued"] += 1

            touched = bar.high >= up - 0.005
            one_word_down = bar.high <= down + 0.005
            prev_height = height

            if start <= bar.date <= end:
                day = days[bar.date]
                if is_up:
                    height += 1
                    run_length += 1
                    is_one_word = bar.low >= up - 0.005
                    if is_one_word:
                        one_word_run += 1
                    elif st_today:
                        # 当日 ST 个股不算「非一字可参与」，也不让该层成为可参与层。
                        pass
                    else:
                        # 最低价没到涨停价 = 当天真正换过手，不是一字板。
                        # 记下「这一层存在非一字股」，供剔除一字（及 ST）后的最高板取用。
                        day["non_one_word_heights"].add(height)
                    # D 项：把当日「本地判为涨停」的票与它是否一字记下来，
                    # 供 F10 天梯重算 max_height_ex_one_word 时逐票查。
                    one_word_by_date[bar.date][stock.code] = is_one_word
                    day["any_limit_up"] += 1
                    day["limit_up"] += 1  # 家数保持原始全市场口径（不剔一字、不剔 ST）
                    day["height_counts"][height] += 1
                    day["height_stocks"][height].append(stock.code)
                    ratio = one_word_run / run_length if run_length else 0.0
                    day["one_word_ratio_by_code"][stock.code] = ratio
                    if height > day["max_height"]:
                        day["max_height"] = height
                        day["leader_codes"] = [stock.code]
                    elif height == day["max_height"]:
                        day["leader_codes"].append(stock.code)
                else:
                    height = 0
                    run_length = 0
                    one_word_run = 0

                if is_down:
                    day["limit_down"] += 1
                    down_streak += 1
                    if one_word_down:
                        day["one_word_limit_down"] += 1
                    if prev_height >= 2:
                        day["high_board_limit_down"] += 1
                    if down_streak >= 2:
                        day["consecutive_limit_down"] += 1
                else:
                    down_streak = 0

                if touched:
                    day["touched_limit"] += 1
                    if not is_up:
                        day["broken_limit"] += 1
            else:
                # Keep the running state correct across the pre-start prefix.
                if is_up:
                    height += 1
                    run_length += 1
                    if bar.low >= up - 0.005:
                        one_word_run += 1
                else:
                    height = 0
                    run_length = 0
                    one_word_run = 0
                if is_down:
                    down_streak += 1
                else:
                    down_streak = 0

            prev_close = bar.close

    # 天梯/高度最终口径（用户定稿）：
    #   梯队 height_counts/height_stocks/leader_codes 与 max_height：只剔「当日 ST」，一字保留；
    #   max_height_ex_one_word：剔当日 ST 后再剔一字，一字状态未知的票严格剔除，不退回上界；
    #   limit_up/limit_down 家数不动（原始全市场口径，仍按 F10 官方家数覆盖）。
    ladder = _load_ladder()
    for date, day in days.items():
        f10 = ladder.get(str(date)) or {}
        base: dict[int, list[str]] = defaultdict(list)
        for code, level in f10.items():
            base[level].append(code)
        for level, codes in day["height_stocks"].items():
            for code in codes:
                if code not in f10:
                    base[level].append(code)
        if not base:
            continue
        st_set = {
            code
            for codes in base.values()
            for code in codes
            if _is_st_today(st_hist, date, code, _is_st_name(names.get(code)), names.get(code))
        }
        st_kept = {level: [c for c in codes if c not in st_set] for level, codes in base.items()}
        st_kept = {level: codes for level, codes in st_kept.items() if codes}
        day["height_stocks"] = st_kept
        day["height_counts"] = defaultdict(int)
        for level, codes in st_kept.items():
            day["height_counts"][level] = len(codes)
        if st_kept:
            mx = max(st_kept)
            day["max_height"] = mx
            day["leader_codes"] = list(st_kept[mx])
        else:
            day["max_height"] = 0
            day["leader_codes"] = []
        # height_ex：ST 剔除后再剔一字；一字未知票严格剔除（不再退化为含未知的上界）。
        info = one_word_by_date.get(date) or {}
        unknown = 0
        ex_levels: list[int] = []
        for level, codes in st_kept.items():
            for code in codes:
                flag = info.get(code)
                if flag is None:
                    unknown += 1
                elif not flag:
                    ex_levels.append(level)
        day["ex_one_word"] = max(ex_levels) if ex_levels else 0
        if unknown:
            _F10_EX_STATS["degraded"] += 1
        else:
            _F10_EX_STATS["full"] += 1

    # F10 summary 家数优先：limit_up/limit_down 以 F10 市场总览（东财官方口径）为准，
    # 本地日线推断降为兜底（覆盖不到的历史 ST 摘帽 / 本地 .day 错值问题一并规避）。
    limit_counts = _load_limit_counts()
    for date, day in days.items():
        cnt = limit_counts.get(str(date))
        if cnt:
            day["limit_up"] = cnt["limit_up"]
            day["limit_down"] = cnt["limit_down"]

    output: dict[int, dict] = {}
    for date, day in days.items():
        output[date] = {
            "date": date,
            "limit_up": day["limit_up"],
            "limit_down": day["limit_down"],
            "touched_limit": day["touched_limit"],
            "broken_limit": day["broken_limit"],
            "one_word_limit_down": day["one_word_limit_down"],
            "high_board_limit_down": day["high_board_limit_down"],
            "consecutive_limit_down": day["consecutive_limit_down"],
            "max_height": day["max_height"],
            # 剔除一字板后的最高板：只认「该层至少有一只非一字股」的最高层；
            # 当日无涨停给 None，有涨停但全是一字给 0（不是缺失，是真的没有可参与的板）。
            # 有 F10 天梯时取 F10 同日重算值（与 max_height 同源），否则走本地口径。
            # ex_one_word 唯一赋值处（约 :595）恒为 int；唯一 None 情形是 base 为空
            # ⇔ any_limit_up==0，旧 else 只能返 None，故直接取值数值等价。
            "max_height_ex_one_word": day["ex_one_word"],
            "leader_codes": day["leader_codes"],
            "height_counts": {str(k): int(v) for k, v in sorted(day["height_counts"].items())},
            "height_stocks": {str(k): list(v) for k, v in sorted(day["height_stocks"].items())},
            "one_word_ratio_by_code": day["one_word_ratio_by_code"],
        }
    return output, anomalies


def _load_existing_stats(path: Path) -> dict[str, dict]:
    """读回现有 market_stats（--merge 用）。

    文件不存在 ⇒ {}（首次生成 / 换输出路径）；文件在但解析失败 / 顶层不是对象 ⇒
    抛 SystemExit，绝不静默当空再整体覆盖——那会把历史统计无声清零。
    与 ``_load_anomaly_list`` 同一口径：坏文件一律硬失败。
    """
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 历史统计不能被静默覆盖
        raise SystemExit(f"[fatal] 现有统计 {path} 解析失败，拒绝 merge 覆盖：{exc}")
    if not isinstance(raw, dict):
        raise SystemExit(f"[fatal] 现有统计 {path} 顶层不是 JSON 对象，拒绝 merge 覆盖")
    return {str(k): v for k, v in raw.items()}


def _anomaly_sort_key(entry: dict) -> tuple[int, int, str]:
    """合并后全局排序键：(日期, 代码)；日期读不出的条目排到最后，不丢也不混排。"""
    day = _anomaly_date(entry)
    return (1 if day is None else 0, day or 0, str(entry.get("code") or ""))


def _stage_json(path: Path, payload, *, indent: int | None = None) -> Path:
    """把 JSON 写进目标同目录的临时文件并返回其路径（**不**替换目标文件）。

    用临时文件是为了「写一半崩掉」不破坏旧文件；提交由 ``os.replace`` 原子完成。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=indent), encoding="utf-8"
        )
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return tmp


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=20210802)
    parser.add_argument("--end", type=int, default=20261231)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "market_stats.json")
    parser.add_argument(
        "--merge",
        action="store_true",
        help="把本次 [start,end] 结果合并进现有 --out（保留窗口外旧日期，不整体覆盖）。",
    )
    parser.add_argument("--anomaly-out", type=Path, default=ANOMALY_PATH)
    args = parser.parse_args()

    data, anomalies = build(args.start, args.end)

    # 第一步：两侧的「读旧 + 校验」全部做完。任一侧文件坏都在落盘前 SystemExit，
    # 因此不会出现「market_stats 已写、anomaly 校验失败」这种半套结果。
    if args.merge:
        existing = _load_existing_stats(args.out)
        old_list = _load_anomaly_list(args.anomaly_out)
        merged = dict(existing)
        merged.update({str(k): v for k, v in data.items()})
        data_write = merged
        # A 项：--merge 必须做真合并。口径：窗口 [start,end] 内的日期用本次重算结果替换，
        # 窗口外旧条目原样保留。
        kept: list[dict] = []
        replaced = 0
        for entry in old_list:
            day = _anomaly_date(entry)
            if day is not None and args.start <= day <= args.end:
                replaced += 1
                continue
            kept.append(entry)
        anomalies_write = kept + anomalies
    else:
        data_write = {str(k): v for k, v in data.items()}
        anomalies_write = list(anomalies)
        old_list = []
        replaced = 0

    # 合并后必须**全局**重排（kept 在窗口外、anomalies 在窗口内，直接拼接不保证有序）。
    anomalies_write.sort(key=_anomaly_sort_key)

    # 第二步：两阶段落盘。两个临时文件都写成功后才 os.replace 成目标文件，
    # 避免「一个文件替换成功、另一个失败」的半套结果。
    staged: list[tuple[Path, Path]] = []
    try:
        staged.append((_stage_json(args.out, data_write), args.out))
        staged.append((_stage_json(args.anomaly_out, anomalies_write, indent=1), args.anomaly_out))
    except Exception:
        for tmp, _target in staged:
            tmp.unlink(missing_ok=True)
        raise
    for tmp, target in staged:
        os.replace(tmp, target)

    if args.merge:
        print(
            f"anomalies merge: before={len(old_list)} window_old={replaced} "
            f"window_new={len(anomalies)} kept={len(kept)} after={len(anomalies_write)}",
            flush=True,
        )
    else:
        print(f"anomalies rewrite: after={len(anomalies_write)}", flush=True)

    print(
        f"days={len(data)} saved={args.out} merge={args.merge} anomalies={len(anomalies)} -> {args.anomaly_out} "
        f"st_hist_rescued={_LIMIT_STATS['rescued']} st_hist_gap_kept={_LIMIT_STATS['kept_anomaly']} "
        f"band_uncertain={sum(1 for a in anomalies if a.get('note') == 'st_name_band_uncertain')} "
        f"st_hist_beyond={_LIMIT_STATS['band_beyond']} "
        f"ex_one_word_f10={_F10_EX_STATS['full']} ex_one_word_degraded={_F10_EX_STATS['degraded']}",
        flush=True,
    )

    for date in (20250606, 20250609, 20250610, 20250611):
        if date in data:
            row = data[date]
            hs = {k: v for k, v in row["height_stocks"].items() if "605099" in v}
            print(
                date,
                "limit_up", row["limit_up"],
                "limit_down", row["limit_down"],
                "max_height", row["max_height"],
                "605099", hs,
                "leaders", row["leader_codes"],
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
