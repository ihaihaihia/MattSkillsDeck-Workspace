"""Minimal TDX local daily-bar reader and limit-up/board-event helpers.

The local TDX ``.day`` files are only used for daily history and backtests.
They do not contain 09:25 auction data; see :mod:`longtou.auction`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
from pathlib import Path
import struct
from typing import Iterable, Iterator

TDX_VIPDOC = Path(r"C:\new_tdx\vipdoc")
_OVERLAY_PATH = Path(__file__).resolve().parents[1] / "data" / "daily_overlay.json"
_overlay_cache: dict[str, list[dict]] | None = None

# TDX .day record: date, open, high, low, close, amount(float), volume, reserved.
_DAY_STRUCT = struct.Struct("<IiiiifIi")


@dataclass(frozen=True)
class Bar:
    date: int  # YYYYMMDD
    open: float
    high: float
    low: float
    close: float
    amount: float
    volume: float


@dataclass(frozen=True)
class StockFile:
    market: str  # "sh" or "sz"
    code: str  # 6 digits
    path: Path

    @property
    def full_code(self) -> str:
        return f"{self.market}{self.code}"


@dataclass(frozen=True)
class BoardEvent:
    stock: StockFile
    index: int
    height: int
    bar: Bar
    prev_close: float

    @property
    def limit_pct(self) -> float:
        return limit_pct(self.stock.code)

    @property
    def limit_price(self) -> float:
        return limit_price(self.prev_close, self.limit_pct)

    @property
    def is_one_word(self) -> bool:
        # Never opened below the limit-up price = one-word board.
        return self.bar.low >= self.limit_price - 0.005


_NAMES_PATH = Path(__file__).resolve().parents[1] / "data" / "stock_names.json"
_names_cache: dict[str, str] | None = None


def _stock_names() -> dict[str, str]:
    """代码 -> 名称（``data/stock_names.json``），失败返回 {}；模块级缓存一次。"""
    global _names_cache
    if _names_cache is None:
        try:
            payload = json.loads(_NAMES_PATH.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        _names_cache = payload if isinstance(payload, dict) else {}
    return _names_cache


def is_st_code(code: str, name: str | None = None) -> bool:
    """该股是否按 ST 5% 幅度：仅主板，且名称含 "ST"（含 ``*ST``/``S*ST``，大小写不敏感）。

    科创/创业 ST 仍是 20%，所以 300/301/302/688/689 先排除。名称来源优先用显式传入的
    ``name``（历史口径可传当日名），否则回退 ``data/stock_names.json``（当前快照名）。
    """
    if code.startswith(("300", "301", "302", "688", "689")):
        return False
    if name is None:
        name = _stock_names().get(code, "")
    return "ST" in str(name or "").upper()


def limit_pct(code: str, name: str | None = None) -> float:
    """Return the daily limit ratio for an A-share.

    科创/创业（688/689/300/301/302）20%；主板 ST（名称含 "ST"，含 ``*ST``）5%；其余 10%。

    TODO(北交所): 920/43/83/87/88 幅度为 30%，但 ``list_stocks`` 目前只扫 sh/sz，
    北交所 .day 未纳入样本，本函数暂不返回 30%（需先补 ``list_stocks`` 采样再启用）。
    """
    if code.startswith(("300", "301", "302", "688", "689")):
        return 0.20
    if is_st_code(code, name):
        return 0.05
    return 0.10


def limit_price(prev_close: float, pct: float) -> float:
    return round(prev_close * (1.0 + pct) + 1e-9, 2)


def is_a_share(market: str, code: str) -> bool:
    if len(code) != 6 or not code.isdigit():
        return False
    if market == "sh":
        return code.startswith(("600", "601", "603", "605", "688", "689"))
    if market == "sz":
        return code.startswith(("000", "001", "002", "003", "300", "301", "302"))
    # TODO(北交所): 920/43/83/87/88（bj 市场）暂不采样，留待后续补充。
    return False


def _daily_overlay() -> dict[str, list[dict]]:
    global _overlay_cache
    if _overlay_cache is None:
        try:
            payload = json.loads(_OVERLAY_PATH.read_text(encoding="utf-8"))
            _overlay_cache = payload.get("bars", {}) if isinstance(payload, dict) else {}
        except Exception:
            _overlay_cache = {}
    return _overlay_cache


def read_day_file(path: Path | str) -> list[Bar]:
    path = Path(path)
    data = path.read_bytes()
    count = len(data) // _DAY_STRUCT.size
    bars: list[Bar] = []
    for i in range(count):
        d, o, h, l, c, amount, volume, _reserved = _DAY_STRUCT.unpack_from(data, i * _DAY_STRUCT.size)
        if d <= 0:
            continue
        bars.append(
            Bar(
                date=int(d),
                open=o / 100.0,
                high=h / 100.0,
                low=l / 100.0,
                close=c / 100.0,
                amount=float(amount),
                volume=float(volume),
            )
        )

    overlay_rows = _daily_overlay().get(path.stem)
    if overlay_rows:
        existing = {bar.date for bar in bars}
        for row in overlay_rows:
            day = int(row.get("date", 0))
            if day <= 0 or day in existing:
                continue
            bars.append(
                Bar(
                    date=day,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    amount=float(row.get("amount", 0.0)),
                    volume=float(row.get("volume", 0.0)),
                )
            )
        bars.sort(key=lambda bar: bar.date)
    return bars


def list_stocks(root: Path = TDX_VIPDOC) -> list[StockFile]:
    stocks: list[StockFile] = []
    for market in ("sh", "sz"):
        directory = root / market / "lday"
        if not directory.is_dir():
            continue
        for path in directory.glob("*.day"):
            stem = path.stem
            if len(stem) < 3:
                continue
            code = stem[2:]
            if is_a_share(stem[:2], code):
                stocks.append(StockFile(market=stem[:2], code=code, path=path))
    stocks.sort(key=lambda s: (s.market, s.code))
    return stocks


def board_events(
    stock: StockFile,
    bars: Iterable[Bar],
    start_date: int = 20210802,
) -> Iterator[BoardEvent]:
    """Yield every limit-up bar with its consecutive-board count.

    Bars before ``start_date`` are still used to establish the previous close and
    the running board height; only events on/after ``start_date`` are yielded.
    """
    prev_close: float | None = None
    height = 0
    pct = limit_pct(stock.code)

    for index, bar in enumerate(bars):
        if prev_close is None:
            prev_close = bar.close
            continue

        target = limit_price(prev_close, pct)
        is_up = bar.close >= target - 0.005
        if is_up:
            height += 1
        else:
            height = 0

        if is_up and bar.date >= start_date:
            yield BoardEvent(
                stock=stock,
                index=index,
                height=height,
                bar=bar,
                prev_close=prev_close,
            )

        prev_close = bar.close


def next_bar(bars: list[Bar], index: int) -> Bar | None:
    if 0 <= index + 1 < len(bars):
        return bars[index + 1]
    return None


def date_int_to_str(value: int) -> str:
    return f"{value // 10000:04d}-{value // 100 % 100:02d}-{value % 100:02d}"


def date_int_to_date(value: int) -> date:
    return date(value // 10000, value // 100 % 100, value % 100)


def date_to_int(value: date | datetime) -> int:
    return value.year * 10000 + value.month * 100 + value.day
