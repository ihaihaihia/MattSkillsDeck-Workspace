"""Candidate/score assembly for one T-1 close.

This is the first usable slice of the v0.5 engine: market snapshot, 2/3/4-board
candidates, 09:25 auction, float-cap tiers, actual turnover, 60-day leader
state, limit-down gate, and grade downgrades.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Iterable, Sequence

from .auction import AuctionSource
from .depth import SealSource, is_trading_time, seal_flow_ratio
from .market import MarketDay, load_market_day
from .names import is_st_name, name_for
from .rules import (
    adjusted_twenty_day_max,
    auction_state,
    cap_grade,
    cap_tier,
    downgrade_grade,
    gap_adjustment as gap_grade_steps,
    grade_for_score,
    upgrade_grade,
    is_water_day,
    one_word_run,
    sixty_day_max,
    stage_cap,
    turnover_state,
)
from .tdx import (
    TDX_VIPDOC,
    Bar,
    StockFile,
    board_events,
    date_int_to_str,
    limit_pct,
    limit_price,
    list_stocks,
    read_day_file,
)

STAGE_LABEL = {2: "2→3", 3: "3→4", 4: "4→5"}


def stage_label(stage: int) -> str:
    """阶段展示名：统一用「当前板数→目标板数」，任意 stage 都成立。"""
    return STAGE_LABEL.get(stage, f"{stage}→{stage + 1}")
SUSPENDED_PATH = Path(__file__).resolve().parents[1] / "data" / "suspended_space.json"
LADDER_PATH = Path(__file__).resolve().parents[1] / "data" / "limit_ladder.json"
MARKET_STATS_PATH = Path(__file__).resolve().parents[1] / "data" / "market_stats.json"
_suspended_cache: dict | None = None
_ladder_cache: dict | None = None
_market_stats_cache: dict | None = None

# 评分维度权重（百分比）。「昨日全天成交额」占 10%，从其余 7 个维度各平均扣除
# 10 / 7 ≈ 1.4286 个百分点，总权重仍为 100%。
PREV_AMOUNT_WEIGHT = 10.0
PREV_AMOUNT_FULL_YI = 3.0   # 昨日成交额 ≤ 3 亿 → 该维度满分
PREV_AMOUNT_ZERO_YI = 10.0  # 昨日成交额 ≥ 10 亿 → 该维度 0 分，且最终档位降一档
_BASE_DIM_WEIGHTS = {
    "竞额20日比": 25.0,
    "情绪": 20.0,
    "分歧": 20.0,
    "题材/地位": 10.0,
    "个股情绪": 10.0,
    "事件驱动": 10.0,
    "开盘%": 5.0,
}
_WEIGHT_CUT = PREV_AMOUNT_WEIGHT / len(_BASE_DIM_WEIGHTS)
WEIGHTS: dict[str, float] = {
    name: weight - _WEIGHT_CUT for name, weight in _BASE_DIM_WEIGHTS.items()
}
WEIGHTS["昨日成交额"] = PREV_AMOUNT_WEIGHT


def dim_weighted(name: str, points: float) -> float:
    """把按原权重算出的分值折算到新权重（每个维度平均让出 10/7 个百分点）。"""
    return points * WEIGHTS[name] / _BASE_DIM_WEIGHTS[name]


@dataclass(frozen=True)
class LeaderState:
    code: str
    height: int
    sixty_day_max_amount: float
    sixty_day_max_date: int
    is_60d_max_next_day: bool
    one_word_ratio: float
    one_word_leader: bool
    post_break_days: int | None
    re_sealed: bool
    valid: bool
    limit_down_break: bool
    rotation_window: bool
    suspended: bool = False
    last_trade_date: int | None = None


@dataclass(frozen=True)
class BreakInfo:
    break_index: int
    days_since: int
    re_sealed: bool
    limit_down_break: bool


def _stock_map(stocks: Iterable[StockFile]) -> dict[str, StockFile]:
    return {stock.code: stock for stock in stocks}


def _limit_up(bar: Bar, prev_close: float, code: str) -> bool:
    return bar.close >= limit_price(prev_close, limit_pct(code)) - 0.005


def recent_break(bars: list[Bar], code: str, target_index: int) -> BreakInfo | None:
    """Find the most recent board-run -> break transition at/before target."""
    flags: list[bool] = [False] * len(bars)
    pct = limit_pct(code)
    for i in range(1, len(bars)):
        flags[i] = bars[i].close >= limit_price(bars[i - 1].close, pct) - 0.005

    i = target_index
    while i >= 1:
        if not flags[i] and flags[i - 1]:
            run = 0
            j = i - 1
            while j >= 1 and flags[j]:
                run += 1
                j -= 1
            if run >= 2:
                prev_close = bars[i - 1].close
                down = round(prev_close * (1.0 - pct) + 1e-9, 2)
                return BreakInfo(
                    break_index=i,
                    days_since=target_index - i,
                    re_sealed=any(flags[k] for k in range(i + 1, target_index + 1)),
                    limit_down_break=bars[i].close <= down + 0.005,
                )
        i -= 1
    return None


def _calendar_days(left: int, right: int) -> int:
    return (
        datetime.strptime(str(left), "%Y%m%d").date()
        - datetime.strptime(str(right), "%Y%m%d").date()
    ).days


def _height_at(bars: list[Bar], code: str, index: int) -> int:
    height = 0
    prev_close = bars[0].close
    pct = limit_pct(code)
    for i in range(1, index + 1):
        if bars[i].close >= limit_price(prev_close, pct) - 0.005:
            height += 1
        else:
            height = 0
        prev_close = bars[i].close
    return height


def _suspended_cache_data() -> dict:
    global _suspended_cache
    if _suspended_cache is None:
        try:
            _suspended_cache = json.loads(SUSPENDED_PATH.read_text(encoding="utf-8"))
        except Exception:
            _suspended_cache = {}
    return _suspended_cache


def apply_suspended_space_boards(
    market: MarketDay,
    stocks: Iterable[StockFile],
    target_date: int,
) -> None:
    """Keep a suspended highest board in its cycle when no one passes it.

    A stock is considered a suspended space-board candidate when it has no bar
    on the target date, its last bar is recent, and that last bar ends a run of
    at least two consecutive limit-ups.  The candidate only matters when its
    last height is at least the current market maximum.
    """
    cached = _suspended_cache_data().get(str(target_date))
    if cached is not None:
        found = [
            (int(item.get("height", 0)), int(item.get("last_date", 0)), str(item.get("code", "")), item)
            for item in cached
            if int(item.get("height", 0)) >= 2
        ]
    else:
        found = []
        for stock in stocks:
            try:
                bars = read_day_file(stock.path)
            except Exception:
                continue
            if len(bars) < 3:
                continue
            dates = [bar.date for bar in bars]
            idx = bisect_left(dates, target_date)
            if idx < len(bars) and bars[idx].date == target_date:
                continue
            last_idx = idx - 1
            if last_idx < 1:
                continue
            last = bars[last_idx]
            gap = _calendar_days(target_date, last.date)
            if gap <= 0 or gap > 30:
                continue
            height = _height_at(bars, stock.code, last_idx)
            if height >= 2:
                found.append(
                    (
                        height,
                        last.date,
                        stock.code,
                        {"one_word_ratio": one_word_run(bars, stock.code, last_idx).ratio},
                    )
                )

    if not found:
        return
    best_height = max(item[0] for item in found)
    if best_height < market.max_height:
        return
    best = [item for item in found if item[0] == best_height]
    codes = [item[2] for item in best]
    market.suspended_leader_codes = codes
    market.suspended_leader_heights = {item[2]: item[0] for item in best}
    market.suspended_leader_last_dates = {item[2]: item[1] for item in best}
    for height, last_date, code, extra in best:
        if isinstance(extra, dict):
            market.one_word_ratio_by_code[code] = float(extra.get("one_word_ratio", 0.0))
    market.max_height = max(market.max_height, best_height)
    existing = [code for code in market.leader_codes if code not in codes]
    market.leader_codes = codes + existing


def leader_state(
    market: MarketDay,
    stock_map: dict[str, StockFile],
    target_date: int,
) -> LeaderState | None:
    if not market.leader_codes:
        return None
    code = market.leader_codes[0]
    stock = stock_map.get(code)
    if stock is None:
        return None
    bars = read_day_file(stock.path)
    dates = [bar.date for bar in bars]
    suspended = code in market.suspended_leader_codes
    last_trade_date = market.suspended_leader_last_dates.get(code)
    if suspended:
        if last_trade_date is None:
            last_trade_date = bars[-1].date if bars else 0
        idx = bisect_left(dates, last_trade_date)
        if idx >= len(bars) or bars[idx].date != last_trade_date or idx == 0:
            return None
    else:
        idx = bisect_left(dates, target_date)
        if idx >= len(bars) or bars[idx].date != target_date or idx == 0:
            return None
        last_trade_date = target_date

    is_board = _limit_up(bars[idx], bars[idx - 1].close, code)
    max60 = sixty_day_max(bars, idx)
    one_word = one_word_run(bars, code, idx)
    brk = recent_break(bars, code, idx)
    post_break = brk is not None and brk.days_since in (1, 2)
    re_sealed = bool(brk and brk.re_sealed)
    rotation = bool(max60.is_latest and is_board) or post_break
    return LeaderState(
        code=code,
        height=market.max_height,
        sixty_day_max_amount=max60.amount,
        sixty_day_max_date=max60.date,
        is_60d_max_next_day=bool(max60.is_latest and is_board),
        one_word_ratio=one_word.ratio,
        one_word_leader=one_word.ratio >= 0.5,
        post_break_days=None if brk is None else brk.days_since,
        re_sealed=re_sealed,
        valid=not re_sealed,
        limit_down_break=bool(brk and brk.limit_down_break and post_break),
        rotation_window=rotation,
        suspended=suspended,
        last_trade_date=last_trade_date,
    )


def _market_stats() -> dict:
    global _market_stats_cache
    if _market_stats_cache is None:
        try:
            _market_stats_cache = json.loads(MARKET_STATS_PATH.read_text(encoding="utf-8"))
        except Exception:
            _market_stats_cache = {}
    return _market_stats_cache


def broken_leader_state(trade_date: int | None, stock_map: dict[str, StockFile] | None = None) -> dict | None:
    """Previous highest board that broke 1 or 2 trading days ago.

    The close-of-day market stats keep the old high-water mark; a lower current
    max means the previous space board broke.  Day 1 and day 2 keep showing the
    old board; from day 3 the new highest board takes over.
    """
    if not trade_date:
        return None
    stats = _market_stats()
    dates = sorted(int(day) for day in stats)
    if not dates:
        return None
    known = [day for day in dates if day <= trade_date]
    if not known:
        return None
    latest = known[-1]
    current = stats[str(latest)]
    current_max = int(current.get("max_height", 0) or 0)
    recent = known[-20:]
    higher = [
        day for day in recent
        if int(stats[str(day)].get("max_height", 0) or 0) > current_max
    ]
    if not higher:
        return None
    high_height = max(int(stats[str(day)].get("max_height", 0) or 0) for day in higher)
    last_high_date = max(
        day for day in higher
        if int(stats[str(day)].get("max_height", 0) or 0) == high_height
    )
    if current_max >= high_height:
        return None
    calendar_set = set(dates)
    calendar_set.add(trade_date)
    cursor = datetime.strptime(str(latest), "%Y%m%d").date() + timedelta(days=1)
    end = datetime.strptime(str(trade_date), "%Y%m%d").date()
    while cursor <= end:
        if cursor.weekday() < 5:
            calendar_set.add(int(cursor.strftime("%Y%m%d")))
        cursor += timedelta(days=1)
    calendar = sorted(calendar_set)
    if last_high_date not in calendar or trade_date not in calendar:
        return None
    age = calendar.index(trade_date) - calendar.index(last_high_date)
    if age not in (1, 2):
        return None
    row = stats[str(last_high_date)]
    codes = row.get("leader_codes") or []
    if not codes:
        return None
    code = str(codes[0])
    if stock_map is not None and code in stock_map and trade_date <= latest:
        try:
            bars = read_day_file(stock_map[code].path)
            dates_for_stock = [bar.date for bar in bars]
            idx = bisect_left(dates_for_stock, trade_date)
            if idx >= len(dates_for_stock) or dates_for_stock[idx] != trade_date:
                # A missing bar on a completed trade date is suspension, not a break.
                last_idx = idx - 1
                if last_idx >= 0 and _calendar_days(trade_date, dates_for_stock[last_idx]) <= 30:
                    return None
        except Exception:
            pass
    return {
        "code": code,
        "name": name_for(code),
        "height": high_height,
        "break_day": age,
        "last_high_date": last_high_date,
        "current_max_height": current_max,
    }


def _ladder_data() -> dict:
    global _ladder_cache
    if _ladder_cache is None:
        try:
            _ladder_cache = json.loads(LADDER_PATH.read_text(encoding="utf-8"))
        except Exception:
            _ladder_cache = {}
    return _ladder_cache


def _ladder_rows(target_date: int) -> list[dict]:
    cached = _ladder_data().get(str(target_date))
    if cached is not None:
        return cached
    try:
        from eltdx import TdxClient

        with TdxClient(timeout=12) as client:
            ladder = client.f10.limit_board_ladder(str(target_date), str(target_date), include_summary=True)
        rows: list[dict] = []
        for row in ladder.rows:
            level = getattr(row, "board_level", None)
            if level is None or int(level) < 1:
                continue
            rows.append(
                {
                    "code": str(getattr(row, "code", "") or ""),
                    "name": str(getattr(row, "name", "") or ""),
                    "level": int(level),
                    "industry": str(getattr(row, "industry", "") or ""),
                    "reason": str(getattr(row, "limit_reason", "") or ""),
                    "extra": str(getattr(row, "limit_reason_extra", "") or ""),
                }
            )
        return rows
    except Exception:
        return []


def stock_emotion(target_date: int, code: str, stage: int) -> dict:
    """Independent vs assisted concept/industry strength, worth 10 points.

    Concept tags are preferred over the broad industry so a 通用设备/机器人
    style union does not inflate the assist count.  If concepts have no peers,
    the industry is used as a fallback.
    """
    rows = _ladder_rows(target_date)
    row = next((item for item in rows if item.get("code") == code), None)
    if row is None:
        return {
            "label": "无数据",
            "score": 0.0,
            "count": 0,
            "sector": "",
            "group_kind": "",
            "assists": [],
            "items": [],
            "detail": "个股情绪 无 F10 连板梯队数据",
            "reason": "个股情绪 无 F10 连板梯队数据",
        }

    industry = str(row.get("industry") or "").strip()
    concepts: list[str] = []
    for key in ("reason", "extra"):
        value = str(row.get(key) or "").strip()
        if value and value not in concepts:
            concepts.append(value)

    def peers_for(group: str) -> list[dict]:
        return [
            item
            for item in rows
            if item.get("code") != code
            and int(item.get("level", 0)) >= 1
            and group in {
                str(item.get("industry") or "").strip(),
                str(item.get("reason") or "").strip(),
                str(item.get("extra") or "").strip(),
            }
        ]

    concept_peers = [(tag, peers_for(tag)) for tag in concepts]
    chosen_tag = ""
    chosen_kind = ""
    peers: list[dict] = []
    if concept_peers:
        def concept_rank(item: tuple[str, list[dict]]) -> tuple[int, int]:
            tag, group_peers = item
            low = sum(
                1
                for peer in group_peers
                if 1 <= int(peer.get("level", 0)) <= 3 and int(peer.get("level", 0)) < stage
            )
            return low, len(group_peers)

        tag, group_peers = max(concept_peers, key=concept_rank)
        if group_peers:
            chosen_tag, chosen_kind, peers = tag, "概念", group_peers
    if not peers and industry:
        group_peers = [item for item in rows if item.get("code") != code and item.get("industry") == industry and int(item.get("level", 0)) >= 1]
        if group_peers:
            chosen_tag, chosen_kind, peers = industry, "板块", group_peers

    if not peers:
        sector = chosen_tag or industry or (concepts[0] if concepts else "")
        if not sector:
            return {
                "label": "无数据",
                "score": 0.0,
                "count": 0,
                "sector": "",
                "group_kind": "",
                "assists": [],
                "items": [],
                "detail": "个股情绪 无板块/概念标签",
                "reason": "个股情绪 无板块/概念标签",
            }
        return {
            "label": "独立",
            "score": 10.0,
            "count": 0,
            "sector": sector,
            "group_kind": chosen_kind,
            "assists": [],
            "items": [],
            "detail": f"个股情绪 独立（{sector} 内无其他连板）",
            "reason": f"个股情绪 独立（{sector} 内无其他连板）",
        }

    assists = [
        item for item in peers
        if 1 <= int(item.get("level", 0)) <= 3 and int(item.get("level", 0)) < stage
    ]
    detail_items: list[str] = []
    items: list[dict] = []
    for item in assists:
        name = str(item.get("name") or "").strip()
        level = int(item.get("level", 0))
        label = f"{level}板"
        detail_items.append(f"{name} {label} {chosen_tag}")
        items.append(
            {
                "code": item.get("code", ""),
                "name": name,
                "level": level,
                "label": label,
                "group": chosen_tag,
            }
        )
    detail = "；".join(detail_items)
    if assists:
        return {
            "label": f"助力{len(assists)}",
            "score": float(min(10, 6 + len(assists))),
            "count": len(assists),
            "sector": chosen_tag,
            "group_kind": chosen_kind,
            "assists": [item.get("code", "") for item in assists],
            "items": items,
            "detail": f"个股情绪 助力{len(assists)}（{chosen_kind}：{chosen_tag}；{detail}）",
            "reason": f"个股情绪 助力{len(assists)}（{chosen_kind}：{chosen_tag}；{detail}）",
        }
    return {
        "label": "同板",
        "score": 4.0,
        "count": 0,
        "sector": chosen_tag,
        "group_kind": chosen_kind,
        "assists": [],
        "items": [],
        "detail": f"个股情绪 同板（{chosen_kind}：{chosen_tag}；有连板但无低位助力）",
        "reason": f"个股情绪 同板（{chosen_kind}：{chosen_tag}；有连板但无低位助力）",
    }


def event_driver(target_date: int, market: MarketDay, emotion: dict) -> dict:
    """News/market/hotspot classification, worth 10 points."""
    sector = str(emotion.get("sector") or "").strip()
    if not sector:
        return {
            "label": "无数据",
            "score": 0.0,
            "news": "无 F10 题材标签",
            "market": "",
            "hotspot": "",
            "detail": "新闻：无 F10 题材标签；大盘：—；热点：—",
        }
    ladder = _ladder_data()
    dates = sorted(int(day) for day in ladder)

    def count_for(day: int, tag: str) -> int:
        rows = _ladder_rows(day)
        return sum(
            1 for row in rows
            if tag in {
                str(row.get("industry") or "").strip(),
                str(row.get("reason") or "").strip(),
                str(row.get("extra") or "").strip(),
            }
        )

    prior_dates = [day for day in dates if day < target_date][-2:]
    current_count = count_for(target_date, sector)
    prior_counts = [count_for(day, sector) for day in prior_dates]
    p1 = prior_counts[-1] if prior_counts else 0
    p2 = prior_counts[-2] if len(prior_counts) > 1 else 0

    if current_count >= 3 and p1 >= 2 and p2 >= 2:
        hotspot = "主线"
    elif current_count >= 2 and p1 <= 1:
        hotspot = "新题材"
    elif p1 > current_count and current_count <= 1:
        hotspot = "退潮"
    elif current_count >= 2:
        hotspot = "发酵"
    else:
        hotspot = "个股"

    text = sector
    policy_words = ("政策", "规划", "补贴", "国常会", "监管", "试点", "立法", "发改委", "工信部", "财政部")
    info_words = ("公告", "中标", "订单", "业绩", "重组", "并购", "涨价", "签约", "合作", "收购")
    if any(word in text for word in policy_words):
        news_kind = "政策"
    elif any(word in text for word in info_words):
        news_kind = "信息"
    else:
        news_kind = "题材/舆情"

    if market.limit_down_intensity >= 60 or market.high_board_limit_down >= 2:
        risk = "风险高"
    elif market.limit_down_intensity >= 40 or market.high_board_limit_down >= 1:
        risk = "风险中"
    else:
        risk = "风险低"

    news_text = f"{sector}（{news_kind}；F10 题材/舆情）"
    market_text = (
        f"{risk}（涨停{market.limit_up}/跌停{market.limit_down}；"
        f"跌停强度{market.limit_down_intensity}；高位跌停{market.high_board_limit_down}；"
        f"封板率{market.seal_rate:.1f}%）"
    )
    hotspot_text = (
        f"{sector} 当前{current_count}只连板，前1日{p1}只、前2日{p2}只，{hotspot}"
    )
    base_score = {"主线": 9.0, "新题材": 8.0, "发酵": 7.0, "个股": 4.0, "退潮": 2.0}.get(hotspot, 3.0)
    if news_kind == "政策":
        base_score += 1.0
    elif news_kind == "信息":
        base_score += 0.5
    if risk == "风险高":
        base_score -= 1.0
    event_score = round(max(0.0, min(10.0, base_score)), 1)
    return {
        "label": f"{sector}·{hotspot}",
        "score": event_score,
        "news": news_text,
        "market": market_text,
        "hotspot": hotspot_text,
        "detail": f"新闻：{news_text}；大盘：{market_text}；热点：{hotspot_text}",
    }


def _action_for_grade(grade: str, one_word: bool = False, blocked: bool = False) -> str:
    if blocked:
        return "禁止"
    if one_word:
        return "观察（一字）"
    if grade in ("A", "B"):
        return "可研究"
    if grade == "C":
        return "观察"
    return "不做"


def prev_amount_score(prev_amount: float) -> float:
    """昨日全天成交额得分：≤3 亿满分，3~10 亿线性递减，≥10 亿 0 分；单位元。"""
    yi = max(0.0, float(prev_amount or 0.0)) / 1e8
    if yi <= PREV_AMOUNT_FULL_YI:
        return PREV_AMOUNT_WEIGHT
    if yi >= PREV_AMOUNT_ZERO_YI:
        return 0.0
    span = PREV_AMOUNT_ZERO_YI - PREV_AMOUNT_FULL_YI
    return PREV_AMOUNT_WEIGHT * (PREV_AMOUNT_ZERO_YI - yi) / span


def opening_score(open_gap: float) -> float:
    if -1 <= open_gap <= 1:
        return 5.0
    if 1 < open_gap <= 4:
        return 4.0
    return 0.0


def position_for(score: float, action: str) -> int:
    if action != "可研究":
        return 0
    if score >= 80:
        return 80
    if score >= 70:
        return 60
    if score >= 65:
        return 50
    if score >= 60:
        return 40
    if score >= 55:
        return 30
    if score >= 50:
        return 20
    return 10


# ---------------------------------------------------------------------------
# 打板纪律：时机 / 参与位 / 结构定性 / 加分项
#
# 参考外部打板系统（享逸AI）的打板报告结构，全部用现有数据推导：
# 日线成交额、09:25 竞价涨幅、换手状态、连板高度、题材标签，不引入新数据源。
# 这些函数都是纯函数（只读入参），便于单测；所有入参允许 None/缺失，绝不抛异常。
# ---------------------------------------------------------------------------

TIMING_LIMIT_UP = "打板"
TIMING_HALF_WAY = "半路"
TIMING_NO_CHASE = "不追"

# 昨日成交额（亿）分界：≤10 亿量能仍可控（与评分里 0 分临界一致）；
# >20 亿视为巨量，分歧过大，次日承接要求过高。
TIMING_AMOUNT_OK_YI = 10.0
TIMING_AMOUNT_HEAVY_YI = 20.0
# 竞价高开（%）：+2%~+7% 为可打板区间；>7% 溢价透支；>9% 接近一字。
TIMING_GAP_LOW = 2.0
TIMING_GAP_HIGH = 7.0
TIMING_GAP_NEAR_ONE_WORD = 9.0

# 参与位（买卖纪律）：按时机档位给不同措辞，止损口径统一为「次日 -3% 或 破板」。
TIMING_ENTRY_TEXT = {
    TIMING_LIMIT_UP: "打板价=涨停价；止损=次日 -3% 或 破板",
    TIMING_HALF_WAY: "等回踩均价/分时均线再进，不追高；止损=次日 -3% 或 破板",
    TIMING_NO_CHASE: "不参与；等次日承接确认或换更硬的标；已持有则破板/次日 -3% 止损",
}


def _num(value: object, default: float | None = None) -> float | None:
    """把报告里的数值安全转成 float；None/空串/脏数据返回 default，不抛异常。"""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _grade_weak(grade: object) -> bool:
    """基础档是否 ≤ C（A 最好、D 最差）；空值按最差处理，避免漏判。"""
    order = ("A", "B", "C", "D")
    text = str(grade or "").strip().upper()
    if text not in order:
        return True
    return order.index(text) >= order.index("C")


def timing_for(
    *,
    stage: int | None,
    base_grade: object,
    open_gap_pct: object,
    prev_amount: object,
    turnover_state: str = "",
    one_word: bool = False,
    final_grade: object = None,
) -> dict:
    """打板时机三档判定（打板 / 半路 / 不追）+ 参与位，返回纯 dict。

    判定依据（先否决、再择优；阈值全部来自现有打分口径的同一批临界值）：

    1. 不追（硬否决，优先级最高）
       - 基础档 ≤ C：量能/分歧/情绪已有维度不合格，追高胜率低；
       - 昨日成交额 > 20 亿：巨量＝分歧巨大，次日承接要求过高，极易一日游；
       - 竞价高开 > 9%：接近一字，打板买点最差，追进去就是接盘。
    2. 打板（最优档）——连板高度 ≥ 2，且三项都支持：
       - 昨日成交额 ≤ 10 亿：量能未过热，仍有承接空间（10 亿是评分 0 分临界）；
       - 竞价高开在 +2%~+7%：低于 2% 承接不明，高于 7% 溢价过高、日内回撤风险大；
       - 分歧不过大：换手状态非「死亡换手」，且不是一字板（一字买不到/接力风险高）。
    3. 半路（次优档）——昨日成交额 > 10 亿（量太重）、或竞价高开 > 7%（溢价透支），
       买点等回踩确认，不追高；高度 < 2 或数据缺失时同样归此档：无身位优势、只能低吸。

    prev_amount 单位元；stage 为 T-1 连板高度（候选阶段 2/3/4）。
    """
    gap = _num(open_gap_pct)
    amount_yi = _num(prev_amount)
    if amount_yi is not None:
        amount_yi = max(0.0, amount_yi) / 1e8
    height = int(stage or 0)
    reasons: list[str] = []

    if _grade_weak(base_grade):
        timing = TIMING_NO_CHASE
        reasons.append(f"基础档 {base_grade or '-'} ≤ C，量能/分歧已有硬伤，不参与")
    elif final_grade is not None and _grade_weak(final_grade):
        # 降档（stage_cap / gap / stage_priority）只改 final_grade，这里必须一起看，
        # 否则最终档已降到 C/D 的票仍会显示「打板」，与「操作」列自相矛盾。
        timing = TIMING_NO_CHASE
        reasons.append(f"最终档 {final_grade} ≤ C（梯队/量能降档后），不参与")
    elif amount_yi is not None and amount_yi > TIMING_AMOUNT_HEAVY_YI:
        timing = TIMING_NO_CHASE
        reasons.append(
            f"昨日成交额 {amount_yi:.2f}亿 > {TIMING_AMOUNT_HEAVY_YI:.0f}亿（巨量），分歧过大不追"
        )
    elif gap is not None and gap > TIMING_GAP_NEAR_ONE_WORD:
        timing = TIMING_NO_CHASE
        reasons.append(
            f"竞价高开 {gap:+.2f}% > +{TIMING_GAP_NEAR_ONE_WORD:.0f}%，接近一字，打板买点差"
        )
    else:
        height_ok = height >= 2
        amount_ok = amount_yi is not None and amount_yi <= TIMING_AMOUNT_OK_YI
        gap_ok = gap is not None and TIMING_GAP_LOW <= gap <= TIMING_GAP_HIGH
        divergence_ok = turnover_state != "死亡换手" and not one_word
        if height_ok and amount_ok and gap_ok and divergence_ok:
            timing = TIMING_LIMIT_UP
            reasons.append(
                f"{height} 板有身位；竞价 {gap:+.2f}% 在 +{TIMING_GAP_LOW:.0f}%~"
                f"+{TIMING_GAP_HIGH:.0f}%；成交额 {amount_yi:.2f}亿 ≤ "
                f"{TIMING_AMOUNT_OK_YI:.0f}亿；分歧可控 → 可打板"
            )
        else:
            timing = TIMING_HALF_WAY
            if not height_ok:
                reasons.append(f"连板高度 {height} < 2，无身位优势，只能低吸半路")
            if not amount_ok:
                if amount_yi is None:
                    reasons.append("昨日成交额缺失，无法确认量能，按半路等回踩")
                else:
                    reasons.append(
                        f"昨日成交额 {amount_yi:.2f}亿 > {TIMING_AMOUNT_OK_YI:.0f}亿，量太重，等回踩"
                    )
            if not gap_ok:
                if gap is None:
                    reasons.append("竞价涨幅缺失，无法确认强度，按半路等回踩")
                else:
                    reasons.append(
                        f"竞价高开 {gap:+.2f}% 不在 +{TIMING_GAP_LOW:.0f}%~"
                        f"+{TIMING_GAP_HIGH:.0f}%，溢价不合适，等回踩"
                    )
            if not divergence_ok:
                reasons.append(
                    "T-1 一字板，无有效分歧可打" if one_word else "换手状态死亡换手，分歧过大，等回踩"
                )

    return {
        "timing": timing,
        "position": TIMING_ENTRY_TEXT[timing],
        "reasons": reasons,
    }


UNCLASSIFIED_THEME = "未分类"


def theme_key(row: dict) -> str:
    """题材键：优先个股情绪里的题材标签，其次事件驱动标签的题材部分。

    两处标签都缺失时返回 `UNCLASSIFIED_THEME`。该值只表示「无题材标签」，
    不是真实题材，池内统计（pool_stats）必须跳过它，否则多只无标签票会被
    聚合成一个假题材、进而输出「跟风票，防一日游」的假结论。
    """
    sector = str(row.get("stock_sector") or "").strip()
    if sector:
        return sector
    label = str(row.get("event_driver") or "").strip()
    if "·" in label:
        label = label.split("·", 1)[0].strip()
    return label or UNCLASSIFIED_THEME


def pool_stats(rows: list[dict]) -> tuple[int, dict[str, dict]]:
    """统计候选池结构：池内最高板 + 各题材出现次数/最高板。

    返回 (pool_max, {题材: {"count": n, "max_height": h, "codes": [...]}})。
    错误行（无 stage_n）不参与统计；无题材标签的行只贡献高度、不进题材聚合。
    """
    pool_max = 0
    stats: dict[str, dict] = {}
    for row in rows:
        if row.get("error"):
            continue
        height = int(_num(row.get("stage_n"), 0) or 0)
        pool_max = max(pool_max, height)
        key = theme_key(row)
        if key == UNCLASSIFIED_THEME:
            continue
        item = stats.setdefault(key, {"count": 0, "max_height": 0, "codes": []})
        item["count"] += 1
        item["max_height"] = max(item["max_height"], height)
        item["codes"].append(str(row.get("code") or ""))
    return pool_max, stats


def structure_for(row: dict, pool_max: int, theme_stats: dict[str, dict]) -> str:
    """结构定性：有没有高度龙头 + 本股题材是否重复出现（跟随票/一日游风险）。

    数据依据：
    - 候选池存在 4 板（4→5 阶段）即视为「有高度龙头」；最高仅 2 板＝无高标，防一日游；
    - 同题材在候选池出现 ≥2 只、且该题材内最高板 ≤2（题材里没有高标领涨）
      ⇒ 记为「跟风票，防一日游」（无高标时跟风票最容易一日游）。
    """
    key = theme_key(row)
    stat = theme_stats.get(key) or {}
    count = int(stat.get("count") or 0)
    theme_height = int(stat.get("max_height") or 0)

    if pool_max >= 4:
        head = f"有高度龙头（池内最高 {pool_max} 板）"
    elif pool_max >= 3:
        head = f"高度一般（池内最高 {pool_max} 板，无 4 板以上高标）"
    else:
        head = f"无高标（池内最高 {pool_max} 板），一日游风险高"

    if key == UNCLASSIFIED_THEME or not count:
        # 无题材标签时不做题材共振判断，避免多只无标签票被当成同一题材的跟风票
        theme = "无题材标签（未归类），不做题材共振判断"
    elif count >= 2 and theme_height <= 2:
        theme = f"题材〔{key}〕{count} 只且最高 {theme_height} 板 → 跟风票，防一日游"
    elif count >= 2:
        theme = f"题材〔{key}〕{count} 只共振（题材内最高 {theme_height} 板）"
    else:
        theme = f"题材〔{key}〕独苗"
    return f"{head}；{theme}"


def bonus_items(row: dict) -> list[dict]:
    """打板加分项逐条判定（7 条），每条给「加分/不加分」+ 一句话理由。

    口径（与评分维度同源，不新增数据）：
    1. 题材新鲜度：事件驱动热点为新题材/主线 → 加分；
    2. 竞价强度：高开 +2%~+7% → 加分（低于 2% 承接不明、高于 7% 溢价过高）；
    3. 成交额健康：昨日 3~10 亿 → 加分（<3 亿承接不足，>10 亿量能过热）；
    4. 昨日换手率适中：5%~20% → 加分（过低换手不足，过高分歧/出货）；
    5. 连板位置：2~3 板 → 加分（2~3 板是加速期，4 板以上性价比下降）；
    6. 流通市值适中：30~150 亿 → 加分（过小易操纵、过大拉不动）；
    7. 分歧适中：换手状态健康区且非一字板 → 加分。
    """
    gap = _num(row.get("open_gap_pct"))
    amount_yi = _num(row.get("prev_amount"))
    if amount_yi is not None:
        amount_yi = max(0.0, amount_yi) / 1e8
    turnover = _num(row.get("turnover_pct"))
    mcap = _num(row.get("float_mcap_yi"))
    height = int(_num(row.get("stage_n"), 0) or 0)
    tstate = str(row.get("turnover_state") or "").strip()
    one_word = bool(row.get("one_word"))
    label = str(row.get("event_driver") or "").strip()
    hotspot = label.split("·")[-1].strip() if "·" in label else ""

    items: list[dict] = []

    fresh = hotspot in ("新题材", "主线")
    items.append({
        "key": "题材新鲜度",
        "short": "题材",
        "ok": fresh,
        "reason": (
            f"{hotspot} 属新题材/主线，题材有新鲜度"
            if fresh
            else f"{hotspot or '无题材标签'}，非新题材/主线，题材不新鲜"
        ),
    })

    gap_ok = gap is not None and TIMING_GAP_LOW <= gap <= TIMING_GAP_HIGH
    items.append({
        "key": "竞价强度",
        "short": "竞价",
        "ok": gap_ok,
        "reason": (
            f"高开 {gap:+.2f}%，落在 +2%~+7% 强势区"
            if gap_ok
            else (f"高开 {gap:+.2f}%，不在 +2%~+7%" if gap is not None else "竞价涨幅缺失")
        ),
    })

    amount_ok = amount_yi is not None and 3.0 <= amount_yi <= TIMING_AMOUNT_OK_YI
    items.append({
        "key": "成交额健康",
        "short": "量能",
        "ok": amount_ok,
        "reason": (
            f"昨日 {amount_yi:.2f}亿，处于 3~10 亿健康区间"
            if amount_ok
            else (f"昨日 {amount_yi:.2f}亿，不在 3~10 亿" if amount_yi is not None else "成交额缺失")
        ),
    })

    turnover_ok = turnover is not None and 5.0 <= turnover <= 20.0
    items.append({
        "key": "昨日换手率适中",
        "short": "换手",
        "ok": turnover_ok,
        "reason": (
            f"换手 {turnover:.2f}%，5%~20% 适中"
            if turnover_ok
            else (f"换手 {turnover:.2f}%，不在 5%~20%" if turnover is not None else "换手率缺失")
        ),
    })

    height_ok = height in (2, 3)
    items.append({
        "key": "连板位置",
        "short": "连板",
        "ok": height_ok,
        "reason": (
            f"{height} 板，处于 2~3 板加速区"
            if height_ok
            else f"{height} 板，不在 2~3 板最优区"
        ),
    })

    mcap_ok = mcap is not None and 30.0 <= mcap <= 150.0
    items.append({
        "key": "流通市值适中",
        "short": "市值",
        "ok": mcap_ok,
        "reason": (
            f"流通市值 {mcap:.1f}亿，30~150 亿兼顾弹性与容量"
            if mcap_ok
            else (f"流通市值 {mcap:.1f}亿，不在 30~150 亿" if mcap is not None else "流通市值缺失")
        ),
    })

    divergence_ok = tstate == "健康区" and not one_word
    if one_word:
        divergence_reason = "T-1 一字板，无有效分歧（且买不到）"
    elif tstate:
        divergence_reason = f"换手状态{tstate}，{'分歧可控' if divergence_ok else '分歧偏大'}"
    else:
        divergence_reason = "换手状态缺失"
    items.append({
        "key": "分歧适中",
        "short": "分歧",
        "ok": divergence_ok,
        "reason": divergence_reason,
    })
    return items


def bonus_text(items: list[dict]) -> str:
    """加分项表格单元格文本：命中数/总数 + 各条 ✓/✗ 短标签。"""
    if not items:
        return "-"
    passed = sum(1 for item in items if item["ok"])
    marks = " ".join(f"{'✓' if item['ok'] else '✗'}{item['short']}" for item in items)
    return f"{passed}/{len(items)} {marks}"


def bonus_reason_text(items: list[dict]) -> str:
    """加分项完整理由：逐条「加分/不加分（依据）」。"""
    return "；".join(
        f"{item['key']}{'加分' if item['ok'] else '不加分'}（{item['reason']}）"
        for item in items
    )


def annotate_discipline(rows: list[dict]) -> None:
    """原地给候选行补上「时机/参与位/结构定性/加分项」以及明细字段。

    必须在所有档位调整（含 stage_priority 的梯队降档）之后调用：此时 final_grade
    已是终值，timing_for 同时看 base_grade 与 final_grade，避免「最终档已降到 C/D
    但时机仍写打板 / 打板价=涨停价」的列间矛盾。错误行只留占位符，不会抛异常。
    """
    pool_max, theme_stats = pool_stats(rows)
    for row in rows:
        if row.get("error"):
            row.setdefault("时机", "-")
            row.setdefault("参与位", "-")
            row.setdefault("结构定性", "-")
            row.setdefault("加分项", "-")
            continue
        decision = timing_for(
            stage=row.get("stage_n"),
            base_grade=row.get("base_grade"),
            open_gap_pct=row.get("open_gap_pct"),
            prev_amount=row.get("prev_amount"),
            turnover_state=str(row.get("turnover_state") or ""),
            one_word=bool(row.get("one_word")),
            final_grade=row.get("final_grade"),
        )
        row["时机"] = decision["timing"]
        row["参与位"] = decision["position"]
        row["时机理由"] = decision["reasons"]
        row["结构定性"] = structure_for(row, pool_max, theme_stats)
        items = bonus_items(row)
        row["加分项"] = bonus_text(items)
        row["加分项明细"] = items
        row["加分项理由"] = bonus_reason_text(items)


def _score_candidate(
    *,
    auction_pass: bool,
    auction_ratio_max: float,
    gate_max: float,
    open_gap: float,
    turnover: str,
    one_word: bool,
    rotation_window: bool,
    limit_down_intensity: float,
    stock_emotion_score: float = 0.0,
    event_score: float = 0.0,
    opening_score_value: float = 0.0,
    theme_placeholder: float = 10.0,
    prev_amount: float = 0.0,
) -> tuple[float, dict[str, float], str]:
    """A transparent v0.1 score; theme data is still a placeholder.

    The score is only for ranking after hard gates; it is not a probability.
    Each dimension is computed on its original 0-100 point scale and then
    rescaled by ``dim_weighted`` to the current weights (100% total).
    """
    parts: dict[str, float] = {}
    if not auction_pass:
        parts["竞额20日比"] = 0.0
    else:
        extra = min(max((auction_ratio_max - gate_max) / max(gate_max, 0.01), 0.0), 1.0) * 10.0
        ratio_score = 15.0 + extra
        parts["竞额20日比"] = round(dim_weighted("竞额20日比", min(25.0, ratio_score)), 1)
    parts["开盘%"] = round(dim_weighted("开盘%", float(opening_score_value)), 1)

    parts["情绪"] = round(
        dim_weighted(
            "情绪",
            min(
                20.0,
                max(0.0, 20.0 * (1.0 - min(max(limit_down_intensity, 0.0), 100.0) / 100.0))
                + (4.0 if rotation_window else 0.0),
            ),
        ),
        1,
    )

    turnover_score = {"健康区": 20.0, "灰色区": 12.0, "缩量": 8.0, "死亡换手": 0.0}.get(turnover, 0.0)
    parts["分歧"] = round(dim_weighted("分歧", 0.0 if one_word else turnover_score), 1)

    theme_points = float(theme_placeholder)
    if not one_word:
        theme_points = min(10.0, theme_placeholder + (2.0 if rotation_window else 0.0))
    parts["题材/地位"] = round(dim_weighted("题材/地位", theme_points), 1)
    parts["个股情绪"] = round(dim_weighted("个股情绪", float(stock_emotion_score)), 1)
    parts["事件驱动"] = round(dim_weighted("事件驱动", float(event_score)), 1)
    parts["昨日成交额"] = round(prev_amount_score(prev_amount), 1)

    total = round(sum(parts.values()), 1)
    if one_word:
        base = "C"
    elif not auction_pass or turnover == "死亡换手":
        base = "D"
    else:
        base = grade_for_score(total)
    return total, parts, base


def gain_space(bars: list[Bar], idx: int) -> dict:
    """Remaining gain before the 10-day 100% / 30-day 200% regulatory lines.

    The percentages are close-to-close cumulative gains ending at T-1 and are
    deliberately excluded from the score.
    """
    out: dict[str, float | str | None] = {
        "gain_10d_pct": None,
        "gain_30d_pct": None,
        "room_10d_pct": None,
        "room_30d_pct": None,
    }
    labels: list[str] = []
    for days, threshold in ((10, 100.0), (30, 200.0)):
        base_idx = idx - days
        if base_idx < 0:
            labels.append("-")
            continue
        base = bars[base_idx].close
        current = bars[idx].close
        if base <= 0 or current <= 0:
            labels.append("-")
            continue
        gain = (current / base - 1.0) * 100.0
        target_price = base * (1.0 + threshold / 100.0)
        room = (target_price / current - 1.0) * 100.0
        out[f"gain_{days}d_pct"] = round(gain, 2)
        out[f"room_{days}d_pct"] = round(room, 2)
        if room <= 0:
            labels.append("超限")
        else:
            labels.append(f"{room:.1f}%")
    out["label"] = "/".join(labels)
    return out


def build_candidate(
    stock: StockFile,
    stage: int,
    target_date: int,
    market: MarketDay,
    leader: LeaderState | None,
    auction_source: AuctionSource,
    equity_source: "EquitySource",
    name: str = "",
    next_date_override: int | None = None,
) -> dict:
    bars = read_day_file(stock.path)
    dates = [bar.date for bar in bars]
    idx = bisect_left(dates, target_date)
    if idx >= len(bars) or bars[idx].date != target_date or idx == 0:
        return {"code": stock.code, "error": "no target bar"}
    if idx < 60:
        return {"code": stock.code, "error": "listed less than 60 trading days"}
    bar = bars[idx]
    prev_close = bars[idx - 1].close
    if bar.close < limit_price(prev_close, limit_pct(stock.code)) - 0.005:
        return {"code": stock.code, "error": "not limit-up on target"}
    if idx + 1 < len(bars):
        next_bar = bars[idx + 1]
    elif next_date_override:
        # Live 09:25 path: today's daily bar does not exist yet, but the
        # candidate only needs the next date to fetch the opening match.
        next_bar = Bar(
            date=next_date_override,
            open=bar.close,
            high=bar.close,
            low=bar.close,
            close=bar.close,
            amount=0.0,
            volume=0.0,
        )
    else:
        return {"code": stock.code, "error": "no next trading day"}
    one_word = bar.low >= limit_price(prev_close, limit_pct(stock.code)) - 0.005
    max20 = adjusted_twenty_day_max(bars, idx, stock.code)
    water = is_water_day(bars, idx, stock.code)
    gain_room = gain_space(bars, idx)

    try:
        turnover_result = equity_source.turnover_pct(stock.code, target_date, bar.volume)
    except Exception as exc:
        return {
            "code": stock.code,
            "stage": stage_label(stage),
            "error": f"share capital failed: {type(exc).__name__}: {exc}",
            "target_date": target_date,
            "next_date": next_bar.date,
        }
    if turnover_result is None:
        return {"code": stock.code, "error": "no historical share capital"}
    turnover, share_capital = turnover_result
    # 流通市值口径：该交易日收盘价 × 该交易日当时的流通股本（历史股本，
    # 由 shares.EquitySource 从通达信股本变迁按变动日取 <= 目标日最近一条）。
    # 档位、门槛、换手率都由同一个 share_capital 派生，保证整行同源。
    float_mcap_yi = share_capital.circulating_shares * bar.close / 1e8
    tier = cap_tier(float_mcap_yi)
    turnover_state_value = turnover_state(turnover, tier)

    try:
        auction = auction_source.fetch(stock.code, date_int_to_str(next_bar.date))
    except Exception as exc:  # keep a whole report/backtest alive on one bad page
        return {
            "code": stock.code,
            "stage": stage_label(stage),
            "error": f"auction fetch failed: {type(exc).__name__}: {exc}",
            "target_date": target_date,
            "next_date": next_bar.date,
        }
    if auction is None:
        return {
            "code": stock.code,
            "stage": stage_label(stage),
            "error": "no 09:25 auction",
            "target_date": target_date,
            "next_date": next_bar.date,
        }

    auction_ratio_prev = auction.amount_yuan / bar.amount * 100.0 if bar.amount > 0 else 0.0
    auction_ratio_max = auction.amount_yuan / max20.amount * 100.0 if max20.amount > 0 else 0.0
    gate = float(tier.auction_low)
    auction_pass = auction_ratio_max >= gate
    auction_label = auction_state(auction_ratio_max, tier)
    open_gap = (auction.price / bar.close - 1.0) * 100.0

    higher_leader = bool(leader and leader.height > stage)
    rotation_window = bool(leader and leader.rotation_window)
    leader_limit_down_break = bool(leader and leader.limit_down_break)
    leader_one_word = bool(leader and leader.one_word_leader)
    leader_suspended = bool(leader and leader.suspended)
    emotion = stock_emotion(target_date, stock.code, stage)
    event = event_driver(target_date, market, emotion)
    score, score_parts, base = _score_candidate(
        auction_pass=auction_pass,
        auction_ratio_max=auction_ratio_max,
        gate_max=gate,
        open_gap=open_gap,
        turnover=turnover_state_value,
        one_word=one_word,
        rotation_window=rotation_window,
        limit_down_intensity=market.limit_down_intensity,
        stock_emotion_score=emotion["score"],
        event_score=event["score"],
        opening_score_value=opening_score(open_gap),
        prev_amount=float(bar.amount),
    )
    cap_value = stage_cap(stage)
    grade = cap_grade(base, cap_value)

    reasons: list[str] = []
    downgrades = 0
    upgrades = 0
    gap_label = "none"
    gap_steps = gap_grade_steps(open_gap)
    blocked = False
    if not auction_pass:
        reasons.append(f"竞额20日比 {auction_ratio_max:.2f}% < 分档门槛 {gate:.2f}%")
    else:
        reasons.append(f"竞额20日比 {auction_ratio_max:.2f}% ≥ 分档门槛 {gate:.2f}%")
    reasons.append(f"开盘 {open_gap:+.2f}%")
    reasons.append(emotion["reason"])
    if gain_room.get("gain_10d_pct") is not None:
        reasons.append(
            "涨幅空间：10日累计 {:+.1f}%（距100% {:+.1f}%）；30日累计 {:+.1f}%（距200% {:+.1f}%）".format(
                gain_room["gain_10d_pct"],
                gain_room["room_10d_pct"],
                gain_room["gain_30d_pct"],
                gain_room["room_30d_pct"],
            )
        )
    if gap_steps < 0:
        downgrades += 1
        gap_label = "down"
        if open_gap < -1:
            reasons.append("开盘低于 -1%，降一档")
        else:
            reasons.append("开盘高于 4%，降一档")
    elif gap_steps > 0:
        upgrades += 1
        gap_label = "up"
        reasons.append("开盘在 1%–4%，加一档")
    reasons.append(f"昨换手 {turnover:.2f}% → {turnover_state_value}")
    if one_word:
        reasons.append("T-1 一字板，只观察")

    if higher_leader and leader is not None:
        if leader_limit_down_break:
            blocked = True
            reasons.append("最高板跌停断板，禁止")
        elif leader_suspended:
            reasons.append("最高板停牌中，停牌期间无更高板，仍在空间板周期，不降档")
        elif rotation_window:
            reasons.append("最高板处于天量次日/断板窗口，不降档")
        elif leader_one_word:
            grade = cap_grade(grade, "B")
            reasons.append("最高板一字为主，名义补涨，封顶 B")
        else:
            downgrades += 1
            reasons.append("更高板仍在吸金，降一档")

    if water and auction_pass:
        downgrades += 1
        reasons.append("目标 T-1 地天板/深水反包且竞价通过，再降一档")

    prev_amount_yi = float(bar.amount) / 1e8
    if prev_amount_yi > PREV_AMOUNT_ZERO_YI:
        downgrades += 1
        reasons.append(
            f"昨日成交额 {prev_amount_yi:.2f}亿 > {PREV_AMOUNT_ZERO_YI:.0f}亿，降一档"
        )

    risk_gate = "通过"
    if market.limit_down_intensity >= 60 or market.high_board_limit_down >= 2:
        risk_gate = "禁止"
        blocked = True
        reasons.append(
            f"跌停强度 {market.limit_down_intensity}，高位跌停 {market.high_board_limit_down}，禁止"
        )
    elif market.limit_down_intensity >= 40:
        risk_gate = "降档"
        downgrades += 1
        reasons.append(f"跌停强度 {market.limit_down_intensity}，降一档")

    if upgrades:
        grade = upgrade_grade(grade, upgrades)
        grade = cap_grade(grade, cap_value)
    if downgrades:
        grade = downgrade_grade(grade, downgrades)
    if blocked:
        grade = "D"

    if blocked:
        action = "禁止"
    elif one_word:
        action = "观察（一字）"
    elif grade in ("A", "B"):
        action = "可研究"
    elif grade == "C":
        action = "观察"
    else:
        action = "不做"

    if max20.water_days:
        reasons.append(
            f"20日最大额 {max20.amount / 1e8:.2f} 亿"
            f"（{max20.date}，含 {max20.water_days} 个地天板/水下日，已按 50% 折算）"
        )

    reasons.append(
        "评分 {:.1f}（竞额20日比 {:.1f} / 市场情绪 {:.1f} / 分歧 {:.1f} / 题材 {:.1f} / 个股情绪 {:.1f} / 事件驱动 {:.1f} / 开盘 {:.1f} / 昨日成交额 {:.1f}；题材暂为占位）".format(
            score,
            score_parts.get("竞额20日比", 0),
            score_parts.get("情绪", 0),
            score_parts.get("分歧", 0),
            score_parts.get("题材/地位", 0),
            score_parts.get("个股情绪", 0),
            score_parts.get("事件驱动", 0),
            score_parts.get("开盘%", 0),
            score_parts.get("昨日成交额", 0),
        )
    )

    return {
        "code": stock.code,
        "name": name,
        "market": stock.market,
        "stage": stage_label(stage),
        "stage_n": stage,
        "target_date": target_date,
        "next_date": next_bar.date,
        "next_date_text": date_int_to_str(next_bar.date),
        "board_height": stage,
        "one_word": one_word,
        "water_day": water,
        "float_mcap_yi": round(float_mcap_yi, 2),
        "cap_tier": tier.name,
        "turnover_pct": round(turnover, 2),
        "turnover_state": turnover_state_value,
        "share_capital_date": share_capital.date.isoformat() if share_capital.date else None,
        "share_capital_source": share_capital.source,
        "auction_price": round(auction.price, 2),
        "prev_amount": round(bar.amount, 2),
        "auction_amount": round(auction.amount_yuan, 2),
        "auction_ratio_prev": round(auction_ratio_prev, 2),
        "auction_ratio_max": round(auction_ratio_max, 2),
        "auction_gate": round(gate, 2),
        "auction_gate_max": round(gate, 2),
        "auction_pass": auction_pass,
        "auction_label": auction_label,
        "open_gap_pct": round(open_gap, 2),
        "stock_emotion": emotion["label"],
        "stock_emotion_score": round(emotion["score"], 1),
        "stock_sector": emotion["sector"],
        "stock_emotion_detail": emotion["detail"],
        "stock_emotion_items": emotion.get("items", []),
        "assist_count": emotion["count"],
        "event_driver": event["label"],
        "event_score": round(event["score"], 1),
        "event_detail": event["detail"],
        "event_news": event["news"],
        "event_market": event["market"],
        "event_hotspot": event["hotspot"],
        "assist_codes": emotion["assists"],
        "gain_space": gain_room["label"],
        "gain_10d_pct": gain_room["gain_10d_pct"],
        "gain_30d_pct": gain_room["gain_30d_pct"],
        "room_10d_pct": gain_room["room_10d_pct"],
        "room_30d_pct": gain_room["room_30d_pct"],
        "max20_amount": round(max20.amount, 2),
        "max20_raw_amount": round(max20.raw_amount, 2),
        "max20_date": max20.date,
        "max20_water_days": max20.water_days,
        "leader": None if leader is None else {
            "code": leader.code,
            "height": leader.height,
            "sixty_day_max_amount": round(leader.sixty_day_max_amount, 2),
            "sixty_day_max_date": leader.sixty_day_max_date,
            "is_60d_max_next_day": leader.is_60d_max_next_day,
            "one_word_ratio": round(leader.one_word_ratio, 3),
            "post_break_days": leader.post_break_days,
            "re_sealed": leader.re_sealed,
            "valid": leader.valid,
            "limit_down_break": leader.limit_down_break,
            "rotation_window": leader.rotation_window,
            "suspended": leader.suspended,
            "last_trade_date": leader.last_trade_date,
        },
        "higher_leader": higher_leader,
        "rotation_window": rotation_window,
        "risk_gate": risk_gate,
        "limit_down_intensity": market.limit_down_intensity,
        "high_board_limit_down": market.high_board_limit_down,
        "score": score,
        "score_parts": score_parts,
        "gap_adjustment": gap_label,
        "gap_upgrades": upgrades,
        "score_note": "题材/地位暂用占位分，等题材数据接入后替换",
        "base_grade": base,
        "stage_cap": cap_value,
        "downgrades": downgrades,
        "final_grade": grade,
        "action": action,
        "position_pct": position_for(score, action),
        "position_text": f"{position_for(score, action)}%",
        "reasons": reasons,
    }


def build_report(
    target_date: int,
    stages: Sequence[int] | None = None,
    next_date_override: int | None = None,
    trade_date: int | None = None,
    display_date: int | None = None,
    with_lhb: bool = False,
    auto_extend: bool = True,
) -> dict:
    stocks = list_stocks(TDX_VIPDOC)
    stock_map = _stock_map(stocks)
    market = load_market_day(target_date, TDX_VIPDOC)
    apply_suspended_space_boards(market, stocks, target_date)
    leader = leader_state(market, stock_map, target_date)
    # 阶段集合：2→3 / 3→4 / 4→5 三段常驻；auto_extend 时再跟随当日天梯最高板，
    # 把 >=5 的实际高度一并纳入（当日最高 7 板 → 可能出 7→8）。
    # 高阶阶段只在当日真有该高度个股时加入，无票自然不显示空段。
    base = {2, 3, 4}
    if stages:
        base |= {int(s) for s in stages if int(s) >= 2}
    if auto_extend:
        base |= {h for h in market.height_stocks if h >= 5}
    stages = sorted(base)
    candidates: list[dict] = []
    from .shares import EquitySource

    with AuctionSource(timeout=5.0) as auction_source, EquitySource(timeout=5.0) as equity_source:
        for stage in stages:
            for code in market.height_stocks.get(stage, []):
                stock = stock_map.get(code)
                if stock is None:
                    continue
                name = name_for(code)
                if is_st_name(name):
                    continue
                candidates.append(
                    build_candidate(
                        stock,
                        stage,
                        target_date,
                        market,
                        leader,
                        auction_source,
                        equity_source,
                        name,
                        next_date_override=next_date_override,
                    )
                )
    actionable = [
        row for row in candidates
        if not row.get("error") and row.get("action") == "可研究"
    ]
    stage_priority = None
    if actionable:
        highest_actionable = max(int(row.get("stage_n", 0)) for row in actionable)
        affected = 0
        for row in candidates:
            if row.get("error") or row.get("one_word"):
                continue
            if int(row.get("stage_n", 0)) <= 0 or int(row.get("stage_n", 0)) >= highest_actionable:
                continue
            if row.get("final_grade") not in ("A", "B", "C"):
                continue
            row["final_grade"] = downgrade_grade(row["final_grade"], 1)
            row["downgrades"] = int(row.get("downgrades", 0)) + 1
            row["reasons"].append(
                f"更高梯队（{highest_actionable}板）已有可做标的，低梯队降一档"
            )
            row["action"] = _action_for_grade(
                row["final_grade"],
                one_word=bool(row.get("one_word")),
                blocked=row.get("risk_gate") == "禁止",
            )
            row["position_pct"] = position_for(row["score"], row["action"])
            row["position_text"] = f"{row['position_pct']}%"
            row["stage_priority_downgrade"] = True
            affected += 1
        stage_priority = {"highest_actionable": highest_actionable, "affected": affected}

    # 打板纪律：时机/参与位/结构定性/加分项，必须在所有档位调整之后统一补
    annotate_discipline(candidates)

    next_date_text = next(
        (row.get("next_date_text", "") for row in candidates if row.get("next_date_text")),
        date_int_to_str(next_date_override) if next_date_override else "",
    )
    # 封单额双口径 + 封流比（口径①竞价封单/口径②实时封单）；网络异常全部降级为「—」
    attach_seal_amounts(candidates, next_date_text)
    # 龙虎榜 + T+1 再板率（东财 T 日盘后榜单 + 本地 TDX 日线）；默认关闭，
    # 因为 history.py 会按日期循环调用，不能对每个历史日期都发网络请求。
    lhb_info = attach_lhb(candidates, target_date) if with_lhb else None
    effective_trade = trade_date or next_date_override
    if not effective_trade and next_date_text:
        try:
            effective_trade = int(next_date_text.replace("-", ""))
        except ValueError:
            effective_trade = None
    leader_break = broken_leader_state(effective_trade, stock_map)
    report_market = market
    report_leader = leader
    if display_date:
        report_market = load_market_day(display_date, TDX_VIPDOC)
        apply_suspended_space_boards(report_market, stocks, display_date)
        report_leader = leader_state(report_market, stock_map, display_date)
    return {
        "target_date": target_date,
        "next_date_text": next_date_text,
        "display_date": display_date or target_date,
        "leader_break": leader_break,
        "stage_priority": stage_priority,
        "market": report_market.as_dict(),
        "leader": None if report_leader is None else {
            "code": report_leader.code,
            "height": report_leader.height,
            "sixty_day_max_amount": round(report_leader.sixty_day_max_amount, 2),
            "sixty_day_max_date": report_leader.sixty_day_max_date,
            "is_60d_max_next_day": report_leader.is_60d_max_next_day,
            "one_word_ratio": round(report_leader.one_word_ratio, 3),
            "post_break_days": report_leader.post_break_days,
            "re_sealed": report_leader.re_sealed,
            "valid": report_leader.valid,
            "limit_down_break": report_leader.limit_down_break,
            "rotation_window": report_leader.rotation_window,
            "suspended": report_leader.suspended,
            "last_trade_date": report_leader.last_trade_date,
        },
        "candidates": candidates,
        # 盘前四问：只读本地数据（market_stats.json + TDX 日线），不发网络请求
        "four_questions": four_questions(target_date, report_market, candidates, stock_map),
        # 封单额双口径说明，渲染层直接原文输出，避免报告声称有本地不存在的「收盘封单额」
        "seal_note": SEAL_NOTE,
        # 龙虎榜口径：龙虎榜＝东财 T 日盘后披露（竞价模式下当日榜通常「未披露」）；
        # T+1 再板率＝本地 TDX 日线「涨停日次日再涨停」比例。未开启时保持 None。
        "lhb": lhb_info,
    }


# ===== 盘前四问 / 封单额（2026-10 新增）=====
# 四问口径：全部只读本地数据 —— data/market_stats.json（由本地 TDX 日线的
# 一次性扫描生成，含 1262 个交易日的涨停/连板统计）+ longtou/tdx.py 的日线。
FQ_WINDOW = 250          # 历史回溯窗口：近 250 个交易日
FQ_MEMORY_WINDOW = 20    # 题材记忆窗口：近 20 个交易日
SEAL_MAX_ROWS = 24       # 单份报告最多取封单的行数（防御性上限，候选池一般 <15）

# 封单额口径声明（原样落到报告里）：两条口径 + 明确缺口，任何一条都不许含糊。
SEAL_NOTE = (
    "口径①竞价封单(万)＝09:25 开盘集合竞价未匹配量 × 竞价价 × 100（手→股），"
    "来源 eltdx 0x056a 竞价序列的 AuctionPoint.unmatched_volume；"
    "服务端只对当日返回 09:15–09:25 序列，历史日期返回的是 14:57–15:00 收盘集合竞价序列，"
    "故历史日期一律「—」，不用 14:5x 数据冒充 9:25 封单。"
    "口径②实时封单(万)＝五档买一价 × 买一量 × 100，来源 eltdx 0x0547 五档（quotes.get_depth），"
    "只在盘中（周一~周五 09:15–11:30 / 13:00–15:00）取数，收盘/非交易时段「—」。"
    "封流比＝封单额 ÷ 流通市值 × 100%（两边都换算成元），优先用竞价封单，缺失时退实时封单；"
    "竞价未匹配量在卖盘一侧（卖盘残余）不算封单；封流比 0<x<0.01% 记「<0.01%」，不写成 0.00%。"
    "缺口：历史「收盘封单额」本地无数据源（TDX 日线不含分笔/委托队列，overlay 也没有），本报告不提供该项。"
)


def _mget(market: object, key: str, default: int = 0) -> int:
    """market 可能是 MarketDay 对象，也可能是报告里的 as_dict() 字典；取整数不抛异常。"""
    value = market.get(key) if isinstance(market, dict) else getattr(market, key, None)
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _stats_pairs() -> list[tuple[int, dict]]:
    """market_stats.json 的 (日期, 当日统计) 列表，按日期升序；脏键跳过。"""
    pairs: list[tuple[int, dict]] = []
    for key, row in _market_stats().items():
        if not isinstance(row, dict):
            continue
        try:
            pairs.append((int(key), row))
        except (TypeError, ValueError):
            continue
    pairs.sort(key=lambda item: item[0])
    return pairs


def _emotion_question(pairs: list[tuple[int, dict]], target_date: int) -> dict:
    """四问①：近 3 个交易日（T-1/T-2/T-3）涨停家数 + 最高板，给一句情绪判断。

    数据来源：data/market_stats.json 的 limit_up / max_height / broken_limit / height_counts，
    口径与页面「市场」段完全同源。
    """
    known = [(date, row) for date, row in pairs if date <= target_date]
    if not known:
        return {"missing": True, "days": [],
                "verdict": "market_stats.json 无目标日及之前的数据，本项不构成结论"}
    days = [
        {
            "date": date,
            "limit_up": int(row.get("limit_up", 0) or 0),
            "limit_down": int(row.get("limit_down", 0) or 0),
            "broken_limit": int(row.get("broken_limit", 0) or 0),
            "max_height": int(row.get("max_height", 0) or 0),
            "height_counts": row.get("height_counts") or {},
        }
        for date, row in known[-3:]
    ]
    if len(days) < 2:
        verdict = f"仅 {len(days)} 个交易日样本，不做情绪判断"
    else:
        cur, prev = days[-1], days[-2]
        height_delta = cur["max_height"] - prev["max_height"]
        limit_delta = cur["limit_up"] - prev["limit_up"]
        height_text = "高度抬升（修复）" if height_delta > 0 else (
            "高度下降（退潮）" if height_delta < 0 else "高度维持"
        )
        count_text = "涨停家数回升" if limit_delta > 0 else (
            "涨停家数回落" if limit_delta < 0 else "涨停家数持平"
        )
        verdict = (
            f"{height_text}、{count_text}"
            f"（涨停 {prev['limit_up']}→{cur['limit_up']} 家，"
            f"最高板 {prev['max_height']}→{cur['max_height']} 板）"
        )
    return {"missing": False, "days": days, "verdict": verdict}


def _catalyst_question(candidates: list[dict]) -> dict:
    """四问②：盘前催化 —— 按题材汇总候选池的 event_driver，按集中度排序。

    没有事件数据时明确写「事件驱动数据缺失，本项不构成结论」，绝不编造题材催化。
    题材分组复用 theme_key()（个股情绪题材优先，否则事件标签前半段）。
    """
    rows = [row for row in candidates if not row.get("error")]
    groups: dict[str, dict] = {}
    for row in rows:
        theme = theme_key(row)
        label = str(row.get("event_driver") or "").strip()
        item = groups.setdefault(theme, {"theme": theme, "drivers": [], "codes": []})
        if label and label not in item["drivers"]:
            item["drivers"].append(label)
        item["codes"].append(str(row.get("code") or ""))
    themes = sorted(groups.values(), key=lambda item: (-len(item["codes"]), item["theme"]))
    has_event = any(str(row.get("event_driver") or "").strip() for row in rows)
    if not rows:
        verdict = "候选池为空，无题材可汇总"
    elif not has_event:
        verdict = "事件驱动数据缺失，本项不构成结论"
    else:
        verdict = (
            f"{len(themes)} 个题材有事件标签，按候选集中度排序"
            "（只是候选池内的题材分布，不代表全市场催化）"
        )
    return {"missing": not has_event, "themes": themes[:8], "verdict": verdict}


def _bar_height_check(market: object, stock_map: dict | None, target_date: int) -> str:
    """用本地日线的 board_events 复核当前最高板的连板高度，作为四问③的独立校验。"""
    if not stock_map:
        return "日线复核不可用（未传入股票表）"
    leader_codes = market.get("leader_codes") if isinstance(market, dict) else getattr(market, "leader_codes", None)
    codes = [str(code) for code in (leader_codes or [])]
    if not codes:
        return "日线复核不可用（无最高板代码）"
    code = codes[0]
    stock = stock_map.get(code)
    if stock is None:
        return f"日线复核不可用（{code} 无本地日线）"
    try:
        bars = read_day_file(stock.path)
    except Exception as exc:
        return f"日线复核失败（{type(exc).__name__}）"
    height = 0
    last_height = 0
    for event in board_events(stock, bars):
        last_height = event.height
        if event.bar.date == target_date:
            height = event.height
            break
    if not height:
        # 目标日停牌（无当日 bar）时退回最近一次涨停事件的连板高度，并如实标注
        if last_height:
            return f"{code} 目标日无涨停日线（可能停牌），最近一次涨停连板 {last_height} 板"
        return f"{code} 日线无涨停事件"
    return f"日线 board_events 复核：{code} 连板 {height} 板"


def _backtest_question(pairs: list[tuple[int, dict]], target_date: int, market: object,
                       stock_map: dict | None = None) -> dict:
    """四问③：历史回溯 —— 近 250 交易日内「最高板＝当前最高板」出现后，次日涨停家数平均变化。

    主口径：market_stats.json 的 limit_up 序列（该文件本身就是本地 TDX 日线的一次性扫描结果），
    因此同高度样本的次日变化可在无网络、无重扫日线的前提下复现；
    当前最高板的高度再用 tdx.board_events 对个股日线复核一次（见 bar_check）。
    """
    known = [(date, row) for date, row in pairs if date <= target_date]
    window = known[-FQ_WINDOW:]
    height = _mget(market, "max_height")
    result = {
        "height": height,
        "window": len(window),
        "samples": 0,
        "avg_change": None,
        "up": 0,
        "flat": 0,
        "down": 0,
        "detail": [],
        "bar_check": _bar_height_check(market, stock_map, target_date),
    }
    if not window or height <= 0:
        result["verdict"] = "数据不足（无窗口或无最高板），本项不构成结论"
        return result
    changes: list[int] = []
    for index in range(len(window) - 1):
        date, row = window[index]
        if int(row.get("max_height", 0) or 0) != height:
            continue
        next_date, next_row = window[index + 1]
        change = int(next_row.get("limit_up", 0) or 0) - int(row.get("limit_up", 0) or 0)
        changes.append(change)
        if len(result["detail"]) < 3:
            result["detail"].append({
                "date": date,
                "next_date": next_date,
                "limit_up": int(row.get("limit_up", 0) or 0),
                "next_limit_up": int(next_row.get("limit_up", 0) or 0),
                "change": change,
            })
    up = sum(1 for value in changes if value > 0)
    down = sum(1 for value in changes if value < 0)
    result.update({
        "samples": len(changes),
        "up": up,
        "down": down,
        "flat": len(changes) - up - down,
    })
    if not changes:
        result["verdict"] = f"近 {len(window)} 个交易日内无「最高板 {height} 板」样本，本项无数值结论"
        return result
    average = sum(changes) / len(changes)
    result["avg_change"] = round(average, 1)
    result["verdict"] = (
        f"最高板 {height} 板出现后，次日涨停家数平均变化 **{average:+.1f} 家**"
        f"（样本 {len(changes)} 个；上涨 {up} / 持平 {len(changes) - up - down} / 下跌 {down}）"
    )
    return result


def _memory_question(candidates: list[dict], pairs: list[tuple[int, dict]], target_date: int) -> dict:
    """四问④：题材记忆 —— 候选池各题材的成员股近 20 个交易日是否上过板。

    本地没有历史题材快照，因此按「题材成员股是否出现在近 20 日 height_stocks 里」推断新/老题材，
    口径在 verdict 里写明，避免被误读为题材本身的连板序列。
    """
    known = [(date, row) for date, row in pairs if date <= target_date]
    window = known[-FQ_MEMORY_WINDOW:]
    hits: dict[str, int] = {}
    for _date, row in window:
        heights = row.get("height_stocks") or {}
        for codes in heights.values():
            for code in codes or ():
                text = str(code)
                hits[text] = hits.get(text, 0) + 1
    _pool_max, theme_stats = pool_stats(candidates)
    items: list[dict] = []
    for theme, stat in theme_stats.items():
        codes = [str(code) for code in (stat.get("codes") or [])]
        total = sum(hits.get(code, 0) for code in codes)
        repeated = [code for code in codes if hits.get(code, 0) > 0]
        items.append({
            "theme": theme,
            "codes": codes,
            "hits": total,
            "repeated": repeated,
            "label": (
                f"老题材反复（近 {len(window)} 日 {total} 次上板）"
                if total > 0
                else f"新题材（近 {len(window)} 日未上板）"
            ),
        })
    items.sort(key=lambda item: (-item["hits"], -len(item["codes"]), item["theme"]))
    old = sum(1 for item in items if item["hits"] > 0)
    if not items:
        verdict = "候选池为空，本项不构成结论"
    else:
        verdict = (
            f"{len(items)} 个题材中 {old} 个近 {len(window)} 日有成员上板（老题材反复），"
            f"{len(items) - old} 个为新题材；"
            f"口径＝题材成员股是否出现在近 {len(window)} 日涨停名单（height_stocks）中"
        )
    return {"missing": not items, "themes": items[:8], "window": len(window), "verdict": verdict}


def four_questions(
    target_date: int,
    market: object,
    candidates: list[dict],
    stock_map: dict | None = None,
) -> dict:
    """盘前四问总入口：情绪温度 / 盘前催化 / 历史回溯 / 题材记忆。

    全部只读本地数据，任何一项异常都降级成一句说明，不抛异常、不阻断报告。
    """
    try:
        pairs = _stats_pairs()
    except Exception:
        pairs = []
    answers: dict = {}

    def _safe(name: str, build) -> None:
        try:
            answers[name] = build()
        except Exception as exc:
            answers[name] = {
                "missing": True,
                "verdict": f"本项统计失败：{type(exc).__name__}: {exc}",
            }

    _safe("emotion", lambda: _emotion_question(pairs, target_date))
    _safe("catalyst", lambda: _catalyst_question(candidates))
    _safe("backtest", lambda: _backtest_question(pairs, target_date, market, stock_map))
    _safe("memory", lambda: _memory_question(candidates, pairs, target_date))
    return answers


def attach_seal_amounts(rows: list[dict], trading_date: str = "", *, max_rows: int = SEAL_MAX_ROWS) -> None:
    """给候选行补「竞价封单/实时封单/封流比」三列与逐行取数说明。

    口径见 depth.py 模块注释；任何网络/接口/解析异常都降级为「—」（None），
    保证整份报告永远能渲染出来。错误行只留占位。
    """
    for row in rows:
        if row.get("error"):
            row.setdefault("竞价封单", None)
            row.setdefault("实时封单", None)
            row.setdefault("封流比", None)
            continue
    targets = [row for row in rows if not row.get("error")][:max_rows]
    if not targets:
        return
    try:
        with SealSource(timeout=5.0) as source:
            for row in targets:
                code = str(row.get("code") or "")
                # 撮合日：优先报告级交易日，其次逐行 next_date（live 09:25 只有 override 才有）
                date_text = trading_date
                if not date_text and row.get("next_date"):
                    try:
                        date_text = date_int_to_str(int(row["next_date"]))
                    except (TypeError, ValueError):
                        date_text = ""
                if code and date_text:
                    try:
                        auction_yuan, auction_note = source.auction_locked(code, date_text)
                    except Exception as exc:
                        auction_yuan, auction_note = None, f"{type(exc).__name__}: {exc}"
                else:
                    auction_yuan, auction_note = None, ("无代码" if not code else "撮合日缺失")
                try:
                    realtime_yuan, realtime_note = (
                        source.realtime_locked(code) if code else (None, "无代码")
                    )
                except Exception as exc:
                    realtime_yuan, realtime_note = None, f"{type(exc).__name__}: {exc}"
                row["竞价封单"] = auction_yuan
                row["竞价封单说明"] = auction_note
                row["实时封单"] = realtime_yuan
                row["实时封单说明"] = realtime_note
                row["封流比"] = seal_flow_ratio(
                    auction_yuan if auction_yuan is not None else realtime_yuan,
                    row.get("float_mcap_yi"),
                )
    except Exception as exc:  # client 创建失败等整体降级
        note = f"封单取数不可用：{type(exc).__name__}: {exc}"
        for row in targets:
            row.setdefault("竞价封单", None)
            row.setdefault("实时封单", None)
            row.setdefault("封流比", None)
            row.setdefault("竞价封单说明", note)
            row.setdefault("实时封单说明", note)


# ===== 龙虎榜 + T+1 再板率（2026-10 新增）=====
LHB_MAX_ROWS = 12  # 单份报告最多查龙虎榜/再板率的行数（防御性上限，候选池一般 <15）


def attach_lhb(rows: list[dict], target_date: int, *, max_rows: int = LHB_MAX_ROWS) -> dict:
    """给候选行补「龙虎榜 / 龙虎榜净买亿 / 龙虎榜原因 / T+1 再板率」四个字段。

    口径：
    - 龙虎榜＝东财当日盘后披露的榜单（T 日收盘后才有，竞价模式下当日榜一律「未披露」）；
    - T+1 再板率＝本地 TDX 日线统计「涨停日次日再涨停」的比例，纯本地、不联网。

    断网/接口变更/解析失败全部降级：错误写进返回值的 ``error``，绝不抛；
    单票再板率失败只影响该行（置 None），不影响其它行。
    """
    from . import lhb  # 延迟导入：避免顶层循环导入，也避免离线环境下拖慢 engine 导入

    day = date_int_to_str(target_date)
    result: dict = {"date": day, "published": False, "count": 0, "error": None}
    stocks: dict[str, dict] = {}
    try:
        stocks = {
            str(item.get("code") or ""): item
            for item in lhb.fetch_billboard(day, timeout=8.0)
            if item.get("code")
        }
        result["published"] = bool(stocks)
        result["count"] = len(stocks)
    except Exception as exc:  # 网络/接口/解析异常：报告继续渲染
        result["error"] = f"{type(exc).__name__}: {exc}"

    # 榜单已发布时，没上榜的票写「未上榜」；榜单为空（当日盘后未出）写「未披露」
    miss_text = "未上榜" if stocks else "未披露"
    for row in [row for row in rows if not row.get("error")][:max_rows]:
        code = str(row.get("code") or "")
        hit = stocks.get(code)
        row["龙虎榜"] = "上榜" if hit else miss_text
        row["龙虎榜净买亿"] = round(float(hit.get("net_amt") or 0.0) / 1e8, 2) if hit else None
        row["龙虎榜原因"] = str(hit.get("reason") or "") if hit else ""
        try:
            row["T1再板率"] = lhb.t1_relimit_rate(code) if code else None
        except Exception:  # 缺 .day 文件/脏数据：本行降级为「—」
            row["T1再板率"] = None
    return result
