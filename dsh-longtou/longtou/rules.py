"""Pure rule primitives for the v0.5 leader strategy.

No I/O here: callers pass bars, amounts, and market-cap values in, so the
rules can be unit-tested offline and reused by both the CLI and the DSH host.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .tdx import Bar, limit_pct, limit_price

GRADE_ORDER = ("A", "B", "C", "D")


@dataclass(frozen=True)
class CapTier:
    name: str
    min_mcap_yi: float
    max_mcap_yi: float | None
    auction_low: float
    auction_high: float
    turnover_low: float
    turnover_high: float
    death_turnover: float


CAP_TIERS: tuple[CapTier, ...] = (
    CapTier("超大盘", 2000, None, 1, 3, 1, 3, 8),
    CapTier("大盘股", 500, 2000, 3, 5, 3, 8, 12),
    CapTier("中盘股", 100, 500, 5, 8, 8, 15, 25),
    CapTier("小盘股", 30, 100, 8, 12, 15, 25, 35),
    CapTier("微盘股", 0, 30, 12, 15, 20, 30, 40),
)


def cap_tier(float_mcap_yi: float) -> CapTier:
    for tier in CAP_TIERS:
        if float_mcap_yi >= tier.min_mcap_yi and (
            tier.max_mcap_yi is None or float_mcap_yi < tier.max_mcap_yi
        ):
            return tier
    return CAP_TIERS[-1]


def auction_state(ratio_pct: float, tier: CapTier) -> str:
    """Hard gate is the table's lower bound; upper bound is only a warning."""
    if ratio_pct < tier.auction_low:
        return "不通过"
    if ratio_pct > tier.auction_high:
        return "通过但过热"
    return "通过"


def turnover_state(turnover_pct: float, tier: CapTier) -> str:
    if turnover_pct >= tier.death_turnover:
        return "死亡换手"
    if turnover_pct >= tier.turnover_high:
        return "灰色区"
    if turnover_pct >= tier.turnover_low:
        return "健康区"
    return "缩量"


def is_water_day(bars: Sequence[Bar], index: int, code: str) -> bool:
    """地天板 or open ≤ -5% with a limit-up close.

    These days get 50% weight when computing the 20-day max amount.
    """
    if index <= 0 or index >= len(bars):
        return False
    bar = bars[index]
    prev_close = bars[index - 1].close
    pct = limit_pct(code)
    up = limit_price(prev_close, pct)
    down = round(prev_close * (1.0 - pct) + 1e-9, 2)
    ditian = bar.low <= down + 0.005 and bar.close >= up - 0.005
    deep_reversal = bar.open <= prev_close * 0.95 + 1e-9 and bar.close >= up - 0.005
    return bool(ditian or deep_reversal)


@dataclass(frozen=True)
class MaxWindow:
    amount: float
    raw_amount: float
    date: int
    index: int
    is_latest: bool
    lookback: int
    water_days: int


def sixty_day_max(bars: Sequence[Bar], index: int, window: int = 60) -> MaxWindow:
    start = max(0, index - window + 1)
    segment = bars[start : index + 1]
    if not segment:
        return MaxWindow(0, 0, 0, -1, False, 0, 0)
    max_offset, max_bar = max(enumerate(segment), key=lambda pair: pair[1].amount)
    return MaxWindow(
        amount=max_bar.amount,
        raw_amount=max_bar.amount,
        date=max_bar.date,
        index=start + max_offset,
        is_latest=(start + max_offset == index),
        lookback=len(segment),
        water_days=0,
    )


def adjusted_twenty_day_max(bars: Sequence[Bar], index: int, code: str, window: int = 20) -> MaxWindow:
    """20-day max amount with 地天板 / deep-water days weighted at 50%."""
    start = max(0, index - window + 1)
    best_amount = -1.0
    best_raw = 0.0
    best_index = -1
    water_days = 0
    for i in range(start, index + 1):
        raw = bars[i].amount
        water = is_water_day(bars, i, code)
        amount = raw * 0.5 if water else raw
        if water:
            water_days += 1
        if amount > best_amount:
            best_amount = amount
            best_raw = raw
            best_index = i
    return MaxWindow(
        amount=max(best_amount, 0.0),
        raw_amount=best_raw,
        date=bars[best_index].date if best_index >= 0 else 0,
        index=best_index,
        is_latest=(best_index == index),
        lookback=index - start + 1,
        water_days=water_days,
    )


def board_flags(bars: Sequence[Bar], code: str) -> list[bool]:
    flags = [False] * len(bars)
    pct = limit_pct(code)
    for i in range(1, len(bars)):
        target = limit_price(bars[i - 1].close, pct)
        flags[i] = bars[i].close >= target - 0.005
    return flags


@dataclass(frozen=True)
class OneWordRun:
    run_length: int
    one_word_days: int
    ratio: float


def one_word_run(bars: Sequence[Bar], code: str, index: int) -> OneWordRun:
    """Measure the current consecutive-board run ending at ``index``."""
    flags = board_flags(bars, code)
    indices: list[int] = []
    i = index
    while i >= 1 and flags[i]:
        indices.append(i)
        i -= 1
    indices.reverse()
    one_word = 0
    pct = limit_pct(code)
    for i in indices:
        target = limit_price(bars[i - 1].close, pct)
        if bars[i].low >= target - 0.005:
            one_word += 1
    run_length = len(indices)
    return OneWordRun(
        run_length=run_length,
        one_word_days=one_word,
        ratio=(one_word / run_length) if run_length else 0.0,
    )


def limit_down_intensity(
    limit_down_count: int,
    one_word_limit_down_count: int,
    high_board_limit_down_count: int,
    consecutive_limit_down_count: int,
) -> float:
    """0-100 heuristic risk score; initial v0.5 calibration."""
    score = 0.0
    score += min(max(limit_down_count, 0) / 30.0, 1.0) * 40.0
    score += min(max(one_word_limit_down_count, 0) / 6.0, 1.0) * 25.0
    score += min(max(high_board_limit_down_count, 0) / 3.0, 1.0) * 25.0
    score += min(max(consecutive_limit_down_count, 0) / 5.0, 1.0) * 10.0
    return round(min(score, 100.0), 1)


def grade_for_score(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"


def downgrade_grade(grade: str, steps: int = 1) -> str:
    try:
        index = GRADE_ORDER.index(grade)
    except ValueError:
        return "D"
    return GRADE_ORDER[min(index + max(steps, 0), len(GRADE_ORDER) - 1)]


def upgrade_grade(grade: str, steps: int = 1) -> str:
    try:
        index = GRADE_ORDER.index(grade)
    except ValueError:
        return "D"
    return GRADE_ORDER[max(0, index - max(steps, 0))]


def gap_adjustment(open_gap: float) -> int:
    """+1 for a 1–4% open, -1 outside -1%..4%, else 0."""
    if open_gap < -1:
        return -1
    if 1 < open_gap <= 4:
        return 1
    if open_gap > 4:
        return -1
    return 0


def cap_grade(grade: str, cap: str) -> str:
    """Return the worse of two grades (A better than D)."""
    return grade if GRADE_ORDER.index(grade) >= GRADE_ORDER.index(cap) else cap


def stage_cap(stage: int) -> str:
    caps = {2: "B", 3: "A", 4: "B"}
    # 4 板及以上统一封顶 B（5 板以上不再额外压到 C 档）；3 板给 A 是「情绪确认窗口」的有意非单调设计
    if stage is not None and stage >= 5:
        return caps[4]
    return caps.get(stage, "C")
