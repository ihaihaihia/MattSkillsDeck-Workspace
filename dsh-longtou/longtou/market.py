"""Market-wide limit-up / limit-down snapshot from local TDX daily bars."""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Iterable

from .rules import limit_down_intensity, one_word_run
from .tdx import TDX_VIPDOC, Bar, limit_pct, limit_price, list_stocks, read_day_file

STATS_PATH = Path(__file__).resolve().parents[1] / "data" / "market_stats.json"
_stats_cache: dict | None = None


@dataclass
class MarketDay:
    date: int
    limit_up: int = 0
    limit_down: int = 0
    touched_limit: int = 0
    broken_limit: int = 0
    one_word_limit_down: int = 0
    high_board_limit_down: int = 0
    consecutive_limit_down: int = 0
    max_height: int = 0
    leader_codes: list[str] = field(default_factory=list)
    height_counts: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    height_stocks: dict[int, list[str]] = field(default_factory=lambda: defaultdict(list))
    one_word_ratio_by_code: dict[str, float] = field(default_factory=dict)
    suspended_leader_codes: list[str] = field(default_factory=list)
    suspended_leader_heights: dict[str, int] = field(default_factory=dict)
    suspended_leader_last_dates: dict[str, int] = field(default_factory=dict)

    @property
    def seal_rate(self) -> float:
        total = self.limit_up + self.broken_limit
        return (self.limit_up / total * 100.0) if total else 0.0

    @property
    def limit_down_intensity(self) -> float:
        return limit_down_intensity(
            self.limit_down,
            self.one_word_limit_down,
            self.high_board_limit_down,
            self.consecutive_limit_down,
        )

    @property
    def leader_one_word_ratio(self) -> float:
        if not self.leader_codes:
            return 0.0
        return max(self.one_word_ratio_by_code.get(code, 0.0) for code in self.leader_codes)

    def as_dict(self) -> dict:
        return {
            "date": self.date,
            "limit_up": self.limit_up,
            "limit_down": self.limit_down,
            "touched_limit": self.touched_limit,
            "broken_limit": self.broken_limit,
            "seal_rate": round(self.seal_rate, 1),
            "one_word_limit_down": self.one_word_limit_down,
            "high_board_limit_down": self.high_board_limit_down,
            "consecutive_limit_down": self.consecutive_limit_down,
            "limit_down_intensity": self.limit_down_intensity,
            "max_height": self.max_height,
            "leader_codes": self.leader_codes,
            "height_counts": {str(k): v for k, v in sorted(self.height_counts.items())},
            "height_stocks": {str(k): v for k, v in sorted(self.height_stocks.items())},
            "leader_one_word_ratio": round(self.leader_one_word_ratio, 3),
            "suspended_leader_codes": self.suspended_leader_codes,
            "suspended_leader_heights": self.suspended_leader_heights,
            "suspended_leader_last_dates": self.suspended_leader_last_dates,
        }


def _state_at(stock_code: str, bars: list[Bar], target: int) -> tuple[int, bool, bool, None] | None:
    """Return (index, is_up, is_down, None) for a target date, or None."""
    dates = [bar.date for bar in bars]
    index = bisect_left(dates, target)
    if index >= len(bars) or bars[index].date != target:
        return None
    if index == 0:
        return None
    prev_close = bars[index - 1].close
    pct = limit_pct(stock_code)
    up = limit_price(prev_close, pct)
    down = round(prev_close * (1.0 - pct) + 1e-9, 2)
    bar = bars[index]
    return index, bar.close >= up - 0.005, bar.close <= down + 0.005, None


def _cached_stats() -> dict:
    global _stats_cache
    if _stats_cache is None:
        try:
            _stats_cache = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        except Exception:
            _stats_cache = {}
    return _stats_cache


def market_day_from_stats(row: dict) -> MarketDay:
    return MarketDay(
        date=int(row["date"]),
        limit_up=int(row.get("limit_up", 0)),
        limit_down=int(row.get("limit_down", 0)),
        touched_limit=int(row.get("touched_limit", 0)),
        broken_limit=int(row.get("broken_limit", 0)),
        one_word_limit_down=int(row.get("one_word_limit_down", 0)),
        high_board_limit_down=int(row.get("high_board_limit_down", 0)),
        consecutive_limit_down=int(row.get("consecutive_limit_down", 0)),
        max_height=int(row.get("max_height", 0)),
        leader_codes=list(row.get("leader_codes", [])),
        height_counts={int(key): int(value) for key, value in row.get("height_counts", {}).items()},
        height_stocks={
            int(key): list(value) for key, value in row.get("height_stocks", {}).items()
        },
        one_word_ratio_by_code={
            str(key): float(value) for key, value in row.get("one_word_ratio_by_code", {}).items()
        },
    )


def load_market_day(target_date: int, root: Path = TDX_VIPDOC) -> MarketDay:
    """Use the one-pass cache when available; fall back to a full scan."""
    row = _cached_stats().get(str(target_date))
    if row is not None:
        return market_day_from_stats(row)
    return scan_market(target_date, root)


def scan_market(target_date: int, root: Path = TDX_VIPDOC) -> MarketDay:
    day = MarketDay(date=target_date)
    for stock in list_stocks(root):
        try:
            bars = read_day_file(stock.path)
        except Exception:
            continue
        if len(bars) < 30:
            continue
        dates = [bar.date for bar in bars]
        index = bisect_left(dates, target_date)
        if index >= len(bars) or bars[index].date != target_date or index == 0:
            continue

        pct = limit_pct(stock.code)
        prev_close: float | None = None
        height = 0
        down_streak = 0
        prev_height = 0
        found: tuple[bool, bool, bool, bool] | None = None

        for i in range(index + 1):
            bar = bars[i]
            if prev_close is None:
                prev_close = bar.close
                continue
            target_up = limit_price(prev_close, pct)
            target_down = round(prev_close * (1.0 - pct) + 1e-9, 2)
            is_up = bar.close >= target_up - 0.005
            is_down = bar.close <= target_down + 0.005
            if i == index:
                found = (
                    is_up,
                    is_down,
                    bar.high >= target_up - 0.005,
                    bar.high <= target_down + 0.005,
                )
                prev_height = height
                break
            height = height + 1 if is_up else 0
            down_streak = down_streak + 1 if is_down else 0
            prev_close = bar.close

        if found is None:
            continue
        is_up, is_down, touched, one_word_down = found
        if is_up:
            height += 1
            day.limit_up += 1
            day.height_counts[height] += 1
            day.height_stocks[height].append(stock.code)
            if height > day.max_height:
                day.max_height = height
                day.leader_codes = [stock.code]
            elif height == day.max_height:
                day.leader_codes.append(stock.code)
            run = one_word_run(bars, stock.code, index)
            day.one_word_ratio_by_code[stock.code] = run.ratio
        if is_down:
            day.limit_down += 1
            down_streak += 1
            if one_word_down:
                day.one_word_limit_down += 1
            if prev_height >= 2:
                day.high_board_limit_down += 1
            if down_streak >= 2:
                day.consecutive_limit_down += 1
        else:
            down_streak = 0
        if touched:
            day.touched_limit += 1
            if not is_up:
                day.broken_limit += 1

    return day


def scan_market_days(start: int, end: int, root: Path = TDX_VIPDOC) -> dict[int, MarketDay]:
    """Convenience helper for small date ranges; each date rescans the files."""
    result: dict[int, MarketDay] = {}
    # The current CLI only needs a single date; this helper is for small ranges.
    for stock in list_stocks(root):
        try:
            bars = read_day_file(stock.path)
        except Exception:
            continue
        for bar in bars:
            if start <= bar.date <= end:
                result.setdefault(bar.date, MarketDay(date=bar.date))
    for date_value in sorted(result):
        result[date_value] = scan_market(date_value, root)
    return result
