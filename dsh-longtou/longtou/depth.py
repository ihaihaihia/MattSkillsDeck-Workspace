"""封单额 / 封流比取数（双口径），独立于 engine，避免 engine 继续膨胀。

## 口径① 竞价封单（列名「竞价封单(万)」）—— 历史可复现
    09:25 开盘集合竞价时点的**买盘未匹配量 × 竞价价 × 100**（手→股）。
    数据源：eltdx 0x056a 竞价序列 ``client.auctions.series(code, trading_date)``，
            包内 ``models/auction.py`` 的 ``AuctionPoint.unmatched_volume``（竞价未匹配量，手）。
    实测限制（2026-10-21 验证）：0x056a 对**当日**返回 09:15–09:25 开盘竞价序列，
            对**历史日期**返回的是 14:57–15:00 的收盘集合竞价序列。
            因此历史日期取不到 09:25 未匹配量 → 一律填「—」，**不编造**。
            另：``TradeTick.auction_unmatched_signed_volume``（models/trade.py:31）在
            ``trades.opening_match_history`` 返回的 opening_match 记录上实测为 None，不可用。

## 口径② 实时封单（列名「实时封单(万)」）—— 只在盘中可用
    五档买一价 × 买一量 × 100（与包内 ``models/quote.py:178`` 的
    ``CategoryQuoteRecord.locked_amount`` 同公式）。
    数据源：eltdx 0x0547 五档刷新 ``client.quotes.get_depth(codes)`` →
            ``QuoteRefreshPage.records[].buy_levels[0]``（价, 量手）。
    非交易时段不发请求，直接「—」。

## 封流比
    封单额（元）÷ 流通市值（元）× 100%。流通市值 = ``float_mcap_yi * 1e8``（引擎已有）。
    封单额缺（或竞价未匹配量在**卖盘**一侧）时退实时封单；两者都缺记「—」。

## 明确缺口
    历史「收盘封单额」本地无数据源：TDX 日线不含分笔/委托队列，本地 overlay 也无。
    本模块不提供、报告也不得声称有该项。

所有路径都做异常降级：网络/接口/解析失败只会返回 (None, 原因)，不抛异常打断报告。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

try:  # 与 auction.py 一致的导入策略：包缺失时降级而非崩溃
    from eltdx import TdxClient
except ImportError:  # pragma: no cover
    TdxClient = None  # type: ignore[assignment]

_CN = timezone(timedelta(hours=8), "Asia/Shanghai")

# 单份报告最多取数的行数上限：候选池一般 <15 只，这里只做防御，避免异常数据把请求打爆。
MAX_SEAL_ROWS = 24

# 开盘集合竞价时点窗口：09:15:00 – 09:25:59（09:25 撮合，最后一条快照通常 09:24:5x）。
AUCTION_START_SEC = 9 * 3600 + 15 * 60
AUCTION_END_SEC = 9 * 3600 + 25 * 60 + 59


@dataclass(frozen=True)
class SealQuote:
    """一只票的双口径封单额（单位：元）与取数说明。"""

    auction_yuan: float | None = None
    auction_note: str = ""
    realtime_yuan: float | None = None
    realtime_note: str = ""


def is_trading_time(now: datetime | None = None) -> bool:
    """是否 A 股盘中（含集合竞价）：周一~周五 09:15–11:30 / 13:00–15:00，北京时间。"""
    current = (now or datetime.now(_CN)).astimezone(_CN)
    if current.weekday() >= 5:
        return False
    seconds = current.hour * 3600 + current.minute * 60 + current.second
    morning = 9 * 3600 + 15 * 60 <= seconds <= 11 * 3600 + 30 * 60
    afternoon = 13 * 3600 <= seconds <= 15 * 3600
    return morning or afternoon


def _time_seconds(point: object) -> int | None:
    """竞价快照的当日秒数：优先 time_seconds，其次解析 time_label。"""
    value = getattr(point, "time_seconds", None)
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    label = str(getattr(point, "time_label", "") or "")
    parts = label.split(":") if label.count(":") == 2 else []
    if len(parts) == 3:
        try:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except ValueError:
            return None
    return None


def auction_seal_from_series(series: object) -> tuple[float | None, str]:
    """从 AuctionSeries 里取 09:25 时点未匹配量算竞价封单额；取不到返回 (None, 原因)。

    只认落在 09:15–09:25:59 的快照；服务端对历史日期返回收盘集合竞价序列，
    此时判定为「无 09:25 序列」，不拿 14:5x 的数据冒充 9:25 封单。
    """
    points = list(getattr(series, "points", ()) or ())
    if not points:
        return None, "无竞价快照"
    in_window: list[tuple[int, object]] = []
    for point in points:
        seconds = _time_seconds(point)
        if seconds is not None and AUCTION_START_SEC <= seconds <= AUCTION_END_SEC:
            in_window.append((seconds, point))
    if not in_window:
        labels = [str(getattr(p, "time_label", "") or "") for p in points]
        span = f"{labels[0]}–{labels[-1]}" if labels else "未知"
        return None, f"该日无 09:25 竞价序列（服务端返回 {span}）"
    in_window.sort(key=lambda item: item[0])
    seconds, point = in_window[-1]
    price = float(getattr(point, "price", 0.0) or 0.0)
    lots = getattr(point, "unmatched_volume", None)
    if lots is None:
        return None, "快照无未匹配量字段"
    # 未匹配量是绝对值，方向在 unmatched_signed_raw / unmatched_direction_raw（负＝卖盘）。
    # 卖盘残余不是封单：当成封单会凭空造出极小的「封流比」（20261021 002929 实例）。
    direction = getattr(point, "unmatched_signed_raw", None)
    if direction is None:
        direction = getattr(point, "unmatched_direction_raw", None)
    if direction is not None and direction < 0:
        return None, f"{getattr(point, 'time_label', '')} 未匹配量为卖盘 {lots} 手，非封单"
    if price <= 0 or lots <= 0:
        return None, f"{getattr(point, 'time_label', '')} 未匹配量 {lots} 手 × 价 {price}"
    return lots * 100.0 * price, f"{getattr(point, 'time_label', '')} 未匹配 {lots} 手 × {price:.2f}"


def realtime_seal_from_depth(page: object, code: str) -> tuple[float | None, str]:
    """从五档刷新页取买一价 × 买一量 × 100 得实时封单；取不到返回 (None, 原因)。"""
    records = list(getattr(page, "records", ()) or ())
    if not records:
        return None, "五档无返回记录"
    record = next((item for item in records if str(getattr(item, "code", "")) == code), None)
    if record is None:
        # 宁可判「无值」，也不能把别的票的五档当成本票封单（静默错数据更致命）
        return None, "返回记录不含该代码"
    levels = list(getattr(record, "buy_levels", ()) or ())
    if not levels:
        return None, "五档无买盘"
    bid = levels[0]
    price = float(getattr(bid, "price", 0.0) or 0.0)
    lots = int(getattr(bid, "volume", 0) or 0)
    if price <= 0 or lots <= 0:
        return None, "买一档无量"
    return price * lots * 100.0, f"买一 {price:.2f} × {lots} 手"


class SealSource:
    """eltdx 封单取数封装：一只票一个 client，逐只顺序取，全部异常降级。"""

    def __init__(self, timeout: float = 5.0, *, realtime: bool | None = None) -> None:
        self._timeout = timeout
        self._client = None
        self._error = ""
        # realtime=None 表示按当前时间自动判断；显式 True/False 便于测试与离线渲染。
        self._realtime_allowed = is_trading_time() if realtime is None else bool(realtime)
        self._auction_cache: dict[tuple[str, str], tuple[float | None, str]] = {}
        if TdxClient is None:
            self._error = "eltdx 未安装"

    def _ensure_client(self):
        if self._client is not None or self._error:
            return self._client
        try:
            self._client = TdxClient(timeout=self._timeout)
        except Exception as exc:  # 连接失败不阻断整份报告
            self._error = f"{type(exc).__name__}: {exc}"
        return self._client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def __enter__(self) -> "SealSource":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def auction_locked(self, code: str, trading_date: str) -> tuple[float | None, str]:
        """口径①：竞价封单额（元）与说明。历史日期无 09:25 序列时返回 (None, 原因)。"""
        key = (code, trading_date)
        if key in self._auction_cache:
            return self._auction_cache[key]
        client = self._ensure_client()
        if client is None:
            result = (None, self._error or "无 client")
            self._auction_cache[key] = result
            return result
        try:
            series = client.auctions.series(_full_code(code), trading_date)
            result = auction_seal_from_series(series)
        except Exception as exc:  # 网络/协议失败降级
            result = (None, f"{type(exc).__name__}: {exc}")
        self._auction_cache[key] = result
        return result

    def realtime_locked(self, code: str) -> tuple[float | None, str]:
        """口径②：实时封单额（元）与说明。非交易时段直接返回 (None, 非盘中)。"""
        if not self._realtime_allowed:
            return None, "非交易时段，实时封单不可得"
        client = self._ensure_client()
        if client is None:
            return None, self._error or "无 client"
        try:
            page = client.quotes.get_depth([_full_code(code)])
            return realtime_seal_from_depth(page, code)
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"


def _full_code(code: str) -> str:
    """补交易所前缀（复用 auction.full_code 的口径，避免循环导入就内联一份）。"""
    text = str(code or "").strip()
    if text.startswith(("sh", "sz")):
        return text
    if len(text) != 6 or not text.isdigit():
        raise ValueError(f"not a 6-digit A-share code: {code!r}")
    return "sh" + text if text.startswith("6") else "sz" + text


def seal_flow_ratio(seal_yuan: float | None, float_mcap_yi: object) -> float | None:
    """封流比（%）= 封单额 ÷ 流通市值；单位统一为元，缺任一则 None。"""
    if seal_yuan is None:
        return None
    try:
        mcap_yuan = float(float_mcap_yi) * 1e8  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if mcap_yuan <= 0:
        return None
    return seal_yuan / mcap_yuan * 100.0
