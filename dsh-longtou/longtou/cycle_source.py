"""情绪周期数据采集层：纯标准库取数 + 历史序列聚合。

设计约束（面板依赖它，不能因为外网挂掉而崩）：

* 每个函数单次请求超时 8 秒，任何失败返回 ``None`` 或空列表，**绝不抛异常**。
* 只用标准库 ``urllib`` + ``json``；不引入 requests / akshare / pandas。
* 逐日历史序列（涨停家数、最高板、连板梯队）一律读 ``data/market_stats.json``，
  不重新抓历史——该文件已有 1263 个交易日。

实测过的坑（2026-10-22，本机 Windows）：

* ``push2.eastmoney.com`` / ``push2his.eastmoney.com`` 会以
  ``RemoteDisconnected: Remote end closed connection without response`` 的方式
  拒绝 Python 的 OpenSSL 栈（同一个坑 ``longtou/eastmoney.py`` 里也记录过），
  因此每次请求先走 urllib，失败再走 PowerShell ``Invoke-RestMethod``（.NET 栈），
  两条都失败就返回 None。
* ``push2ex.eastmoney.com`` 目前 urllib 直连正常。
* ``fs=m:90+t:2`` 实测返回 496 个板块（含「电机Ⅱ/电机Ⅲ」这类二三级细分行业），
  本模块只按涨跌幅排序取前 5，不额外过滤。

接口字段对照（实测，非文档推测）：

============= ==========================================================
指数快照        ``ulist.np/get``: f2 现价 / f3 涨跌幅(%) / f6 成交额(元) / f14 名称
指数K线        ``kline/get``: f51 日期 / f53 收盘 / f57 成交额(元) / f59 涨跌幅(%)
涨停池          ``getTopicZTPool``: c 代码 / n 名称 / lbc 连板数 / fbt 首封 /
                lbt 最后封板 / zbc 炸板次数 / fund 封单资金(元) / hybk 行业板块
                （该接口**没有**「涨停原因」字段，business 侧只能留 None）
涨跌分布        ``getTopicZDFenBu``: fenbu = [{bucket: count}]，bucket 为涨跌幅档位
板块榜          ``clist/get``: f3 涨跌幅 / f14 板块名 / f128 领涨股
============= ==========================================================

金额单位：对外一律「亿元」，与前端契约一致；原始接口是元。
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

# 本地通达信日线兜底用；_DAY_STRUCT 直接复用 tdx 的结构常量，避免记录格式漂移
from .tdx import TDX_VIPDOC, Bar, _DAY_STRUCT, list_stocks, read_day_file

ROOT = Path(__file__).resolve().parents[1]
MARKET_STATS = ROOT / "data" / "market_stats.json"
STOCK_NAMES = ROOT / "data" / "stock_names.json"
# F10 连板天梯：`{YYYYMMDD: [{code, name, level, ...}]}`，连板高度的权威源（build_ladder 兜底用）
LIMIT_LADDER = ROOT / "data" / "limit_ladder.json"
_LIMIT_LADDER_CACHE: dict[str, Any] | None = None
# 逐日 ST 档位历史：`{YYYYMMDD: {代码: 5 | 10}}`。band 是该股**当日**的涨跌停档位，
# 10 = 10% 档（当日未 ST），5 = 5% 档（当日是 ST）。由 ``tools/build_st_hist.py`` 离线生成，
# ``build_ladder`` 用它决定历史日要不要剥掉「当前名称」里后来才贴上的 ST 前缀。
ST_HIST = ROOT / "data" / "st_hist.json"
_ST_HIST_CACHE: dict[str, Any] | None = None
# 每日停牌的空间板缓存：`{YYYYMMDD: [{code, height, last_date, one_word_ratio}]}`，
# 键是**停牌当日**（build_suspended_stats.py 对每个目标日检查该股当日有无 bar，
# 无 bar 且停牌前连板数 ≥2 才记进这一天），不是停牌前最后交易日
SUSPENDED_SPACE = ROOT / "data" / "suspended_space.json"
# 历史 09:25 集合竞价成交额缓存：`{YYYYMMDD: {代码: 亿元 float}}`，
# 由 ``tools/auction_backfill.py`` 离线生成，``build_ladder`` 用它给每只股票补 ``auction_yi``
AUCTION_HISTORY = ROOT / "data" / "auction_history.json"
# 模块级缓存：120 天梯队每只股票都要查，每次读盘会白读 120 次；None = 还没读过盘
_AUCTION_HISTORY_CACHE: dict[str, Any] | None = None
# 历史 09:25 集合竞价成交量缓存：`{YYYYMMDD: {代码: 手 float}}`，与金额文件同形。
# 由 ``tools/auction_backfill.py`` **并行**生成，回填未完成时文件可能不存在 / 只覆盖
# 部分日期 → 一律优雅降级成 None（不抛异常、不打日志）。
AUCTION_VOLUME_HISTORY = ROOT / "data" / "auction_volume_history.json"
_AUCTION_VOLUME_CACHE: dict[str, Any] | None = None
# 本地 .day 只解析末尾这么几根 bar：120 个交易日的梯队最多回溯约 170 个自然日，
# 300 根足够；整份文件逐根解析（约 1150 根）× 8820 个 (date, code) 对会把一次
# build_ladder 拖到 20 秒以上（实测 24.8s → 见下缓存后的口径）。
_TAIL_BARS = 300
# 代码 -> .day 路径（没有该文件的也缓存 None，避免反复全量扫描）
_DAY_PATH_CACHE: dict[str, Path | None] = {}
# 代码 -> .day 末尾原始字节（None = 无文件/读盘失败）
_DAY_TAIL_CACHE: dict[str, bytes | None] = {}

TIMEOUT = 8.0  # 单次请求超时（秒），契约要求
RETRY_GAP = 0.4  # 东财对连发敏感，请求之间留一点间隔
MAX_POOL_PAGES = 3  # 涨停池翻页上限，防止异常 tc 导致死循环

# 当日实时涨停/跌停家数落盘件：由 ``tools/build_live_counts.py`` 生成，结构见该脚本。
# ``collect()`` 只读不算（读盘失败 / 日期不匹配 → 静默跳过，series[-1] 保持 null），
# 这样面板采集不会因为多一条网络腿而变慢或变脆。
LIVE_COUNTS = ROOT / "data" / "cycle" / "live_counts.json"
# 全市场快照：沪深主板 + 创业板 + 科创板 + 北交所（与原型的 fs 完全一致）
SNAPSHOT_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
SNAPSHOT_FIELDS = "f2,f3,f12,f14,f18"
SNAPSHOT_PAGE_SIZE = 200  # clist 每页条数；A 股约 5900 只 → 约 30 页
SNAPSHOT_MAX_PAGES = 60  # 翻页上限，防止异常 total 导致死循环
# 涨停价容差：最小变动价位是 0.01，判定用「差不到半分钱」即视为触及。
# 反算一侧用 Decimal ROUND_HALF_UP（交易所也是四舍五入到分），Python 内建 round()
# 是银行家舍入（round(11.055, 2) → 11.05），会把涨停价算低一分 → 这类股会漏判。
LIMIT_TOL = 0.005

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
_UT = "7eea3edcaed734bea9cbfc24409ed989"

# 指数：契约里的五个，secid 前缀 1=沪 0=深
INDICES: tuple[tuple[str, str], ...] = (
    ("上证指数", "1.000001"),
    ("深证成指", "0.399001"),
    ("创业板指", "0.399006"),
    ("沪深300", "1.000300"),
    ("上证50", "1.000016"),
)

_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$r = Invoke-RestMethod -Uri $env:CYC_URL -Headers @{
  'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
  'Referer' = 'https://quote.eastmoney.com/'
} -TimeoutSec 8
$r | ConvertTo-Json -Depth 20 -Compress | Set-Content -LiteralPath $env:CYC_OUT -Encoding UTF8
"""


# --------------------------------------------------------------------------
# 底层：双通路取 JSON，永不抛异常
# --------------------------------------------------------------------------
def _fetch_json_powershell(url: str, timeout: float) -> dict | None:
    """PowerShell/.NET 栈的兜底通路；失败返回 None。"""
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="longtou-cycle-") as tmp:
            out = Path(tmp) / "payload.json"
            env = os.environ.copy()
            env["CYC_URL"] = url
            env["CYC_OUT"] = str(out)
            completed = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", _PS_SCRIPT],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout + 6,
            )
            if completed.returncode != 0 or not out.exists():
                return None
            payload = json.loads(out.read_text(encoding="utf-8-sig"))
            return payload if isinstance(payload, dict) else None
    except Exception:  # noqa: BLE001 - 兜底通路本身也不许抛
        return None


def _fetch_json(url: str, timeout: float = TIMEOUT) -> dict | None:
    """取一个 JSON 对象；urllib 失败退 PowerShell，仍失败返回 None。"""
    try:
        with urlopen(Request(url, headers=_HEADERS), timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
        if isinstance(payload, dict):
            return payload
    except Exception:  # noqa: BLE001 - 对外契约要求不抛
        pass
    return _fetch_json_powershell(url, timeout)


def _sleep() -> None:
    time.sleep(RETRY_GAP)


# --------------------------------------------------------------------------
# 指数快照 / 全市场成交额
# --------------------------------------------------------------------------
def fetch_index_quotes() -> list[dict]:
    """五个指数的实时点位与涨跌幅；单个指数取不到就跳过，全取不到返回 []（不编数字）。

    主通路是一次 ``ulist.np`` 请求拿全部 5 个；若该接口只回了部分指数（实测限流时
    会半量返回），缺的那几个再逐个用 ``kline`` 的 f53 收盘 + f59 涨跌幅补。
    """
    secids = ",".join(secid for _, secid in INDICES)
    url = (
        "https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2"
        f"&secids={secids}&fields=f2,f3,f12,f14"
    )
    payload = _fetch_json(url)
    rows = ((payload or {}).get("data") or {}).get("diff") or []
    if isinstance(rows, dict):  # 有些返回把 diff 写成 {"0": {...}}
        rows = list(rows.values())
    got: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("f12") or row.get("f14") or "")
        point, pct = _num(row.get("f2")), _num(row.get("f3"))
        if not code or (point is None and pct is None):
            continue
        got[code] = {"point": point, "pct": pct}

    quotes: list[dict] = []
    for name, secid in INDICES:
        code = secid.split(".", 1)[-1]
        item = got.get(code)
        if item is None:
            # 退化通路：该指数的 K 线最后两根（f53 收盘 / f59 涨跌幅）
            bars = _kline_bars(secid, lmt=1)
            _sleep()
            if not bars:
                continue
            item = {"point": bars[-1].get("close"), "pct": bars[-1].get("pct")}
            if item["point"] is None:
                continue
        quotes.append({"name": name, "point": item.get("point"), "pct": item.get("pct")})
    return quotes


def _kline_bars(secid: str, lmt: int = 2) -> list[dict]:
    """K 线：返回 [{date, close, amount, pct}]，失败 []。amount 单位元。"""
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&fields1=f1,f2,f3&fields2=f51,f53,f57,f59"
        f"&klt=101&fqt=1&end=20500101&lmt={int(lmt)}"
    )
    payload = _fetch_json(url)
    lines = ((payload or {}).get("data") or {}).get("klines") or []
    bars: list[dict] = []
    for line in lines:
        parts = str(line).split(",")
        if len(parts) < 4:
            continue
        bars.append(
            {
                "date": parts[0].replace("-", ""),
                "close": _num(parts[1]),
                "amount": _num(parts[2]),
                "pct": _num(parts[3]),
            }
        )
    return bars


def fetch_market_amount() -> dict:
    """两市成交额（亿元）：沪市用上证指数 K 线 f57，深市用深证综指 0.399106。

    上证指数覆盖沪市全部股票，深证综指覆盖深市全部股票，两者相加即两市总额。
    任一腿取不到就整体返回 None（宁缺勿造：用深证成指会少算深市一半以上）。
    """
    empty = {"total": None, "prev": None, "pct": None}
    sh = _kline_bars("1.000001", lmt=2)
    _sleep()
    sz = _kline_bars("0.399106", lmt=2)
    if not sh or not sz:
        return empty
    per_date: dict[str, float] = {}
    for bar in sh + sz:
        if bar["date"] and bar["amount"] is not None:
            per_date[bar["date"]] = per_date.get(bar["date"], 0.0) + bar["amount"]
    if len(per_date) < 2:
        return empty
    dates = sorted(per_date)
    total, prev = per_date[dates[-1]], per_date[dates[-2]]
    pct = round((total - prev) / prev * 100.0, 1) if prev else None
    return {
        "total": round(total / 1e8, 1),
        "prev": round(prev / 1e8, 1),
        "pct": pct,
        # total/prev 实际所属的交易日（YYYYMMDD 字符串）：上层用它决定能否回填 series 末点，
        # 避免把「昨天/别的交易日」的成交额贴到今天那一列上（宁缺勿错日期）
        "date": dates[-1],
    }


# --------------------------------------------------------------------------
# 本地兜底：东财拿不到时读通达信日线（数据是「上一收盘」，不伪造当日）
# --------------------------------------------------------------------------
_LOCAL_INDICES: tuple[tuple[str, str], ...] = (
    ("上证指数", "sh/lday/sh000001.day"),
    ("深证成指", "sz/lday/sz399001.day"),
    ("创业板指", "sz/lday/sz399006.day"),
)

_TAIL_BARS_AMOUNT = 12  # 成交额用：每个 .day 只读尾部这么多条，够覆盖目标日 + 前一交易日 + 停牌股


def fetch_index_quotes_local(target_date: int) -> list[dict]:
    """本地指数日线兜底：point 取收盘点位，pct 用前一条 bar 的收盘算涨跌幅。

    口径：``point = close``，``pct = (close / 前一条 close - 1) * 100`` 保留 2 位；
    取**不晚于** ``target_date`` 的最后一根 bar（通达信当天数据尚未下载时不报空，
    也不会用未来日期顶替），目标日之前一条 bar 都没有的指数直接跳过。
    每项带上 ``date``（该 bar 的 YYYYMMDD，int）= 卡片上显示的点位实际是哪天收盘，
    上层汇总成 ``indices_date`` 给前端标注；不再像以前那样取到日期又丢掉。
    结构：``[{"name", "point", "pct", "date"}]``，失败返回 []。
    """
    quotes: list[dict] = []
    for name, rel in _LOCAL_INDICES:
        path = TDX_VIPDOC / rel
        try:
            bars: list[Bar] = [bar for bar in read_day_file(path) if bar.date <= target_date]
        except Exception:  # noqa: BLE001 - 兜底通路同样不许抛
            continue
        if not bars:
            continue
        last = bars[-1]
        pct = None
        if len(bars) >= 2 and bars[-2].close:
            pct = round((last.close / bars[-2].close - 1.0) * 100.0, 2)
        quotes.append({"name": name, "point": last.close, "pct": pct, "date": last.date})
    return quotes


def _tail_dates_amounts(path: Path, count: int = _TAIL_BARS_AMOUNT) -> list[tuple[int, float]]:
    """只读 .day 文件尾部 ``count`` 条记录，返回 ``[(date, amount)]``；失败返回 []。

    .day 是定长 32 字节、按日期升序追加的，所以直接 seek 到文件尾往前取，
    不必把 1200 多根 bar 全解析（全市场 5000 个文件全量读要几十秒）。
    """
    size = _DAY_STRUCT.size
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            length = handle.tell()
            start = max(length - size * count, 0)
            start -= start % size  # 对齐到记录边界，防止半条记录
            handle.seek(start)
            chunk = handle.read(length - start)
    except OSError:
        return []
    rows: list[tuple[int, float]] = []
    for offset in range(0, len(chunk) - size + 1, size):
        day, _open, _high, _low, _close, amount, _volume, _reserved = _DAY_STRUCT.unpack_from(chunk, offset)
        if day > 0:
            rows.append((int(day), float(amount)))
    return rows


def fetch_market_amount_local(target_date: int) -> dict:
    """本地兜底：汇总全市场 A 股日线 amount（元）得到两市成交额。

    口径与 :func:`fetch_market_amount` 一致：``total`` / ``prev`` 单位是**亿元**
    （元 / 1e8，保留 1 位），``pct = (total / prev - 1) * 100`` 保留 1 位。
    目标日取「不晚于 target_date 的最后一个有数据的交易日」，prev 取它之前一天；
    只扫目标日与前一交易日的成交额，一天都取不到就返回全 None（不编数字）。
    另加覆盖度守卫：某天只有不到半数个股落地（通达信盘中/半更新）时视为无效，
    退到更早的完整交易日，避免把半天的累计当成整日成交额。
    """
    empty = {"total": None, "prev": None, "pct": None}
    per_date: dict[int, float] = {}
    per_count: dict[int, int] = {}
    for stock in list_stocks():
        for day, amount in _tail_dates_amounts(stock.path):
            if day <= target_date:
                per_date[day] = per_date.get(day, 0.0) + amount
                per_count[day] = per_count.get(day, 0) + 1
    if not per_date:
        return empty
    peak = max(per_count.values())
    days = sorted(day for day in per_date if per_count[day] * 2 >= peak)
    if not days:
        return empty
    total = per_date[days[-1]]
    prev = per_date[days[-2]] if len(days) >= 2 else None
    pct = round((total - prev) / prev * 100.0, 1) if prev else None
    return {
        "total": round(total / 1e8, 1),
        "prev": round(prev / 1e8, 1) if prev else None,
        "pct": pct,
    }


# --------------------------------------------------------------------------
# 涨跌家数 / 板块
# --------------------------------------------------------------------------
def fetch_breadth() -> dict:
    """全市场涨跌家数：来自 ``getTopicZDFenBu`` 的涨跌分布档位。

    返回 ``{"up": 上涨家数, "down": 下跌家数, "flat": 平盘家数}``，失败时三个值都是 None。
    该接口是**实时快照**，不支持历史日期，所以历史 up/down 只能留 None。
    """
    empty = {"up": None, "down": None, "flat": None}
    url = f"https://push2ex.eastmoney.com/getTopicZDFenBu?ut={_UT}&dpt=wz.ztzt&_=1"
    payload = _fetch_json(url)
    buckets = ((payload or {}).get("data") or {}).get("fenbu") or []
    up = down = flat = 0
    seen = False
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        for key, count in bucket.items():
            level, cnt = _num(key), _num(count)
            if level is None or cnt is None:
                continue
            seen = True
            if level > 0:
                up += int(cnt)
            elif level < 0:
                down += int(cnt)
            else:
                flat += int(cnt)
    if not seen:
        return empty
    return {"up": up, "down": down, "flat": flat}


def fetch_sector_rank() -> dict:
    """东财行业板块涨跌幅榜：``{"gain": [前5], "loss": [前5]}``。

    每项 ``{"name": 板块名, "pct": 涨跌幅%, "leader": 领涨股}``；取不到就空列表。
    """
    out = {"gain": [], "loss": []}
    for key, order in (("gain", 1), ("loss", 0)):
        url = (
            "https://push2.eastmoney.com/api/qt/clist/get"
            f"?pn=1&pz=5&po={order}&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:2"
            "&fields=f3,f12,f14,f128"
        )
        payload = _fetch_json(url)
        rows = ((payload or {}).get("data") or {}).get("diff") or []
        if isinstance(rows, dict):
            rows = list(rows.values())
        for row in rows:
            if not isinstance(row, dict):
                continue
            out[key].append(
                {"name": row.get("f14"), "pct": _num(row.get("f3")), "leader": row.get("f128")}
            )
        _sleep()
    return out


# --------------------------------------------------------------------------
# 涨停池（当日连板梯队明细）
# --------------------------------------------------------------------------
def fetch_zt_pool(date: str) -> list[dict]:
    """东财涨停池（akshare ``stock_zt_pool_em`` 的底层）：按 date 取当日涨停明细。

    每项字段严格对齐前端契约：
    ``code / name / boards(连板数) / fbt(首封时间 HH:MM:SS) / lbt(最后封板时间) /
    zbc(炸板次数) / fund(封单资金,亿元) / hybk(所属板块) / reason(涨停原因,恒为 None)``。

    ``getTopicZTPool`` 的返回里没有「涨停原因」这一列，只有 hybk（行业板块），
    所以 reason 一律给 None，不拿行业名去冒充原因。
    """
    date = str(date)
    if len(date) != 8 or not date.isdigit():
        return []
    pool: list[dict] = []
    total = None
    for page in range(MAX_POOL_PAGES):
        url = (
            "https://push2ex.eastmoney.com/getTopicZTPool"
            f"?ut={_UT}&dpt=wz.ztzt&Pageindex={page}&pagesize=170&sort=fbt%3Aasc&date={date}&_=1"
        )
        payload = _fetch_json(url)
        data = (payload or {}).get("data") or {}
        rows = data.get("pool") or []
        if not rows:
            break
        if total is None:
            total = _num(data.get("tc"))
        for row in rows:
            if not isinstance(row, dict):
                continue
            fund = _num(row.get("fund"))
            pool.append(
                {
                    "code": row.get("c"),
                    "name": row.get("n"),
                    "boards": _int(row.get("lbc")),
                    "fbt": _hhmmss(row.get("fbt")),
                    "lbt": _hhmmss(row.get("lbt")),
                    "zbc": _int(row.get("zbc")),
                    "fund": round(fund / 1e8, 2) if fund is not None else None,
                    "hybk": row.get("hybk") or None,
                    "reason": None,
                }
            )
        if total is None or len(pool) >= int(total):
            break
        _sleep()  # 只在实际翻页时等待
    return pool


# --------------------------------------------------------------------------
# 当日实时涨停/跌停家数（官方池子优先 + 全市场快照交叉校验）
# --------------------------------------------------------------------------
def _limit_ratio(code: Any, name: Any) -> float:
    """该股当日涨跌停幅度：科创/创业 20%，北交所 30%，主板 ST 5%，其余 10%。

    名称里的 ST 判定要求**名称含 "ST"**（含 ``*ST``、大小写、全角星号都能命中，
    因为只做 ``"ST" in name.upper()``）。
    """
    c = str(code or "")
    n = str(name or "").upper()
    if c.startswith(("688", "689", "300", "301", "302")):
        return 0.20
    if c.startswith(("920", "43", "83", "87", "88")):
        return 0.30
    if "ST" in n:  # 仅主板 ST 收窄到 5%（创业板/科创板 ST 仍是 20%，上面已先返回）
        return 0.05
    return 0.10


def _limit_price(prev: Any, ratio: float, sign: int) -> float | None:
    """按幅度反算涨停（sign=1）/跌停（sign=-1）价，四舍五入到分。

    交易所的涨停价是「昨收 × (1 ± 幅度) 四舍五入到 0.01」，必须用 Decimal
    ``ROUND_HALF_UP``：内建 ``round()`` 是银行家舍入，``round(11.055, 2)`` 会得到
    11.05（真实涨停价 11.06），这类股会被漏判。
    """
    value = _num(prev)
    if value is None or value <= 0:
        return None
    quote = Decimal(str(value)) * (Decimal(1) + Decimal(str(sign)) * Decimal(str(ratio)))
    return float(quote.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _pool_rows(kind: str, date: str) -> dict:
    """取东财官方涨停池（kind="zt"）/跌停池（kind="dt"）：``{"count": int|None, "rows": [...]}``。

    两个池子的排序参数**不能共用**（实测 20261106）：涨停池用 ``fbt:asc``；
    跌停池用 ``fbt:asc`` 会返回 ``rc=0`` 但 ``pool`` 为空，必须用 ``fund:asc``；
    不带 sort 直接 ``rc=102`` 参数非法。所以按 kind 分别给排序，别合并。
    """
    sort = "fbt%3Aasc" if kind == "zt" else "fund%3Aasc"
    endpoint = "getTopicZTPool" if kind == "zt" else "getTopicDTPool"
    rows: list[dict] = []
    count: int | None = None
    for page in range(MAX_POOL_PAGES):
        url = (
            f"https://push2ex.eastmoney.com/{endpoint}"
            f"?ut={_UT}&dpt=wz.ztzt&Pageindex={page}&pagesize=170&sort={sort}&date={date}&_=1"
        )
        payload = _fetch_json(url)
        data = (payload or {}).get("data") or {}
        page_rows = data.get("pool") or []
        if count is None:
            count = _int(data.get("tc"))
        if not page_rows:
            break
        for row in page_rows:
            if not isinstance(row, dict):
                continue
            price = _num(row.get("p"))
            rows.append(
                {
                    "code": row.get("c"),
                    "name": row.get("n"),
                    # 池子的 p 是「价格 × 1000」的整数（5010 → 5.01）
                    "price": round(price / 1000.0, 2) if price is not None else None,
                    "pct": _num(row.get("zdp")),
                }
            )
        if count is None or len(rows) >= count:
            break
        _sleep()  # 只在实际翻页时等待
    if count is None and rows:
        count = len(rows)
    return {"count": count, "rows": rows}


def fetch_snapshot_counts() -> dict:
    """拉一次全市场快照，按**涨停价反算**（口径 B）统计当日涨停/跌停。

    返回 ``{"rows": 拉到的条数, "total": 接口报的总数, "up": [...], "down": [...]}``；
    ``up``/``down`` 每项 ``{"code", "name", "pct", "price", "prev"}``。
    一条都拉不到时 ``rows=0``、两个列表空（调用方据此判失败，不写脏数据）。
    """
    rows: list[dict] = []
    total = 0
    for page in range(1, SNAPSHOT_MAX_PAGES + 1):
        url = (
            f"https://push2.eastmoney.com/api/qt/clist/get"
            f"?pn={page}&pz={SNAPSHOT_PAGE_SIZE}&po=1&np=1&fltt=2&invt=2&fid=f3"
            f"&fs={SNAPSHOT_FS}&fields={SNAPSHOT_FIELDS}"
        )
        payload = _fetch_json(url)
        data = (payload or {}).get("data") or {}
        page_rows = data.get("diff") or []
        if isinstance(page_rows, dict):  # 少数参数组合下 diff 是 {序号: row}
            page_rows = list(page_rows.values())
        total = _int(data.get("total")) or total
        if not page_rows:
            break
        rows.extend(page_rows)
        if total and len(rows) >= total:
            break
        _sleep()  # 约 30 页，每页之间留间隔，别把东财打挂
    up: list[dict] = []
    down: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        code, name = row.get("f12"), row.get("f14")
        price, prev, pct = _num(row.get("f2")), _num(row.get("f18")), _num(row.get("f3"))
        if price is None or prev is None or price <= 0 or prev <= 0:
            continue  # 停牌 / 缺价
        ratio = _limit_ratio(code, name)
        zt = _limit_price(prev, ratio, 1)
        dt = _limit_price(prev, ratio, -1)
        item = {"code": str(code), "name": name, "pct": pct, "price": price, "prev": prev}
        if zt is not None and abs(price - zt) < LIMIT_TOL:
            up.append(item)
        if dt is not None and abs(price - dt) < LIMIT_TOL:
            down.append(dict(item))
    return {"rows": len(rows), "total": total, "up": up, "down": down}


def live_limit_counts(date: str | None = None) -> dict | None:
    """当日实时涨停/跌停家数：**官方池子条数优先**，全市场快照自算做交叉校验。

    为什么以池子为准：池子（``getTopicZTPool`` / ``getTopicDTPool``）是东财对外
    一致口径，家数不随本地幅度规则猜；快照自算是「按涨停价反算」的独立口径，
    两者一致才算可信，不一致就原样写进 ``verify`` 交给上游判定，**不强行对齐**。

    一条都取不到（池子空 + 快照 0 条）时返回 ``None``：调用方不得写文件。

    已知口径差（20261106 实测，见 ``--check`` 输出）：TDX 基准 33 / 池子 32 /
    快照自算 32。差的那只是 **600537 \\*ST亿晶**——名称带 ``*ST`` 却连续多日按
    ±10% 幅度成交（1.40 → 1.54 → 1.69 → 1.86 → 2.05，均为 10% 幅度四舍五入后的
    涨停价），说明它当日适用 10% 而不是 ST 的 5%（退市整理类标的仍按 10% 幅度），
    东财涨停池也不收录它。所以：``build_market_stats.py`` 走 ``tdx.limit_pct()``
    没有 ST 5% 概念 → 把它算成涨停 → 33；池子与按「主板 ST→5%」判定的快照 → 32。
    """
    base = str(date or latest_stat_date(load_market_stats()) or time.strftime("%Y%m%d"))
    if len(base) != 8 or not base.isdigit():
        return None
    zt = _pool_rows("zt", base)
    _sleep()
    dt = _pool_rows("dt", base)
    _sleep()
    snap = fetch_snapshot_counts()

    snap_up = len(snap["up"]) if snap["rows"] else None
    snap_down = len(snap["down"]) if snap["rows"] else None
    zt_count = zt["count"] if (zt["count"] or 0) > 0 else snap_up
    dt_count = dt["count"] if dt["count"] is not None else snap_down
    if zt_count is None and dt_count is None:
        return None

    # 明细优先用池子（就是被计数的那个集合），池子空时退回快照自算的明细
    up_detail = zt["rows"] or snap["up"]
    down_detail = dt["rows"] or snap["down"]
    source = "eastmoney_pool" if zt["count"] is not None or dt["count"] is not None else "eastmoney_push2"
    return {
        "date": base,
        "limit_up": zt_count,
        "limit_down": dt_count,
        "source": source,
        "up_codes": [str(r.get("code")) for r in up_detail if r.get("code")],
        "down_codes": [str(r.get("code")) for r in down_detail if r.get("code")],
        "up_detail": up_detail,
        "down_detail": down_detail,
        "verify": {
            "zt_pool_count": zt["count"],
            "dt_pool_count": dt["count"],
            "snapshot_up": snap_up,
            "snapshot_down": snap_down,
            "snapshot_rows": snap["rows"],
            "snapshot_total": snap["total"],
            # 快照自算的明细（约 30-50 条，体积可忽略）：两份口径不一致时
            # 要靠它把差异个股的名称/涨幅打出来，否则只能看到一个光代码
            "snapshot_up_detail": snap["up"],
            "snapshot_down_detail": snap["down"],
        },
    }


def load_live_counts() -> dict | None:
    """读 ``data/cycle/live_counts.json``（``tools/build_live_counts.py`` 生成）。

    文件不存在 / 不是 JSON 对象 / 读取失败 → 一律 ``None``，由调用方保持 null 行为。
    """
    try:
        payload = json.loads(LIVE_COUNTS.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 落盘件缺失不许影响采集
        return None
    return payload if isinstance(payload, dict) else None


def apply_live_limit_counts(series: list | None, ladder: list | None, counts: dict | None) -> bool:
    """把当日实时涨停/跌停家数回填到 ``series[-1]`` 与 ``ladder[-1]``。

    **日期必须一致才写**：``counts["date"]`` 与目标条的 ``date`` 不等时跳过，
    避免把今天的家数贴到昨天（或周五）那根柱子上。任何异常都吞掉，绝不打断采集。
    返回是否至少写入了一个字段。
    """
    try:
        if not isinstance(counts, dict):
            return False
        date = str(counts.get("date") or "")
        up = _int(counts.get("limit_up"))
        down = _int(counts.get("limit_down"))
        if not date or (up is None and down is None):
            return False
        written = False
        for target in (series[-1] if series else None, ladder[-1] if ladder else None):
            if not isinstance(target, dict) or str(target.get("date")) != date:
                continue
            if up is not None:
                target["limit_up"] = up
                written = True
            if down is not None:
                target["limit_down"] = down
                written = True
        return written
    except Exception:  # noqa: BLE001 - 兜底：回填失败不许影响主流程
        return False


# --------------------------------------------------------------------------
# 本地历史：market_stats.json
# --------------------------------------------------------------------------
def load_market_stats() -> dict:
    """读 ``data/market_stats.json``（date -> 当日统计）；失败返回 {}。"""
    try:
        payload = json.loads(MARKET_STATS.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return payload if isinstance(payload, dict) else {}


def load_limit_ladder() -> dict:
    """读 ``data/limit_ladder.json``（F10 连板天梯，date -> [{code, level, ...}]）；失败返回 {}。

    连板高度的权威源。``build_market_stats.py`` 重写 ``height_stocks`` 时已经以它为优先，
    这里再缓存一份，供 ``build_ladder`` 对「market_stats 某日缺高度」的日期兜底，
    避免梯队/最高板在该日掉回本地口径。文件缺失 / 坏掉一律返回 {}，不抛异常。
    """
    global _LIMIT_LADDER_CACHE
    if _LIMIT_LADDER_CACHE is not None:
        return _LIMIT_LADDER_CACHE
    try:
        payload = json.loads(LIMIT_LADDER.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        payload = {}
    _LIMIT_LADDER_CACHE = payload if isinstance(payload, dict) else {}
    return _LIMIT_LADDER_CACHE


def load_st_hist() -> dict:
    """读 ``data/st_hist.json`` 的 ``days``（date -> {代码: 5|10} 档位）；失败返回 {}。

    ``build_ladder`` 要给 120 天梯队里每只股票判当日档位，所以首次读盘后缓存在模块级
    （``_ST_HIST_CACHE``），整个进程只加载一次。文件缺失 / JSON 坏掉一律返回 {}，不抛异常。
    """
    global _ST_HIST_CACHE
    if _ST_HIST_CACHE is not None:
        return _ST_HIST_CACHE
    try:
        payload = json.loads(ST_HIST.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 采集层契约要求不抛
        payload = {}
    days = payload.get("days") if isinstance(payload, dict) else None
    _ST_HIST_CACHE = days if isinstance(days, dict) else {}
    return _ST_HIST_CACHE


# 名称里可能出现的 ST 前缀（长的排前面，保证 `SST` / `S*ST` 不会被 `ST` 先咬掉一半）
_ST_PREFIXES = ("S*ST", "SST", "*ST", "ST")


def strip_st_prefix(name: str) -> str:
    """剥掉名称前的 ST 类前缀（``*ST`` / ``ST`` / ``SST`` / ``S*ST``）；剥空则保留原名。"""
    out = name
    for _ in range(len(_ST_PREFIXES)):
        for prefix in _ST_PREFIXES:
            if out.startswith(prefix):
                out = out[len(prefix):]
                break
        else:
            break
    out = out.strip()
    return out or name


def ladder_display_name(code: str, day_st: dict, names: dict) -> str | None:
    """历史日梯队里显示的名称：当日档位为 10（10% 档）时剥掉 ST 前缀，但必须保住 3 字全名。

    依据：``data/st_hist.json`` 的 band=10 是「该股当日处于 10% 涨跌停档」的硬证据，
    而 ST 股当日只能是 5% 档，所以 band=10 的股票当日一定**不是** ST。名称表
    （``data/stock_names.json``）只存**当前**名称，历史日直接用它就会把后来才戴上的
    ST 帽子贴到过去（如 600491 在 2026-07-14 显示成「ST龙元」）。其余情况
    （band=5、档位缺失且名称含 ST）一律保持原名。

    ``names`` 由调用方传入，可能是「F10 天梯当日行内 name + 当前名称」的合并表
    （见 ``build_ladder_rows``）：行内 name 是当日快照名，长度 >=3 时已优先。

    剥离纪律（用户定稿）：剥后不足 3 字**不剥**，保留原名。A 股名称基本 3~4 字，
    剥完只剩两个字几乎一定是把全名咬掉了一半（600491 现名「ST龙元」剥成「龙元」），
    用户已指出这种显示是错的；宁可保留带 ST 的 3~4 字原名，也不要给出 2 字残名。
    """
    name = names.get(code)
    if not isinstance(name, str) or not name:
        return name
    if day_st.get(code) == 10:
        stripped = strip_st_prefix(name).strip()
        if len(stripped) >= 3:
            return stripped
    return name


def load_stock_names() -> dict:
    """代码 -> 名称映射（``data/stock_names.json``）；失败返回 {}。"""
    try:
        payload = json.loads(STOCK_NAMES.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return payload if isinstance(payload, dict) else {}


def load_suspended_space() -> dict:
    """读 ``data/suspended_space.json``（停牌当日 -> 停牌个股列表）；失败返回 {}。

    文件不存在 / JSON 坏掉 / 顶层不是对象，一律返回 {}，**不抛异常**（面板不能因此崩）。
    """
    try:
        payload = json.loads(SUSPENDED_SPACE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 采集层契约要求不抛
        return {}
    return payload if isinstance(payload, dict) else {}


def load_auction_history() -> dict:
    """读 ``data/auction_history.json``（竞价日 -> {代码: 09:25 集合竞价成交额/亿元}）；失败返回 {}。

    120 天梯队里每只股票都要查一次，所以首次读盘后缓存在模块级（``_AUCTION_HISTORY_CACHE``），
    避免一轮 ``build_ladder`` 重复读盘 120 次。文件不存在 / JSON 坏掉 / 顶层不是对象，
    一律返回 {}，**不抛异常**，前端成交额全部降级成 ``null``。
    """
    global _AUCTION_HISTORY_CACHE
    if _AUCTION_HISTORY_CACHE is not None:
        return _AUCTION_HISTORY_CACHE
    try:
        payload = json.loads(AUCTION_HISTORY.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 采集层契约要求不抛
        payload = {}
    _AUCTION_HISTORY_CACHE = payload if isinstance(payload, dict) else {}
    return _AUCTION_HISTORY_CACHE


def _auction_yi(value: Any) -> float | None:
    """竞价成交额（亿元）：只接受有限数字，其余（None / 字符串 / NaN / inf）一律 None。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return round(number, 6)


def load_auction_volume_history() -> dict:
    """读 ``data/auction_volume_history.json``（竞价日 -> {代码: 09:25 竞价成交量/手}）。

    结构：``{"20261105": {"600206": 1234.5, ...}}``。该文件由 ``tools/auction_backfill.py``
    **并行**生成，回填未完成时可能不存在、只覆盖部分日期或正被原子替换 →
    一律降级成 {}（量比全部 null），**不抛异常、不打日志**；读盘后缓存在模块级。
    """
    global _AUCTION_VOLUME_CACHE
    if _AUCTION_VOLUME_CACHE is not None:
        return _AUCTION_VOLUME_CACHE
    try:
        payload = json.loads(AUCTION_VOLUME_HISTORY.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 采集层契约要求不抛（文件可能还没生成）
        payload = {}
    _AUCTION_VOLUME_CACHE = payload if isinstance(payload, dict) else {}
    return _AUCTION_VOLUME_CACHE


def _auction_lots(value: Any) -> float | None:
    """竞价成交量（手）：只接受有限正数，其余（None / 字符串 / NaN / 0）一律 None。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        return None
    return round(number, 3)


def _six_digits(code: Any) -> str | None:
    """把 sh600000 / 600000.SH 之类的写法归一成 6 位数字；不成形 → None。"""
    digits = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(digits) < 6:
        return None
    six = digits[-6:]
    return six if len(six) == 6 else None


def _day_path(code: Any) -> Path | None:
    """6 位代码 -> 本地 ``.day`` 路径；没有该文件返回 None（结果连 None 一起缓存）。

    定位逻辑复用 ``longtou.lhb.find_stock_file``，不复制实现；惰性 import 避免
    cycle_source ↔ lhb 的循环导入。文件不存在时该函数会退回全量扫描，所以「找不到」
    也必须缓存，否则 8820 个 (date, code) 对会把它扫上千遍。
    """
    six = _six_digits(code)
    if six is None:
        return None
    if six in _DAY_PATH_CACHE:
        return _DAY_PATH_CACHE[six]
    path: Path | None
    try:
        from .lhb import find_stock_file

        path = find_stock_file(six).path
    except Exception:  # noqa: BLE001 - 缺 .day / 坏代码都只降级
        path = None
    _DAY_PATH_CACHE[six] = path
    return path


def _day_tail(code: Any) -> bytes | None:
    """``.day`` 末尾 ``_TAIL_BARS`` 根 bar 的原始字节（按代码缓存）；读不到 → None。

    不用 ``tdx.read_day_file`` 整份解析：它把 1000+ 根 bar 全部转成 Bar 对象，
    8820 次调用实测 24.8s；量比只需要「竞价日之前最后一根」的 volume，读末尾
    300 根（9.6KB）就够，且按代码缓存后第二轮直接命中。
    """
    six = _six_digits(code)
    if six is None:
        return None
    if six in _DAY_TAIL_CACHE:
        return _DAY_TAIL_CACHE[six]
    raw: bytes | None = None
    path = _day_path(six)
    if path is not None:
        try:
            size = path.stat().st_size
            step = _DAY_STRUCT.size
            end = size - size % step  # 对齐到整根 bar，避免尾部半根写坏偏移
            start = max(0, end - _TAIL_BARS * step)
            with path.open("rb") as fh:
                fh.seek(start)
                raw = fh.read(end - start)
        except OSError:
            raw = None
    _DAY_TAIL_CACHE[six] = raw
    return raw


def prev_trading_volume(code: str, date: str) -> int | None:
    """``date``（竞价日 YYYYMMDD）之前最后一个交易日的成交量（股）。

    从末尾往前扫，第一条 ``bar.date < date`` 就是昨日那根（升序文件 → 反向即最近）。
    新股上市首日（前面没有 bar）/ 该股无 .day / 长期停牌（末尾 300 根内没有更早
    的 bar）→ None，前端量比降级成「—」。
    """
    raw = _day_tail(code)
    if not raw:
        return None
    try:
        day = int(date)
    except (TypeError, ValueError):
        return None
    step = _DAY_STRUCT.size
    for offset in range(len(raw) - step, -1, -step):
        d, _o, _h, _l, _c, _a, volume, _r = _DAY_STRUCT.unpack_from(raw, offset)
        if d <= 0:
            continue
        if d < day:
            value = int(volume)
            return value if value > 0 else None
    return None


def volume_ratio(lots: Any, prev_volume: Any) -> float | None:
    """竞价量比 = 竞价成交量(手) × 100 ÷ 昨日成交量(股)，保留 2 位小数。

    任一侧缺失 / 非正数 → None（前端显示 ``—``，不显示 0、NaN 或 inf）。
    """
    lot_value = _auction_lots(lots)
    prev_value = _num(prev_volume)
    if lot_value is None or prev_value is None or prev_value <= 0:
        return None
    return round(lot_value * 100.0 / prev_value, 2)


def latest_stat_date(stats: dict) -> str | None:
    """market_stats 里最新的交易日 YYYYMMDD（按字符串排序，键顺序不可信）。"""
    dates = [d for d in stats if isinstance(d, str) and len(d) == 8 and d.isdigit()]
    return max(dates) if dates else None


def window_limit(stats: dict, years: int = 3, fallback: int = 120) -> int:
    """「近 ``years`` 个自然年」窗口长度：从最新交易日所在年份往前推 ``years-1`` 年的
    1 月 1 日算起，返回该日以来的交易日个数（上限 ``260 * years``，下限 ``fallback``）。

    2026-11 回填 2025 全年时把窗口从「只有当年」放宽到「去年 + 今年」；2026-11 再
    放宽到 3 年（2024-01-01 起），把情绪波浪 / 梯队的起点补到 20240102。年份口径仍
    按自然年边界，跨年时最老的一年自动滚出窗口，不硬编码天数（交易日历每年不同，
    硬编码会在年初多截、年末少截）。面板四时点刷新走的是同一函数，窗口一致才不会
    把已回填的历史又截回两年。

    取不到最新日期（空表 / 键不合法）时退回 ``fallback``，保证后端在任何残缺数据下
    都不炸。260 是**单年**交易日安全上限（一年多不可能有 260 个交易日），只防脏数据
    把窗口拉爆。
    """
    latest = latest_stat_date(stats)
    if not latest:
        return fallback
    try:
        newest_year = int(latest[:4])
    except (TypeError, ValueError):
        return fallback
    span = max(int(years or 1), 1)
    year_start = f"{newest_year - (span - 1):04d}0101"
    count = 0
    for date in stats:
        if isinstance(date, str) and len(date) == 8 and date.isdigit() and date >= year_start:
            count += 1
    return max(fallback, min(count, 260 * span))


def ytd_limit(stats: dict, fallback: int = 120) -> int:
    """兼容旧名：等价于 ``window_limit(stats, years=1)``（只看当个自然年）。"""
    return window_limit(stats, years=1, fallback=fallback)


_AMOUNT_INDEX_LEGS: tuple[str, ...] = (
    "sh/lday/sh000001.day",  # 上证指数：覆盖沪市全部股票
    "sz/lday/sz399106.day",  # 深证综指：覆盖深市全部股票
)


def load_market_amount_history() -> dict[str, float]:
    """历史逐日两市成交额（亿元）。

    与 :func:`fetch_market_amount` 同一口径（上证指数 + 深证综指，日线 amount 单位**元**，
    相加后 /1e8 保留 1 位 = 亿元），只是改从本地通达信指数日线取全历史：
    两个指数的 .day 各存约 5 年，足够覆盖 series 的窗口（2024-01 起）。

    两腿都有该日才收（缺一腿就少算一半以上，宁缺勿造：该日 **不出现**，上层取到 None，
    前端黄线在该点断开，不补数）。文件缺失/损坏/解析异常一律返回 {}（整条线不画）。
    """
    legs: list[dict[int, float]] = []
    for rel in _AMOUNT_INDEX_LEGS:
        path = TDX_VIPDOC / rel
        try:
            bars = read_day_file(path)
        except Exception:  # noqa: BLE001 - 兜底通路不许抛，缺文件就整条放弃
            return {}
        per_day: dict[int, float] = {}
        for bar in bars:
            if bar.date and bar.amount:
                per_day[int(bar.date)] = per_day.get(int(bar.date), 0.0) + float(bar.amount)
        if not per_day:
            return {}
        legs.append(per_day)
    sh, sz = legs
    return {
        str(day): round((sh[day] + sz[day]) / 1e8, 1) for day in sh.keys() & sz.keys()
    }


def build_series(stats: dict, limit: int | None = None) -> list[dict]:
    """近两年（去年 + 今年）的情绪序列（升序，最后一个是基准日）。

    ``limit`` 不传时按 ``window_limit(stats)``（窗口＝最近三个自然年年初以来的交易日，
    上限 780；2026-11 回填后默认是 2024-01-02 ~ 2026-11-18 共 696 天左右）；
    显式传值时按传入条数截取。

    up/down 在 market_stats 里没有来源，历史日期统一给 None（不编数字）；
    基准日由上层用实时涨跌家数补齐。

    ``suspended_height`` / ``suspended_codes`` 来自 ``data/suspended_space.json``，
    键就是「停牌当日」：当日停牌的个股没有交易，连板高度不作废，所以前端压力位
    要把这个高度也算进去（P 不因最高板个股停牌而下移）。当日无停牌记录 → ``null`` / ``[]``；
    该文件缺失或解析失败时全部按 ``null`` 降级，只退化成「不看停牌」，不抛异常。

    ``amount`` = 当日两市成交额（亿元，见 :func:`load_market_amount_history`）：面板黄线要用，
    和涨跌停家数同一批日期点。口径与头部 ``amount`` 卡片一致（上证指数 + 深证综指 /1e8），
    某日两腿没凑齐就 ``None``（不补数），基准日由上层用实时值回填。
    """
    suspended = load_suspended_space()
    amounts = load_market_amount_history()
    if limit is None:
        limit = window_limit(stats)
    dates = sorted(d for d in stats if isinstance(d, str) and len(d) == 8 and d.isdigit())
    series: list[dict] = []
    for date in dates[-limit:]:
        row = stats.get(date) or {}
        day_suspended = suspended.get(date)
        codes: list[str] = []
        height: int | None = None
        if isinstance(day_suspended, list):
            for item in day_suspended:
                if not isinstance(item, dict):
                    continue
                code = item.get("code")
                if code not in (None, ""):
                    codes.append(str(code))
                # 同一天多条停牌记录时取最大高度：压力位只需要「停牌中的最高板」
                value = _int(item.get("height"))
                if value is not None and (height is None or value > height):
                    height = value
        series.append(
            {
                "date": date,
                "max_height": _int(row.get("max_height")),
                "height_ex": _int(row.get("max_height_ex_one_word")),
                "limit_up": _int(row.get("limit_up")),
                "limit_down": _int(row.get("limit_down")),
                # 当日两市成交额（亿元）：面板黄线用，历史点来自本地指数日线
                "amount": amounts.get(date),
                "up": None,
                "down": None,
                "suspended_height": height,
                "suspended_codes": codes,
            }
        )
    return series


def build_ladder(
    stats: dict,
    limit: int | None = None,
    names: dict | None = None,
    realtime_lots: dict | None = None,
) -> list[dict]:
    """近两年（去年 + 今年）的连板梯队（升序）。

    ``limit`` 不传时按 ``window_limit(stats)``（窗口同上：最近两个自然年，上限 520），
    与 ``build_series`` 完全同源：前端情绪波浪折线与梯队方块共用同一批日期，
    窗口不一致时梯队会被截短、图也只剩很短一段。

    ``levels`` 按 boards 从高到低，来源是 market_stats 的 ``height_stocks``
    （boards -> [代码]），名称补自 ``data/stock_names.json``，缺了给 None。

    每只股票额外带 ``auction_yi``：当日 09:25 集合竞价成交额（亿元），来自
    ``data/auction_history.json``（``tools/auction_backfill.py`` 生成）。
    该文件缺失 / 坏掉 / 当日该股没记录 → ``auction_yi`` 为 ``None``（前端不渲染这一行），
    不影响其余字段，也不抛异常。

    竞价量比三件套（2026-11 新增，口径 = 竞价成交量 ÷ 昨日成交量）：

    * ``auction_volume_lots``：09:25 竞价成交量（手），来自
      ``data/auction_volume_history.json``；该文件回填未完成时一律 ``None``。
    * ``prev_volume``：竞价日之前最后一个交易日的成交量（股），读本地 .day；
      新股 / 停牌 / 无日线 → ``None``。
    * ``volume_ratio``：``auction_volume_lots * 100 / prev_volume``（round 2）；
      任一侧缺失 → ``None``。

    优先级：``realtime_lots``（同进程注入的实时 ``/auction.json`` 值，形如
    ``{代码: 手}``）只作用于**最新交易日**；没有实时值时退回历史文件。
    ``tools/cycle_collect.py`` 不注入 → 纯历史口径。
    """
    names = names or {}
    if limit is None:
        limit = window_limit(stats)
    auction = load_auction_history()
    volume_history = load_auction_volume_history()
    ladder_cache = load_limit_ladder()
    st_days = load_st_hist()
    realtime = realtime_lots if isinstance(realtime_lots, dict) else {}
    latest = latest_stat_date(stats)
    dates = sorted(d for d in stats if isinstance(d, str) and len(d) == 8 and d.isdigit())
    out: list[dict] = []
    for date in dates[-limit:]:
        row = stats.get(date) or {}
        height_stocks = row.get("height_stocks") or {}
        # F10 优先兜底：market_stats 该日无高度（旧口径/未重算）但 F10 有记录时，直接按 F10 level 组层。
        if not isinstance(height_stocks, dict) or not height_stocks:
            f10_rows = ladder_cache.get(date)
            if isinstance(f10_rows, list) and f10_rows:
                height_stocks = {}
                for r in f10_rows:
                    if not isinstance(r, dict):
                        continue
                    code = str(r.get("code") or "")
                    level = r.get("level")
                    if code and level is not None:
                        height_stocks.setdefault(str(int(level)), []).append(code)
        day_auction = auction.get(date)
        if not isinstance(day_auction, dict):
            day_auction = {}
        day_volume = volume_history.get(date)
        if not isinstance(day_volume, dict):
            day_volume = {}
        # 最新交易日允许用实时值覆盖历史回填（历史日期没有实时源）
        live = date == latest and bool(realtime)
        # 当日档位表：band=10 的股票当日不是 ST，名称要去掉后来才戴上的 ST 帽子
        day_st = st_days.get(date)
        if not isinstance(day_st, dict):
            day_st = {}
        # 名称源优先级：F10 天梯**当日行内 name**（当日快照名，长度 >=3）> 当前名称表。
        # 行内 name 多为 3~4 字全名，且能取到改名前的历史名（如 20240926 603517 行内
        # 「绝味食品」vs 现名「ST绝味」）；不足 3 字的（含占位/空名）不采用，仍用名称表。
        day_names = names
        f10_name_rows = ladder_cache.get(date) if isinstance(ladder_cache, dict) else None
        if isinstance(f10_name_rows, list):
            merged: dict | None = None
            for row in f10_name_rows:
                if not isinstance(row, dict):
                    continue
                row_code = str(row.get("code") or "")
                row_name = row.get("name")
                if not row_code or not isinstance(row_name, str):
                    continue
                row_name = row_name.strip()
                if len(row_name) < 3:
                    continue
                if merged is None:
                    merged = dict(day_names)
                merged[row_code] = row_name
            if merged is not None:
                day_names = merged
        levels: list[dict] = []
        if isinstance(height_stocks, dict):
            for key in sorted(height_stocks, key=lambda k: _int(k) or 0, reverse=True):
                codes = height_stocks.get(key) or []
                if not codes:
                    continue
                stocks: list[dict] = []
                for code in codes:
                    code_text = str(code)
                    lots = _auction_lots(realtime.get(code_text)) if live else None
                    if lots is None:
                        lots = _auction_lots(day_volume.get(code_text))
                    prev_volume = prev_trading_volume(code_text, date)
                    stocks.append(
                        {
                            "code": code_text,
                            "name": ladder_display_name(code_text, day_st, day_names),
                            "auction_yi": _auction_yi(day_auction.get(code_text)),
                            "auction_volume_lots": lots,
                            "prev_volume": prev_volume,
                            "volume_ratio": volume_ratio(lots, prev_volume),
                        }
                    )
                levels.append({"boards": _int(key), "stocks": stocks})
        # 最高板优先取 market_stats 的 max_height；缺失时退回「levels 里最大 boards」
        #（levels 已含 F10 优先兜底，保证与梯队口径一致，不随本地日线掉级）。
        max_height = _int(row.get("max_height"))
        if max_height is None:
            max_height = max((lvl["boards"] or 0 for lvl in levels), default=None)
        out.append(
            {
                "date": date,
                "max_height": max_height,
                "levels": levels,
            }
        )
    return out


def compute_temperature(row: dict) -> int | None:
    """情绪温度 0–100：由 market_stats 当日四项指标加权得到的经验分。

    经验公式（**经验权重，未做历史校准**，只求单调、可解释，不做概率解释）::

        temp = 30 * min(max_height / 8, 1)          # 空间高度：最高板 8 板封顶
             + 30 * min(limit_up / 100, 1)          # 涨停家数：100 家封顶
             + 20 * (1 - min(limit_down / 20, 1))   # 跌停越少越好：20 家打满
             + 20 * min(连板家数 / limit_up, 1)      # 连板占比：梯队厚度

    其中「连板家数」= height_counts 里 boards>=2 的家数之和。
    四段权重合计 100，分段各自截断在 [0,1]，所以输出天然落在 0–100。
    当日 limit_up 与 max_height 同时为 0 视为数据缺失，返回 None。
    """
    row = row or {}
    max_height = _int(row.get("max_height")) or 0
    limit_up = _int(row.get("limit_up")) or 0
    limit_down = _int(row.get("limit_down")) or 0
    if limit_up <= 0 and max_height <= 0:
        return None
    counts = row.get("height_counts") or {}
    lianban = 0
    if isinstance(counts, dict):
        for key, value in counts.items():
            boards, cnt = _int(key), _int(value)
            if boards is not None and boards >= 2 and cnt:
                lianban += cnt
    height_part = 30.0 * min(max_height / 8.0, 1.0)
    breadth_part = 30.0 * min(limit_up / 100.0, 1.0)
    loss_part = 20.0 * (1.0 - min(limit_down / 20.0, 1.0))
    ladder_part = 20.0 * min(lianban / limit_up, 1.0) if limit_up else 0.0
    total = height_part + breadth_part + loss_part + ladder_part
    return int(round(max(0.0, min(total, 100.0))))


# --------------------------------------------------------------------------
# 聚合
# --------------------------------------------------------------------------
def _retry(fn, is_empty, tries: int = 2, gap: float = 1.5):
    """跑 ``fn``，结果为空就再试一次。

    东财 push2 系列对连发请求会间歇性 ``RemoteDisconnected``（实测同一 URL 前一次成功、
    后一次被拒），单跑一次很容易出现「这条腿空、那条腿有」。空结果重试一次能把
    一次采集的整体成功率拉高，每次重试只多一次请求。
    """
    result = fn()
    for _ in range(max(tries - 1, 0)):
        if not is_empty(result):
            break
        time.sleep(gap)
        result = fn()
    return result


def collect(date: str | None = None) -> dict:
    """跑一次采集，返回 ``/cycle.json`` 契约里的完整对象。

    ``date`` 缺省取 market_stats 的最新交易日（数据基准日）。指数、成交额、
    板块、涨跌家数是实时快照，基准日是 T-1 时会与历史断面存在一天错位，
    这是数据源限制，不做伪造。
    """
    stats = load_market_stats()
    names = load_stock_names()
    base = date or latest_stat_date(stats) or time.strftime("%Y%m%d")
    row = stats.get(base) or {}
    try:
        base_day = int(base)
    except (TypeError, ValueError):
        base_day = 0

    breadth = _retry(fetch_breadth, lambda r: r.get("up") is None)
    indices = _retry(fetch_index_quotes, lambda r: not r)
    # 实时源 ulist.np 只回现价与涨跌幅，**没有 bar 日期** → indices_date 留 None，前端不标注（不编日期）
    indices_date = None
    if not indices:  # 东财 push2 不通时退本地通达信日线，仍为空就不编数字
        indices = fetch_index_quotes_local(base_day)
        # 本地兜底取的是 .day 末条 bar：点位其实是「那天收盘」，不是基准日尾盘，前端必须显式标注
        indices_date = next((q.get("date") for q in indices if q.get("date")), None)
    _sleep()
    amount = _retry(fetch_market_amount, lambda r: r.get("total") is None)
    if amount.get("total") is None:
        amount = fetch_market_amount_local(base_day)
    _sleep()
    boards_today = _retry(lambda: fetch_zt_pool(base), lambda r: not r)
    _sleep()
    sector_top = _retry(fetch_sector_rank, lambda r: not r.get("gain") and not r.get("loss"))

    series = build_series(stats, window_limit(stats))
    if series and breadth.get("up") is not None:
        series[-1]["up"] = breadth["up"]
        series[-1]["down"] = breadth["down"]
    # 成交额末点回填：本地指数日线要等收盘后下载才更新，盘中那一格会是 None。
    # 只有实时源给出的成交额**确实属于 series 末点那一天**（amount["date"] 对齐）时才用它顶替，
    # 保证图上最后一点与头部「成交额」卡片是同一个数；日期对不上就不动，绝不贴错日期。
    if series and amount.get("total") is not None and amount.get("date") == series[-1]["date"]:
        series[-1]["amount"] = amount["total"]
    # 梯队窗口跟 series 完全一致：前端同一批日期要同时画折线和梯队方块
    ladder = build_ladder(stats, len(series), names)
    # 当日涨停/跌停家数：读 tools/build_live_counts.py 的落盘件，**与上面的 breadth 解耦**
    # （广度失败不影响家数回填，反之亦然）。日期与 series[-1]/ladder[-1] 不一致、
    # 文件缺失、读取异常 → 一律静默跳过，保持原来的 null 行为，绝不打断采集。
    apply_live_limit_counts(series, ladder, load_live_counts())
    temperature = compute_temperature(row)

    # 涨跌家数逐日历史无来源：market_stats 只有涨停/跌停，没有全市场涨跌家数。
    return {
        "date": base,
        "indices": [
            {"name": q.get("name"), "point": q.get("point"), "pct": q.get("pct")} for q in indices
        ],
        # 指数点位实际所属的 bar 日期（YYYYMMDD int）；实时源能给当天现价时为 None
        "indices_date": indices_date,
        "amount": amount,
        "temperature": temperature,
        "breadth": breadth,
        "series": series,
        "ladder": ladder,
        "boards_today": boards_today,
        "sector_top": sector_top,
    }


# --------------------------------------------------------------------------
# 小工具：安全类型转换
# --------------------------------------------------------------------------
def _num(value: Any) -> float | None:
    """转 float；"-"/""/None/异常一律 None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _int(value: Any) -> int | None:
    number = _num(value)
    return None if number is None else int(number)


def _hhmmss(value: Any) -> str | None:
    """东财的封板时间是整数 HHMMSS（如 92500），转成 ``"09:25:00"``。"""
    raw = _int(value)
    if raw is None or raw <= 0:
        return None
    hour, minute, second = raw // 10000, (raw // 100) % 100, raw % 100
    if hour > 23 or minute > 59 or second > 59:
        return None
    return f"{hour:02d}:{minute:02d}:{second:02d}"
