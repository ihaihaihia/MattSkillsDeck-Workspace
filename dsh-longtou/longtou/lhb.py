"""龙虎榜（LHB）数据层：东财公开接口 + 本地 T+1 再板率计算。

本地 TDX / 同花顺 / 东财客户端都不含龙虎榜数据，龙虎榜只能走东财 datacenter 公开接口：

    单日榜单   RPT_DAILYBILLBOARD_DETAILSNEW
    买五明细   RPT_BILLBOARD_DAILYDETAILSBUY
    卖五明细   RPT_BILLBOARD_DAILYDETAILSSELL

坑（与 :mod:`longtou.eastmoney` 一致）：本机 Python 的 OpenSSL/curl 栈有时会被东财 edge
拒绝，而 .NET 的 ``Invoke-RestMethod`` 能通。因此 :func:`_fetch_json` 先走 urllib（带浏览器 UA），
失败再落 PowerShell 兜底；两条路径都不通时抛 :class:`LhbNetworkError`，
响应不是合法 JSON 时抛 :class:`LhbParseError`，绝不静默返回空列表。

``t1_relimit_rate`` 纯本地计算，不联网，只读 TDX ``.day`` 文件。
"""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import tdx

__all__ = [
    "LhbError",
    "LhbNetworkError",
    "LhbParseError",
    "CACHE_DIR",
    "fetch_billboard",
    "fetch_billboard_detail",
    "parse_billboard_payload",
    "parse_billboard_pages",
    "t1_relimit_rate",
    "find_stock_file",
]

CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "cache"
CACHE_VERSION = 1

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_BILLBOARD = "RPT_DAILYBILLBOARD_DETAILSNEW"
REPORT_DETAIL_BUY = "RPT_BILLBOARD_DAILYDETAILSBUY"
REPORT_DETAIL_SELL = "RPT_BILLBOARD_DAILYDETAILSSELL"

PAGE_SIZE = 500  # 单日榜单每页条数；实测一天几十条，一页即可，仍按 pages 翻页
DETAIL_PAGE_SIZE = 50
MAX_PAGES = 60  # 防御性上限，避免异常 pages 值把请求打爆
TIMEOUT = 10.0  # 单次请求超时（秒）
RETRIES = 2  # 最多重试 2 次

# 与 longtou.tdx.board_events 的默认边界保持一致：20210802 之前的 .day 数据不参与统计
START_DATE = 20210802
RELIMIT_MIN_SAMPLES = 3  # 样本不足 3 次时不给再板率

# 涨停判定阈值：主板 10%，创业板/科创板 20%，取 9.8% / 19.8% 留出四舍五入余量
LIMIT_THRESHOLD_MAIN = 9.8
LIMIT_THRESHOLD_GROWTH = 19.8

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/",
}

# PowerShell 兜底脚本：url 与输出路径走环境变量，避免中文与引号转义问题
_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$r = Invoke-RestMethod -Uri $env:LHB_URL -Headers @{
  'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
  'Referer' = 'https://data.eastmoney.com/'
} -TimeoutSec ([int]$env:LHB_TIMEOUT)
$r | ConvertTo-Json -Depth 30 -Compress | Set-Content -LiteralPath $env:LHB_OUT -Encoding UTF8
"""


class LhbError(RuntimeError):
    """龙虎榜数据层异常基类。"""


class LhbNetworkError(LhbError):
    """网络层失败：urllib 与 PowerShell 兜底都没拿到响应。"""


class LhbParseError(LhbError):
    """解析层失败：拿到响应但不是合法 JSON 或结构不可用。"""


# ---------------------------------------------------------------- 网络层


def _decode_json(body: bytes) -> Any:
    """把响应字节解码成 JSON；解不开就抛解析异常（区分于网络异常）。"""
    text = body.decode("utf-8-sig", errors="replace")
    try:
        return json.loads(text)
    except Exception as exc:  # noqa: BLE001 - 任何解码失败都算解析失败
        raise LhbParseError(f"响应不是合法 JSON：{exc}；前 120 字符={text[:120]!r}") from exc


def _urllib_json(url: str, timeout: float) -> Any:
    """首选通道：urllib + 浏览器 UA。"""
    try:
        request = Request(url, headers=_HEADERS)
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
    except Exception as exc:  # noqa: BLE001 - 网络错误统一归类
        raise LhbNetworkError(f"urllib 请求失败：{exc}") from exc
    return _decode_json(body)


def _powershell_json(url: str, timeout: float) -> Any:
    """兜底通道：PowerShell Invoke-RestMethod（东财 edge 对 Python TLS 挑剔时用）。"""
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        raise LhbNetworkError("找不到 powershell.exe，无法走 Invoke-RestMethod 兜底")

    with tempfile.TemporaryDirectory(prefix="longtou-lhb-") as tmp:
        out = Path(tmp) / "payload.json"
        env = os.environ.copy()
        env["LHB_URL"] = url
        env["LHB_OUT"] = str(out)
        env["LHB_TIMEOUT"] = str(int(max(1.0, timeout)))
        try:
            completed = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", _PS_SCRIPT],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout + 15,
            )
        except subprocess.TimeoutExpired as exc:
            raise LhbNetworkError(f"PowerShell 兜底超时（>{timeout + 15:.0f}s）") from exc
        if completed.returncode != 0 or not out.exists():
            detail = (completed.stderr or completed.stdout or "无输出").strip()[:300]
            raise LhbNetworkError(f"PowerShell 兜底失败：{detail}")
        body = out.read_bytes()

    return _decode_json(body)


def _fetch_json(url: str, timeout: float = TIMEOUT, retries: int = RETRIES) -> Any:
    """带超时与重试的取数：每轮先 urllib，失败落 PowerShell，两路都失败才进下一轮。"""
    rounds = max(1, int(retries) + 1)
    errors: list[str] = []
    parse_failures = 0

    for attempt in range(1, rounds + 1):
        for label, transport in (("urllib", _urllib_json), ("powershell", _powershell_json)):
            try:
                return transport(url, timeout)
            except LhbParseError as exc:
                parse_failures += 1
                errors.append(f"{label}#{attempt} 解析失败：{exc}")
            except Exception as exc:  # noqa: BLE001 - 兜底异常也记录后继续
                errors.append(f"{label}#{attempt} 网络失败：{exc}")

    detail = " | ".join(errors[-4:])
    message = f"东财龙虎榜接口不可用（{rounds} 轮 × urllib/PowerShell）：{detail}"
    if parse_failures and parse_failures == len(errors):
        raise LhbParseError(message)
    raise LhbNetworkError(message)


# ---------------------------------------------------------------- URL 构造


def _normalize_date(value: str) -> str:
    """把 'YYYYMMDD' / 'YYYY-MM-DD' 统一成 'YYYYMMDD'。"""
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) != 8:
        raise ValueError(f"日期非法：{value!r}（需要 YYYYMMDD 或 YYYY-MM-DD）")
    return digits


def _filter_date(day: str) -> str:
    """东财 filter 里的日期格式：YYYY-MM-DD。"""
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


def _normalize_code(value: str) -> str:
    """统一成 6 位代码，允许 'sh600267' / '600267.SH' 这类写法。"""
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) != 6:
        raise ValueError(f"股票代码非法：{value!r}（需要 6 位数字）")
    return digits


def _billboard_url(day: str, page: int) -> str:
    params = {
        "reportName": REPORT_BILLBOARD,
        "columns": "ALL",
        "filter": f"(TRADE_DATE='{_filter_date(day)}')",
        "pageNumber": str(page),
        "pageSize": str(PAGE_SIZE),
        "source": "WEB",
        "client": "WEB",
    }
    # 保留 = ( ) , ' 原样，避免东财 filter 解析器报 InputMismatch
    return DATACENTER_URL + "?" + urlencode(params, safe="=(),'")


def _detail_url(report: str, code: str, day: str, page: int) -> str:
    params = {
        "reportName": report,
        "columns": "ALL",
        "filter": f"(TRADE_DATE='{_filter_date(day)}')(SECURITY_CODE=\"{code}\")",
        "pageNumber": str(page),
        "pageSize": str(DETAIL_PAGE_SIZE),
        "source": "WEB",
        "client": "WEB",
    }
    # 双引号交给 urlencode 编码成 %22（实测单引号包代码会触发 filter 解析失败）
    return DATACENTER_URL + "?" + urlencode(params, safe="=(),'")


# ---------------------------------------------------------------- 原始行 -> 业务字典


def _num(row: dict, *keys: str) -> float:
    """依次取第一个可转 float 的字段，缺失或不可解析时返回 0.0。"""
    for key in keys:
        value = row.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _text(row: dict, *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _row_date(value: Any) -> str:
    """'2026-10-20 00:00:00' / '2026-10-20' / '20261020' -> '20261020'。"""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _row_to_stock(row: dict) -> dict | None:
    """把东财一行榜单转成业务字典；代码缺失的行直接丢弃。"""
    code = _text(row, "SECURITY_CODE")
    if not code:
        return None
    name = _text(row, "SECURITY_NAME_ABBR", "SECURITY_NAME") or code
    reason = _text(row, "EXPLANATION", "EXPLAIN")
    free_cap = _num(row, "FREE_MARKET_CAP")
    return {
        "code": _normalize_code(code),
        "name": name,
        "date": _row_date(row.get("TRADE_DATE")),
        "net_amt": _num(row, "BILLBOARD_NET_AMT"),
        "buy_amt": _num(row, "BILLBOARD_BUY_AMT"),
        "sell_amt": _num(row, "BILLBOARD_SELL_AMT"),
        "deal_amt": _num(row, "BILLBOARD_DEAL_AMT"),
        "total_amt": _num(row, "ACCUM_AMOUNT"),
        "reason": reason,
        "explanations": [reason] if reason else [],
        "change_pct": _num(row, "CHANGE_RATE"),
        "close": _num(row, "CLOSE_PRICE"),
        "turnover_pct": _num(row, "TURNOVERRATE"),
        "free_mcap_yi": round(free_cap / 1e8, 4) if free_cap else 0.0,
        "rows": 1,
    }


def _merge_stock(target: dict, incoming: dict) -> None:
    """同一天同一只票可能因多个上榜原因出现多行：金额取绝对值更大的一行，原因合并。"""
    target["rows"] = int(target.get("rows", 1)) + int(incoming.get("rows", 1))
    if abs(incoming.get("net_amt", 0.0)) > abs(target.get("net_amt", 0.0)):
        for key in ("net_amt", "buy_amt", "sell_amt", "deal_amt", "total_amt", "change_pct", "close", "turnover_pct", "free_mcap_yi"):
            target[key] = incoming.get(key, target.get(key, 0.0))
    merged = list(target.get("explanations") or [])
    for reason in incoming.get("explanations") or []:
        if reason not in merged:
            merged.append(reason)
    target["explanations"] = merged
    target["reason"] = "；".join(merged)


def parse_billboard_payload(payload: Any) -> list[dict]:
    """解析单页东财榜单 payload。

    - ``result`` 为 None / 空 / 缺 ``data`` 时返回 ``[]``（非交易日、当日无上榜股）。
    - 字段缺失（净买额、名称、上榜原因等）容错为 0.0 / 代码 / 空串，不抛异常。
    - 同一代码多行时按 :func:`_merge_stock` 合并为一条。
    """
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    rows = result.get("data")
    if not isinstance(rows, list):
        return []

    merged: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        stock = _row_to_stock(row)
        if stock is None:
            continue
        existing = merged.get(stock["code"])
        if existing is None:
            merged[stock["code"]] = stock
        else:
            _merge_stock(existing, stock)
    return list(merged.values())


def parse_billboard_pages(payloads: Iterable[Any]) -> list[dict]:
    """把多页 payload 合并成一份去重后的列表（保持首次出现顺序）。"""
    merged: dict[str, dict] = {}
    for payload in payloads:
        for stock in parse_billboard_payload(payload):
            existing = merged.get(stock["code"])
            if existing is None:
                merged[stock["code"]] = stock
            else:
                _merge_stock(existing, stock)
    return list(merged.values())


def _page_count(payload: Any) -> int:
    """从 payload 里读总页数；读不到按 1 页处理，并夹到 [1, MAX_PAGES]。"""
    try:
        result = payload.get("result") if isinstance(payload, dict) else None
        pages = int((result or {}).get("pages") or 1)
    except (TypeError, ValueError, AttributeError):
        pages = 1
    return max(1, min(pages, MAX_PAGES))


# ---------------------------------------------------------------- 缓存


def _cache_path(day: str) -> Path:
    return CACHE_DIR / f"lhb_{day}.json"


def _detail_cache_path(code: str, day: str) -> Path:
    return CACHE_DIR / f"lhb_detail_{code}_{day}.json"


def _read_cache(path: Path) -> dict | None:
    """读缓存；文件损坏或版本不符时当作没有缓存。"""
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001 - 坏缓存不该阻断调用
        return None
    if not isinstance(payload, dict) or payload.get("version") != CACHE_VERSION:
        return None
    return payload


def _write_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    body.setdefault("version", CACHE_VERSION)
    body["fetched_at"] = datetime.now().isoformat(timespec="seconds")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _strip_cache_meta(payload: dict) -> dict:
    """去掉缓存元数据，保证「读缓存」与「联网取数」返回同一结构。"""
    return {key: value for key, value in payload.items() if key not in ("version", "fetched_at")}


# ---------------------------------------------------------------- 公开 API


def fetch_billboard(
    date: str,
    *,
    timeout: float = TIMEOUT,
    retries: int = RETRIES,
    use_cache: bool = True,
) -> list[dict]:
    """取某交易日全部龙虎榜上榜股（按代码去重），带本地缓存。

    参数 ``date`` 支持 ``'YYYYMMDD'`` 与 ``'YYYY-MM-DD'``。
    返回列表元素字段：
    ``code`` / ``name`` / ``date`` / ``net_amt``(净买额,元) / ``buy_amt`` / ``sell_amt`` /
    ``deal_amt``(榜单成交额,元) / ``total_amt``(当日总成交额,元) / ``reason``(上榜原因) /
    ``explanations``(原因列表) / ``change_pct`` / ``close`` / ``turnover_pct`` /
    ``free_mcap_yi``(流通市值,亿) / ``rows``(同日原始行数)。

    缓存落 ``data/cache/lhb_YYYYMMDD.json``；同一天二次调用直接读缓存，不再联网。
    """
    day = _normalize_date(date)
    cache_path = _cache_path(day)
    if use_cache:
        cached = _read_cache(cache_path)
        if cached is not None and isinstance(cached.get("rows"), list):
            return list(cached["rows"])

    first = _fetch_json(_billboard_url(day, 1), timeout=timeout, retries=retries)
    payloads: list[Any] = [first]
    for page in range(2, _page_count(first) + 1):
        payloads.append(_fetch_json(_billboard_url(day, page), timeout=timeout, retries=retries))

    stocks = parse_billboard_pages(payloads)
    if use_cache:
        _write_cache(
            cache_path,
            {
                "date": day,
                "source": DATACENTER_URL,
                "report": REPORT_BILLBOARD,
                "count": len(stocks),
                "rows": stocks,
            },
        )
    return stocks


def _parse_detail_rows(payload: Any, side: str) -> list[dict]:
    """解析买五/卖五明细一页；seat 名称缺失的行保留但标记为空串。"""
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    rows = result.get("data")
    if not isinstance(rows, list):
        return []

    seats: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        buy = _num(row, "BUY")
        sell = _num(row, "SELL")
        seats.append(
            {
                "side": side,
                "dept": _text(row, "OPERATEDEPT_NAME"),
                "rank": len(seats) + 1,
                "buy_amt": buy,
                "sell_amt": sell,
                "net_amt": _num(row, "NET", "NET_AMT") or (buy - sell),
                "reason": _text(row, "EXPLANATION", "EXPLAIN"),
            }
        )
    return seats


def fetch_billboard_detail(
    code: str,
    date: str,
    *,
    timeout: float = TIMEOUT,
    retries: int = RETRIES,
    use_cache: bool = True,
) -> dict:
    """取某股某日的买五/卖五营业部明细。

    返回 ``{'code','date','buy':[...],'sell':[...],'buy_total','sell_total','net_total','seats'}``，
    其中每席位含 ``dept``(营业部) / ``rank`` / ``buy_amt`` / ``sell_amt`` / ``net_amt`` / ``reason``。
    缓存落 ``data/cache/lhb_detail_<code>_<YYYYMMDD>.json``。
    """
    day = _normalize_date(date)
    six = _normalize_code(code)
    cache_path = _detail_cache_path(six, day)
    if use_cache:
        cached = _read_cache(cache_path)
        if cached is not None and isinstance(cached.get("buy"), list):
            return _strip_cache_meta(cached)

    buy: list[dict] = []
    sell: list[dict] = []
    for report, side, bucket in (
        (REPORT_DETAIL_BUY, "buy", buy),
        (REPORT_DETAIL_SELL, "sell", sell),
    ):
        first = _fetch_json(_detail_url(report, six, day, 1), timeout=timeout, retries=retries)
        pages = [first]
        for page in range(2, _page_count(first) + 1):
            pages.append(_fetch_json(_detail_url(report, six, day, page), timeout=timeout, retries=retries))
        for page_payload in pages:
            bucket.extend(_parse_detail_rows(page_payload, side))

    # 同一席位在多个上榜原因下会重复出现，按 (side, dept) 合并金额
    deduped: dict[tuple[str, str], dict] = {}
    for seat in buy + sell:
        key = (seat["side"], seat["dept"])
        current = deduped.get(key)
        if current is None:
            deduped[key] = seat
        else:
            current["buy_amt"] += seat["buy_amt"]
            current["sell_amt"] += seat["sell_amt"]
            current["net_amt"] += seat["net_amt"]

    final_buy = [seat for seat in deduped.values() if seat["side"] == "buy"]
    final_sell = [seat for seat in deduped.values() if seat["side"] == "sell"]
    for bucket in (final_buy, final_sell):
        bucket.sort(key=lambda item: (item["buy_amt"] if item["side"] == "buy" else item["sell_amt"]), reverse=True)
        for index, item in enumerate(bucket, start=1):
            item["rank"] = index

    buy_total = sum(item["buy_amt"] for item in final_buy)
    sell_total = sum(item["sell_amt"] for item in final_sell)
    payload = {
        "code": six,
        "date": day,
        "buy": final_buy,
        "sell": final_sell,
        "buy_total": buy_total,
        "sell_total": sell_total,
        "net_total": buy_total - sell_total,
        "seats": len(final_buy) + len(final_sell),
    }
    if use_cache:
        _write_cache(cache_path, payload)
    return payload


# ---------------------------------------------------------------- 本地 T+1 再板率


def find_stock_file(code: str) -> tdx.StockFile:
    """按 6 位代码定位本地 ``.day`` 文件；找不到抛 FileNotFoundError。"""
    raw = str(code).strip().lower()
    market_hint = raw[:2] if raw[:2] in ("sh", "sz") else None
    body = raw[2:] if market_hint else raw
    six = "".join(ch for ch in body if ch.isdigit())
    if len(six) != 6:
        raise ValueError(f"股票代码非法：{code!r}（需要 6 位数字，可带 sh/sz 前缀）")

    order = [market_hint] if market_hint else (["sh", "sz"] if six[0] == "6" else ["sz", "sh"])
    for market in order:
        path = tdx.TDX_VIPDOC / market / "lday" / f"{market}{six}.day"
        if path.is_file():
            return tdx.StockFile(market=market, code=six, path=path)

    # 目录结构异常时回落到全量扫描（慢，但只在兜底路径上发生）
    for stock in tdx.list_stocks():
        if stock.code == six:
            return stock
    raise FileNotFoundError(f"本地 TDX 没有 {six} 的 .day 文件（检查 {tdx.TDX_VIPDOC}）")


def _limit_threshold(code: str) -> float:
    """按板块返回涨停判定阈值：主板 9.8%，创业板/科创板 19.8%。"""
    return LIMIT_THRESHOLD_GROWTH if tdx.limit_pct(code) >= 0.20 else LIMIT_THRESHOLD_MAIN


def t1_relimit_rate(code: str, lookback: int = 250) -> dict:
    """计算该股「涨停日 → 次日继续涨停」的比例（纯本地 .day，不联网）。

    口径：
    - 只统计 ``20210802`` 及之后的交易日（与 :func:`longtou.tdx.board_events` 边界一致）；
    - 涨停阈值按板块取 9.8% / 19.8%（收盘价相对前收涨幅，ST 的 5% 不计入）；
    - ``lookback`` 表示只回看最近多少个交易日（<=0 表示全部历史）；
    - 次日必须仍在样本数据内，否则该涨停日不计入分母。

    返回 ``{'code','samples','relimit','rate','threshold_pct','window','start_date',
    'end_date','samples_insufficient','note'}``；样本少于 3 次时 ``rate`` 为 ``None``。
    """
    stock = find_stock_file(code)
    bars = [bar for bar in tdx.read_day_file(stock.path) if bar.date >= START_DATE]
    if not bars:
        return {
            "code": stock.code,
            "samples": 0,
            "relimit": 0,
            "rate": None,
            "threshold_pct": _limit_threshold(stock.code),
            "window": 0,
            "start_date": "",
            "end_date": "",
            "samples_insufficient": True,
            "note": "本地 .day 在 20210802 之后没有数据",
        }

    window = int(lookback) if int(lookback) > 0 else len(bars)
    window = min(window, len(bars))
    start_index = len(bars) - window
    start_index = max(start_index, 1)  # 首根 K 线没有前收，无法判涨幅
    threshold = _limit_threshold(stock.code)

    samples = 0
    relimit = 0
    last_up_date = 0
    for index in range(start_index, len(bars) - 1):
        prev_close = bars[index - 1].close
        close = bars[index].close
        if prev_close <= 0:
            continue
        change_pct = (close / prev_close - 1.0) * 100.0
        if change_pct < threshold:
            continue
        samples += 1
        last_up_date = bars[index].date
        next_close = bars[index + 1].close
        next_change = (next_close / close - 1.0) * 100.0 if close > 0 else 0.0
        if next_change >= threshold:
            relimit += 1

    rate = round(relimit / samples, 4) if samples else None
    insufficient = samples < RELIMIT_MIN_SAMPLES
    if insufficient and samples:
        rate = None
        note = f"样本太少：{lookback} 日窗口内仅 {samples} 次涨停，需要 ≥{RELIMIT_MIN_SAMPLES} 次"
    elif not samples:
        note = "样本太少：窗口内没有涨停日"
    else:
        note = ""

    return {
        "code": stock.code,
        "samples": samples,
        "relimit": relimit,
        "rate": rate,
        "threshold_pct": threshold,
        "window": len(bars) - start_index,
        "start_date": str(bars[start_index].date),
        "end_date": str(bars[-1].date),
        "last_limit_up": str(last_up_date) if last_up_date else "",
        "samples_insufficient": insufficient,
        "note": note,
    }
