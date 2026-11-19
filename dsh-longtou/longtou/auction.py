"""09:25 official opening-match data from TDX via the open-source ``eltdx`` client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import time
from typing import Iterable

_CN = timezone(timedelta(hours=8), "Asia/Shanghai")


def _normalize_day(value: str) -> str:
    return str(value or "").replace("-", "")[:8]

try:
    from eltdx import TdxClient
except ImportError:  # pragma: no cover - gives a clear runtime message
    TdxClient = None  # type: ignore[assignment]


@dataclass(frozen=True)
class Auction:
    code: str
    trading_date: str
    price: float
    volume_lots: int
    amount_yuan: float

    @property
    def volume_shares(self) -> int:
        return int(self.volume_lots) * 100


def full_code(code: str) -> str:
    if code.startswith(("sh", "sz")):
        return code
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"not a 6-digit A-share code: {code!r}")
    if code.startswith("6"):
        return "sh" + code
    return "sz" + code


class AuctionSource:
    """Sequential eltdx wrapper with one reusable client.

    ``opening_match_history`` is the official 09:25 match. It is not the
    virtual auction snapshot and it is not the 09:31 first minute.
    """

    def __init__(self, timeout: float = 5.0) -> None:
        if TdxClient is None:
            raise RuntimeError(
                "eltdx is not installed. Run: python -m pip install -r requirements.txt"
            )
        self._client = TdxClient(timeout=timeout)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def __enter__(self) -> "AuctionSource":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def fetch(self, code: str, trading_date: str) -> Auction | None:
        last_error: Exception | None = None
        tick = None
        # At 09:25 the current-day historical page is not ready yet, but the
        # live opening match is.  Try the live path first when the date is today.
        is_today = _normalize_day(trading_date) == datetime.now(_CN).strftime("%Y%m%d")
        candidates = [None, trading_date] if is_today else [trading_date]
        for attempt in range(2):
            for candidate in candidates:
                try:
                    tick = self._client.trades.opening_match_history(full_code(code), candidate)
                    last_error = None
                except Exception as exc:  # transient TDX protocol/paging failure
                    last_error = exc
                    tick = None
                if tick is not None:
                    break
            if tick is not None:
                break
            if attempt == 0:
                time.sleep(0.4)
        if last_error is not None and tick is None:
            raise last_error
        if tick is None:
            return None
        amount = getattr(tick, "trade_amount_yuan", None)
        if amount is None:
            amount = float(tick.price) * int(tick.volume) * 100.0
        tick_date = getattr(tick, "trade_datetime", None) or trading_date
        return Auction(
            code=code,
            trading_date=str(tick_date),
            price=float(tick.price),
            volume_lots=int(tick.volume),
            amount_yuan=float(amount),
        )

    def fetch_many(self, codes: Iterable[str], trading_date: str) -> dict[str, Auction | None]:
        """Fetch a small set sequentially; callers should cache and throttle."""
        result: dict[str, Auction | None] = {}
        for code in codes:
            try:
                result[code] = self.fetch(code, trading_date)
            except Exception:
                result[code] = None
        return result
