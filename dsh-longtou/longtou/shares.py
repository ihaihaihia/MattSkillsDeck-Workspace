"""Historical circulating-share capital from eltdx's TDX corporate data.

This avoids using today's share count for a 2021-2026 backtest. The latest
capital-change record at/before the target date gives the then-current
circulating and total shares. TDX local daily volume is in shares, so:

    turnover_pct = volume_shares / circulating_shares * 100
    float_mcap_yi = circulating_shares * close / 1e8

流通市值口径（历史日期必须对应当时，不能用今天的最新值回填）：
    流通市值(亿) = 该交易日收盘价 × 该交易日**当时**的流通股本 / 1e8
流通股本取自通达信股本变迁记录（GBBQ，0x000f）：取事件日 <= 目标日 的
最近一条“股本数量类”记录，`c3_value` 即当时流通股本、`c4_value` 为总股本
（eltdx 已把万股统一换算成股，见 docs/methods/7709-股本变迁GBBQ.md）。
股本数量类标签为 2/3/5/7/8/9/10（送配股上市、非流通股上市、股本变化、
股份回购、增发新股上市、转配股上市、可转债上市）；标签 1/4/11~15 是除权
除息或比例类事件，不含可直接使用的股本数量，故不参与取数。
本地通达信 .day 只有价量额、没有股本，所以历史股本只能来自这份网络记录：
取不到记录时本模块返回 None，由调用方报错跳过该票，绝不拿最新股本折算冒充。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

try:
    from eltdx import TdxClient
except ImportError:  # pragma: no cover
    TdxClient = None  # type: ignore[assignment]

from .auction import full_code

# 股本数量类事件标签：这些记录的 c3/c4 是可直接使用的股本（单位股）。
# 2 送配股上市 / 3 非流通股上市 / 5 股本变化 / 7 股份回购 /
# 8 增发新股上市 / 9 转配股上市 / 10 可转债上市
SHARE_CATEGORIES = (2, 3, 5, 7, 8, 9, 10)


@dataclass(frozen=True)
class ShareCapital:
    date: date | None
    circulating_shares: float
    total_shares: float | None
    source: str


class EquitySource:
    def __init__(self, timeout: float = 5.0) -> None:
        if TdxClient is None:
            raise RuntimeError("eltdx is not installed")
        self._client = TdxClient(timeout=timeout)
        self._cache: dict[str, object] = {}

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def __enter__(self) -> "EquitySource":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _changes(self, code: str):
        key = full_code(code)
        if key not in self._cache:
            self._cache[key] = self._client.corporate.capital_changes(key)
        return self._cache[key]

    def circulating_shares(self, code: str, target: int) -> ShareCapital | None:
        """Return the latest known share capital on/before ``target`` (YYYYMMDD)."""
        target_date = date(target // 10000, target // 100 % 100, target % 100)
        block = self._changes(code)
        # 口径：只要事件日 <= 目标日的股本数量类记录里最近的一条，
        # 即「该交易日当时的流通股本」；不含任何按最新值折算的回退分支。
        rows = [
            record
            for record in getattr(block, "records", ())
            if getattr(record, "date", None) is not None
            and record.date <= target_date
            and getattr(record, "c3_value", None)
            and record.c3_value > 0
            and getattr(record, "category_raw", None) in SHARE_CATEGORIES
        ]
        if not rows:
            return None
        rows.sort(key=lambda record: record.date)
        latest = rows[-1]
        return ShareCapital(
            date=latest.date,
            circulating_shares=float(latest.c3_value),
            total_shares=float(latest.c4_value) if getattr(latest, "c4_value", None) else None,
            source=f"capital_change:{getattr(latest, 'category_raw', '?')}",
        )

    def turnover_pct(self, code: str, target: int, volume_shares: float) -> tuple[float, ShareCapital] | None:
        capital = self.circulating_shares(code, target)
        if capital is None or capital.circulating_shares <= 0:
            return None
        return volume_shares / capital.circulating_shares * 100.0, capital
