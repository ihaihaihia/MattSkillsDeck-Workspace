#!/usr/bin/env python3
"""Standalone local panel server for dsh-longtou.

The DSH-injected panel and this server share the same ``lib/panel.js``.
Open ``http://127.0.0.1:8765/`` in a browser (or use ``启动独立面板.cmd`` for an
Edge/Chrome app window) to use the panel outside the DSH window.

The server reads ``output/latest.md`` on every request; the panel's 60-second
poll therefore sees scheduled 09:25 reports without any DSH web route.
"""

from __future__ import annotations

import argparse
import ctypes
import hmac
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "lib" / "panel.js"
LATEST = ROOT / "output" / "latest.md"
# 情绪周期数据：由 tools/cycle_collect.py 生成（缺失时返回 {} 而不是 500）
CYCLE = ROOT / "data" / "cycle" / "latest.json"
# 进程启动时间：/health 用它给前端判断「还是不是同一个进程」，重启前后必然不同。
STARTED_AT = time.strftime("%Y-%m-%d %H:%M:%S")
# 公网口令闸：--token 传入口令后，任何请求都必须带 ?k=<口令> 或等价 Cookie。
# 空串 = 不校验（只在本机/局域网用时不打扰）。手机首次访问
# https://xxx.trycloudflare.com/?k=<口令> 即可，服务端种 Cookie 后自动跳回干净地址。
TOKEN = ""
COOKIE_NAME = "dsh_lt"
# 09:25 集合竞价成交额：/auction.json 的数据源。
# 官方撮合由 longtou.auction.AuctionSource 现取，只在进程内存缓存 120 秒：
# 不落盘、不改任何数据文件；取不到就返回空 items，前端那一格不渲染。
REPORTS = ROOT / "output" / "reports"
AUCTION_TTL_SECONDS = 120.0
AUCTION_MAX_CODES = 40
_AUCTION_CACHE: dict[tuple[str, str], dict[str, object]] = {}
# 历史回落源（回填产物）：该日实时取不到时按同日读这里，结构「日期 -> 代码 -> 值」。
AUCTION_HISTORY = ROOT / "data" / "auction_history.json"
AUCTION_VOLUME_HISTORY = ROOT / "data" / "auction_volume_history.json"
# 历史文件按 (mtime, size) 缓存在进程内存：回填脚本写完自动失效重读，不必重启面板。
_AUCTION_FILE_CACHE: dict[str, tuple[tuple[int, int], dict[str, object]]] = {}

# 手动刷新（/refresh 触发、/refresh.json 查状态）：面板上的「立刻补数据」按钮。
# 后台跑 tools/panel_refresh.py --auto，状态只放进程内存（重启即归零），进程输出追加到
# output/refresh_manual.log 供事后排查。任何异常都必须回 JSON，绝不 500。
REFRESH_SCRIPT = ROOT / "tools" / "panel_refresh.py"
REFRESH_LOG = ROOT / "output" / "refresh_manual.log"
MARKET_STATS = ROOT / "data" / "market_stats.json"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
AUTO_TIMES = ["09:25", "11:30", "13:00", "15:00"]
CATCHUP_TIMES = ["15:30", "16:30"]
CN_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
# eltdx 日线探测结果的缓存时长（秒）：前端每 60 秒轮询 /refresh.json，
# 不能每次轮询都去连 eltdx；探测失败/抛异常也要缓存，避免断网时反复阻塞。
BAR_PROBE_TTL_SECONDS = 120.0
_REFRESH_LOCK = threading.Lock()
# proc 保留最近一次的后台进程对象；finished_at 为空表示「还没结算完」。
_REFRESH_STATE: dict[str, object] = {
    "proc": None,
    "phase": None,
    "started_at": None,
    "finished_at": None,
    "ok": None,
    "rc": None,
}
_BAR_PROBE: dict[str, object] = {"ts": 0.0, "today": False}
# 时点表的本地兜底副本：正常走 panel_refresh.resolve_phase，导入失败才用这份。
_PHASE_FALLBACK: tuple[tuple[int, str], ...] = (
    (9 * 60 + 25, "open"),
    (11 * 60 + 30, "noon"),
    (13 * 60, "pm"),
    (15 * 60, "close"),
)


def _cn_today() -> str:
    """Asia/Shanghai 的今天（YYYYMMDD）。显式带时区，不依赖机器本地时区设置。"""
    return datetime.now(CN_TZ).strftime("%Y%m%d")


def resolve_refresh_phase(now: datetime) -> str:
    """这次 ``--auto`` 会跑哪个阶段：与 panel_refresh.resolve_phase 完全同规则。

    时点表只在 panel_refresh.py 里维护一份，这里直接调它的函数，免得两处各写一张表后漂移；
    导入失败（脚本被改坏等）时退回本地同序表——算不出阶段也不能让 /refresh 失败。
    """
    try:
        spec = importlib.util.spec_from_file_location("_dsh_lt_panel_refresh", REFRESH_SCRIPT)
        if spec is not None and spec.loader is not None:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return str(module.resolve_phase(now))
    except Exception:  # noqa: BLE001 - 兜底见下
        pass
    minutes = now.hour * 60 + now.minute
    chosen = _PHASE_FALLBACK[0][1]
    for start, name in _PHASE_FALLBACK:
        if minutes >= start:
            chosen = name
    return chosen


def today_bar_ready() -> bool:
    """Asia/Shanghai 今天是否已出日线（eltdx 只读探测，结果缓存 120 秒）。

    探测方式与 panel_refresh.today_bar_ready 一致：取贵州茅台最近两根日线看有没有今天。
    任何异常（缺 eltdx、断网、超时）一律按「今天没有日线」处理并照常写缓存：
    这是给前端看的提示位，不能让一次网络抖动把 /refresh.json 打成 500 或每次都卡住。
    """
    now = time.time()
    if now - float(_BAR_PROBE.get("ts") or 0.0) < BAR_PROBE_TTL_SECONDS:
        return bool(_BAR_PROBE.get("today"))
    today = int(_cn_today())
    ready = False
    script = (
        "import sys;"
        f"sys.path.insert(0, r'{ROOT}');"
        "from eltdx import TdxClient;"
        "c=TdxClient(timeout=10);"
        "r=c.bars.get(['sh600519'],period='day',start=0,count=2,batch_size=1) or {};"
        "s=r.get('sh600519');"
        "days=[int(b.time.strftime('%Y%m%d')) for b in (s.bars if s else [])];"
        f"print('ready',{today} in days);"
        "c.__exit__(None,None,None)"
    )
    try:
        proc = subprocess.run(
            [str(PYTHON), "-c", script],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=40,
            creationflags=0x08000000,  # CREATE_NO_WINDOW：不弹黑窗
        )
        ready = "ready True" in (proc.stdout or "")
    except Exception:  # noqa: BLE001 - 探测失败按「没有今日日线」降级
        ready = False
    _BAR_PROBE["ts"] = now
    _BAR_PROBE["today"] = ready
    return ready


def data_date() -> str:
    """面板数据的最新已落盘交易日：优先 cycle/latest.json 的 date，退回 market_stats 最大 key。"""
    try:
        payload = json.loads(CYCLE.read_text(encoding="utf-8"))
        day = str(payload.get("date") or "").strip()
        if len(day) == 8 and day.isdigit():
            return day
    except Exception:  # noqa: BLE001 - 缺文件/半截 JSON 都退回下一数据源
        pass
    try:
        keys = [
            key
            for key in json.loads(MARKET_STATS.read_text(encoding="utf-8"))
            if isinstance(key, str) and len(key) == 8 and key.isdigit()
        ]
        if keys:
            return max(keys)
    except Exception:  # noqa: BLE001 - 两个数据源都读不到就是空串，前端自己判空
        pass
    return ""


def _reap_refresh_locked() -> None:
    """惰性结算：后台进程已退出就记下 rc/ok/finished_at（只结算一次，调用方须持锁）。"""
    proc = _REFRESH_STATE.get("proc")
    if proc is None or _REFRESH_STATE.get("finished_at") is not None:
        return
    try:
        rc = proc.poll()
    except Exception:  # noqa: BLE001 - poll 异常就当还在跑
        return
    if rc is None:
        return
    _REFRESH_STATE["rc"] = int(rc)
    _REFRESH_STATE["ok"] = int(rc) == 0
    _REFRESH_STATE["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")


def _watch_refresh(proc: subprocess.Popen) -> None:
    """守护线程：等这次刷新进程退出后落 finished_at/ok/rc，/refresh.json 立刻能看到结果。"""
    try:
        proc.wait()
    except Exception:  # noqa: BLE001 - 等不到就交给下次查询时的惰性结算
        pass
    try:
        with _REFRESH_LOCK:
            if _REFRESH_STATE.get("proc") is proc:
                _reap_refresh_locked()
    except Exception:  # noqa: BLE001 - 线程里绝不能让异常冒出去
        pass


def refresh_status() -> dict[str, object]:
    """/refresh.json 的完整载荷：最近一次手动刷新的状态 + 数据新鲜度。

    所有外部读取（磁盘、eltdx）都自己吞异常，保证这里一定拼得出完整字段。
    """
    with _REFRESH_LOCK:
        _reap_refresh_locked()
        proc = _REFRESH_STATE.get("proc")
        running = False
        if proc is not None:
            try:
                running = proc.poll() is None
            except Exception:  # noqa: BLE001 - 拿不到状态就当没在跑
                running = False
        # phase/started_at 反映「最近一次触发」；finished_at/ok/rc 只在跑完后才有值。
        phase = _REFRESH_STATE.get("phase")
        started_at = _REFRESH_STATE.get("started_at")
        finished_at = None if running else _REFRESH_STATE.get("finished_at")
        ok = None if running else _REFRESH_STATE.get("ok")
        rc = None if running else _REFRESH_STATE.get("rc")
    today_ready = today_bar_ready()
    current = data_date()
    today = _cn_today()
    expected = today if today_ready else current
    return {
        "running": running,
        "phase": phase,
        "started_at": started_at,
        "finished_at": finished_at,
        "ok": ok,
        "rc": rc,
        "log": "output/refresh_manual.log",
        "data_date": current,
        "expected_date": expected,
        "stale": expected > current,
        "trading_day": bool(today_ready),
        "auto_times": list(AUTO_TIMES),
        "catchup_times": list(CATCHUP_TIMES),
    }


def start_refresh() -> dict[str, object]:
    """非阻塞触发一次 ``panel_refresh.py --auto``；已有一次在跑就不重复起。"""
    log_fh = None
    with _REFRESH_LOCK:
        _reap_refresh_locked()
        proc = _REFRESH_STATE.get("proc")
        if proc is not None:
            try:
                if proc.poll() is None:
                    return {"ok": True, "started": False, "running": True}
            except Exception:  # noqa: BLE001 - 状态不明时按「没在跑」继续
                pass
        try:
            REFRESH_LOG.parent.mkdir(parents=True, exist_ok=True)
            # 追加写：一次刷新的完整输出都进 output/refresh_manual.log，便于事后对齐失败阶段。
            log_fh = REFRESH_LOG.open("a", encoding="utf-8")
            log_fh.write(f"\n===== /refresh 触发 at={time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            log_fh.flush()
            child = subprocess.Popen(
                [str(PYTHON), str(REFRESH_SCRIPT), "--auto"],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                creationflags=0x08000000,  # CREATE_NO_WINDOW：服务是隐藏进程，子进程也不弹窗
            )
        except Exception as exc:  # noqa: BLE001 - 起不来就如实回报，不 500
            if log_fh is not None:
                try:
                    log_fh.close()
                except Exception:
                    pass
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            # 子进程已继承自己的句柄，父进程这份必须关掉，否则日志长期占用不刷新。
            if log_fh is not None:
                try:
                    log_fh.close()
                except Exception:
                    pass
        phase = resolve_refresh_phase(datetime.now(CN_TZ))
        started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        _REFRESH_STATE.update(
            {
                "proc": child,
                "phase": phase,
                "started_at": started_at,
                "finished_at": None,
                "ok": None,
                "rc": None,
            }
        )
    try:
        threading.Thread(target=_watch_refresh, args=(child,), daemon=True).start()
    except Exception:  # noqa: BLE001 - 线程起不来就靠查询时惰性结算
        pass
    return {
        "ok": True,
        "started": True,
        "running": True,
        "phase": phase,
        "started_at": started_at,
        "log": "output/refresh_manual.log",
    }


def _latest_report_codes() -> list[str]:
    """从最新一份 output/reports/<YYYYMMDD>.md 表格里取候选代码（第 2 列的 6 位数字）。"""
    try:
        files = sorted(
            (p for p in REPORTS.glob("*.md") if p.stem.isdigit()),
            key=lambda p: p.stem,
        )
    except OSError:
        return []
    if not files:
        return []
    try:
        text = files[-1].read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    codes: list[str] = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = line.strip().strip("|").split("|")
        if len(cells) < 2:
            continue
        code = cells[1].strip()
        if len(code) == 6 and code.isdigit() and code not in codes:
            codes.append(code)
    return codes


def _load_auction_file(path: Path) -> dict[str, object]:
    """读 auction_history.json / auction_volume_history.json（按 mtime+size 缓存，坏文件按空表）。"""
    try:
        stat = path.stat()
    except OSError:
        return {}
    stamp = (stat.st_mtime_ns, stat.st_size)
    cached = _AUCTION_FILE_CACHE.get(str(path))
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 半截 / 坏 JSON 都不该拖垮面板
        return {}
    if not isinstance(data, dict):
        return {}
    _AUCTION_FILE_CACHE[str(path)] = (stamp, data)
    return data


def _auction_history_entry(date_text: str, code: str) -> dict[str, object] | None:
    """实时取不到时回落历史文件：成交额取 auction_history.json（亿元），量比取成交量文件（手）。

    返回与实时条目同形的 dict；该日该代码没有成交额记录 → None（前端整格留空）。
    历史文件没有价格 → price 为 None（前端不读这个字段）。
    """
    row = _load_auction_file(AUCTION_HISTORY).get(date_text)
    if not isinstance(row, dict):
        return None
    amount_yi = row.get(code)
    if not isinstance(amount_yi, (int, float)) or isinstance(amount_yi, bool):
        return None
    volume_lots = None
    vol_row = _load_auction_file(AUCTION_VOLUME_HISTORY).get(date_text)
    if isinstance(vol_row, dict):
        raw_vol = vol_row.get(code)
        if isinstance(raw_vol, (int, float)) and not isinstance(raw_vol, bool):
            volume_lots = int(round(float(raw_vol)))
    prev_volume = None
    volume_ratio = None
    try:
        from longtou import cycle_source

        prev_volume = cycle_source.prev_trading_volume(code, date_text)
        if volume_lots is not None:
            volume_ratio = cycle_source.volume_ratio(volume_lots, prev_volume)
    except Exception:  # noqa: BLE001 - 量比算不出就只显示成交额，不影响金额那一侧
        pass
    return {
        "amount_yuan": round(float(amount_yi) * 1e8, 2),
        "amount_yi": round(float(amount_yi), 2),
        "price": None,
        "volume_lots": volume_lots,
        "prev_volume": prev_volume,
        "volume_ratio": volume_ratio,
        "source": "history",
    }


def auction_payload(raw_codes: str, date_text: str) -> dict[str, object]:
    """现取当日 09:25 官方集合竞价成交额（元，附亿元换算）。

    任何异常都返回空 items（HTTP 仍 200），与 /cycle.json 的容错口径一致：
    面板少一个格子也不能整页崩。缓存只放进程内存，绝不写盘。
    """
    items: dict[str, dict[str, object]] = {}
    payload: dict[str, object] = {"ok": False, "date": date_text, "items": items}
    codes = [c.strip() for c in (raw_codes or "").split(",") if c.strip()]
    if not codes:
        codes = _latest_report_codes()
    codes = [c for c in codes if len(c) == 6 and c.isdigit()][:AUCTION_MAX_CODES]
    if not codes:
        payload["error"] = "no codes"
        return payload
    now = time.time()
    # 清过期条目，避免按 (date, code) 细分后缓存无限长（面板长跑不重启）。
    for stale_key in [k for k, v in _AUCTION_CACHE.items() if now - float(v.get("ts", 0.0)) >= AUCTION_TTL_SECONDS]:
        _AUCTION_CACHE.pop(stale_key, None)
    history_hits = 0
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from longtou.auction import AuctionSource
        # 竞价量比：昨日成交量与量比口径复用 cycle_source（与 /cycle.json 梯队同源、
        # 共用其 .day 读盘缓存），避免两处各写一套除法和取 bar 逻辑。
        from longtou import cycle_source

        with AuctionSource(timeout=6.0) as source:
            for code in codes:
                key = (date_text, code)
                cached = _AUCTION_CACHE.get(key)
                if cached and now - float(cached.get("ts", 0.0)) < AUCTION_TTL_SECONDS:
                    items[code] = {k: v for k, v in cached.items() if k != "ts"}
                    continue
                try:
                    auction = source.fetch(code, date_text)
                except Exception:
                    auction = None
                if auction is None:
                    # 实时取不到（历史日 / 当日 09:25 还没撮合 / 源不给）→ 回落历史文件；
                    # 历史文件也没有就留空，绝不编造。
                    fallback = _auction_history_entry(date_text, code)
                    if fallback is None:
                        continue
                    history_hits += 1
                    _AUCTION_CACHE[key] = {**fallback, "ts": now}
                    items[code] = fallback
                    continue
                # 昨日成交量（股）= 竞价日之前最后一个交易日的 .day volume；
                # 新股 / 停牌 / 无日线 → None，前端量比降级成「—」。
                try:
                    prev_volume = cycle_source.prev_trading_volume(code, date_text)
                except Exception:  # noqa: BLE001 - 少一列也不能让整份竞价数据消失
                    prev_volume = None
                entry: dict[str, object] = {
                    "amount_yuan": round(float(auction.amount_yuan), 2),
                    "amount_yi": round(float(auction.amount_yuan) / 1e8, 2),
                    "price": round(float(auction.price), 2),
                    "volume_lots": int(auction.volume_lots),
                    # 竞价量比 = 竞价成交量(手) × 100 ÷ 昨日成交量(股)，round 2
                    "prev_volume": prev_volume,
                    "volume_ratio": cycle_source.volume_ratio(int(auction.volume_lots), prev_volume),
                    "source": "live",
                }
                _AUCTION_CACHE[key] = {**entry, "ts": now}
                items[code] = entry
        payload["ok"] = bool(items)
        payload["history_fallback"] = history_hits
    except Exception as exc:  # noqa: BLE001 - 缺 eltdx / 断网都只降级，不 500
        payload["error"] = f"{type(exc).__name__}: {exc}"
        # 整条实时链路挂了（缺 eltdx / 断网）也要能用历史文件出数。
        for code in codes:
            fallback = _auction_history_entry(date_text, code)
            if fallback is None:
                continue
            items[code] = fallback
            history_hits += 1
        payload["ok"] = bool(items)
        payload["history_fallback"] = history_hits
    return payload


def _panel_windows() -> list[int]:
    """枚举标题含“龙头面板”的顶层窗口（Edge/Chrome 的 --app 窗口）。"""
    user32 = ctypes.windll.user32
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL

    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if "龙头面板" in buf.value:
                found.append(hwnd)
        return True

    user32.EnumWindows(_enum, 0)
    return found


def set_topmost(on: bool) -> int:
    """把面板窗口设为/取消系统级置顶，行为等同微信聊天窗口的“置顶”。

    返回命中的窗口数量；0 表示面板窗口没开着（或标题被改过）。
    """
    user32 = ctypes.windll.user32
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        wintypes.INT,
        wintypes.INT,
        wintypes.INT,
        wintypes.INT,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    insert_after = wintypes.HWND(-1 if on else -2)  # HWND_TOPMOST / HWND_NOTOPMOST
    flags = 0x0001 | 0x0002 | 0x0010  # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
    windows = _panel_windows()
    for hwnd in windows:
        user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0, flags)
    return len(windows)


class PanelServer(ThreadingHTTPServer):
    """面板 HTTP 服务。

    Windows 下 ``allow_reuse_address`` 默认是开的（SO_REUSEADDR），会让**多个进程同时绑定
    同一个端口**：请求被内核随机派给旧进程，表现就是「改了代码却不生效」「/cycle.json 偶发
    404」。这里显式关掉——端口被占就直接 bind 失败退出，永远不会出现两个实例抢答。
    """

    allow_reuse_address = False
    daemon_threads = True


RESTART_FLAG = "--restart-server"  # restart_server 自己这一条进程链的命令行标记
RESTART_LOG = ROOT / "data" / "panel_restart.log"
CHILD_LOG = ROOT / "data" / "panel_restart_child.log"
# 重启互斥体名（Global\ 前缀保证跨会话可见）：同一时刻只允许一个辅助进程执行重启。
RESTART_MUTEX = "Global\\dsh-longtou-panel-restart"


def _child_log(message: str) -> None:
    """把重启辅助进程的诊断信息落盘（data/panel_restart_child.log）并同步到 stderr。

    必须落盘：``/restart`` 拉起子进程时 stdout/stderr 都指向这个文件，前台直接跑则打到控制台。
    重启链路里任何一步失败都不允许再无声无息——静默失败会让「服务没了」完全无从排查。
    """
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [pid {os.getpid()}] {message}"
    try:
        if CHILD_LOG.exists() and CHILD_LOG.stat().st_size > 1_000_000:
            CHILD_LOG.write_text("", encoding="utf-8")  # 防止无限增长
    except Exception:
        pass
    try:
        print(line, file=sys.stderr, flush=True)
    except Exception:
        pass
    try:
        CHILD_LOG.parent.mkdir(parents=True, exist_ok=True)
        with CHILD_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _panel_server_procs() -> list[dict[str, object]]:
    """列出所有 panel_server 进程，带 pid/ppid/命令行。

    ``.venv\\Scripts\\python.exe`` 是启动器壳：它 CreateProcess 出真实解释器后等其退出，
    所以一个实例在进程表里是「壳 + 真实解释器」两个 pid，监听端口的是真实解释器，
    且**杀壳会连带杀掉真实解释器**（实测：杀壳后真实解释器随即消失）。
    重启时必须准确区分「自己这条链」和「要杀的老实例」，否则会把自己杀掉。
    """
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' OR Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -like '*panel_server.py*' } | "
        "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except Exception as exc:  # noqa: BLE001 - 枚举失败必须留痕
        _child_log(f"_panel_server_procs 执行失败: {exc!r}")
        return []
    raw = (completed.stdout or "").strip()
    if not raw:
        return []
    try:
        rows = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 - 解析失败同样留痕
        _child_log(f"_panel_server_procs 解析失败: {exc!r} raw={raw[:400]!r}")
        return []
    if isinstance(rows, dict):
        rows = [rows]
    procs: list[dict[str, object]] = []
    for row in rows:
        try:
            procs.append(
                {
                    "pid": int(row.get("ProcessId")),
                    "ppid": int(row.get("ParentProcessId") or 0),
                    "cmdline": str(row.get("CommandLine") or ""),
                }
            )
        except Exception:
            continue
    return procs


def _panel_server_pids() -> list[int]:
    """所有 panel_server 进程的 pid（壳 + 真实解释器都在内，一个实例通常 2 个）。"""
    return [int(p["pid"]) for p in _panel_server_procs()]


def _port_busy(host: str, port: int) -> bool:
    """能连上就说明还有人占着端口。"""
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


_LOCAL_OPENER: urllib.request.OpenerDirector | None = None


def _health_pid(host: str, port: int, timeout: float = 1.0) -> int | None:
    """读 /health 的 pid。

    端口有人监听 ≠ 我们的服务活着，所以重启成功与否只认「/health 报出一个新 pid」。
    显式禁用代理：本机 127.0.0.1 不该走 HTTP_PROXY，否则环境里有代理时会误判失败。
    """
    global _LOCAL_OPENER
    try:
        if _LOCAL_OPENER is None:
            _LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with _LOCAL_OPENER.open(f"http://{host}:{port}/health", timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return int(payload.get("pid"))
    except Exception:
        return None


def _kill_pid(pid: int) -> bool:
    """taskkill 单个 pid；失败只留痕，不打断整次重启。"""
    try:
        completed = subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=0x08000000,
        )
    except Exception as exc:  # noqa: BLE001 - 留痕
        _child_log(f"taskkill {pid} 异常: {exc!r}")
        return False
    if completed.returncode == 0:
        return True
    text = (completed.stderr or "").strip()
    if "没有找到" in text or "not found" in text.lower():
        # 常态竞态：杀掉 .venv 启动器壳时真实解释器已随之退出，再来杀它就「找不到」。
        _child_log(f"taskkill {pid}: 进程已不存在（随启动器壳一并退出），视为已终止")
        return True
    _child_log(f"taskkill {pid} 失败(rc={completed.returncode}): {text}")
    return False


def _acquire_restart_mutex() -> bool:
    """抢占「正在重启」命名互斥体；抢到返回 True，已有辅助进程在重启则返回 False。

    为什么用命名互斥体而不是锁文件：辅助进程可能被 taskkill 或崩溃打断，锁文件这时
    会残留，下一次重启被误判成「已有重启在进行」而永久跳过，只能手工删文件才能恢复；
    命名互斥体由内核在进程退出（含被杀）时自动释放，不存在残留。

    句柄刻意不显式关闭：辅助进程只做一次重启就退出，退出即释放；显式 CloseHandle 需要
    用 try/finally 包住整个 restart_server（多处 return），改动面更大且更容易出错。

    互斥体拿不到时按「放行」处理：并发保护是加固项，不能因为它让重启本身不可用。
    """
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        ctypes.set_last_error(0)  # 先清残留的 last error，避免把旧值误读成「已存在」
        handle = kernel32.CreateMutexW(None, True, RESTART_MUTEX)
        err = ctypes.get_last_error()
    except Exception as exc:  # noqa: BLE001 - 加固项失败不许拖垮重启
        _child_log(f"创建重启互斥体异常: {type(exc).__name__}: {exc}；本次不做并发保护")
        return True
    if not handle:
        _child_log(f"创建重启互斥体失败(GetLastError={err})；本次不做并发保护")
        return True
    if err == 183:  # ERROR_ALREADY_EXISTS：另一个辅助进程正持有它
        return False
    return True


def _launch_exe() -> str:
    """返回拉起子进程用的解释器路径：优先 pythonw.exe，避免冒出黑色控制台窗口。

    ``sys.executable`` 若是控制台版 python.exe，重启/置顶拉起的子进程会各自带一个黑窗；
    这些子进程只做后台活（重启、设置窗口置顶），不需要控制台，所以换成同目录的
    pythonw.exe（GUI 子系统，无控制台）。找不到 pythonw.exe 时退回原路径，不改变行为。
    """
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        candidate = exe.with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return str(exe)


def restart_server(host: str, port: int) -> int:
    """重启面板服务：杀掉老实例（壳 + 真实解释器）→ 等端口释放 → 只拉起一个新实例。

    由 ``/restart`` 路由拉起本文件的第二个副本执行，所以先 sleep 让父进程把 HTTP 响应刷出去。
    顺序不能反：**必须**确认端口已释放再拉起，否则会重新长出多进程抢答的老毛病。

    关键陷阱（历史故障根因）：本进程是 ``.venv\\Scripts\\python.exe`` 启动器壳的子进程，
    而两者命令行都含 ``--restart-server``。若按「所有 panel_server 进程减去 os.getpid()」
    取受害者，就会把自己的壳也 taskkill 掉——壳被杀会连带杀掉本进程，重启在 Popen 之前
    就无声终止（exit=1、无 traceback、日志不更新、老实例已死、新实例没起）。
    因此受害者只取「命令行不含 --restart-server 的 panel_server 进程」。
    """
    time.sleep(0.6)
    if not _acquire_restart_mutex():
        _child_log("已有重启正在进行（命名互斥体被占用），本次跳过：不重复杀老实例、不重复拉新实例")
        return 0
    me = os.getpid()
    old_pid = _health_pid(host, port)
    procs = _panel_server_procs()

    victims = [
        int(p["pid"])
        for p in procs
        if int(p["pid"]) != me and RESTART_FLAG not in str(p["cmdline"])
    ]
    _child_log(
        "restart 开始: "
        f"me={me} old_pid={old_pid} victims={victims} 全部进程={[(p['pid'], p['ppid']) for p in procs]}"
    )

    for pid in victims:
        _kill_pid(pid)

    deadline = time.time() + 8.0
    while time.time() < deadline and _port_busy(host, port):
        time.sleep(0.25)
    if _port_busy(host, port):
        error = "端口没有释放（老实例没被杀掉，或还有别的进程在监听）"
        _child_log(f"restart 失败: {error}")
        _write_restart_log(False, victims, error, old_pid=old_pid, new_pid=None)
        return 1

    try:
        CHILD_LOG.parent.mkdir(parents=True, exist_ok=True)
        with CHILD_LOG.open("a", encoding="utf-8") as log_fh:
            proc = subprocess.Popen(
                [_launch_exe(), str(Path(__file__).resolve()), "--host", host, "--port", str(port)]
                + (["--token", TOKEN] if TOKEN else []),
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=log_fh,
                creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            )
        spawn_pid = proc.pid
    except Exception as exc:  # noqa: BLE001 - 起不来就是失败，必须落盘
        error = f"拉起新实例失败: {type(exc).__name__}: {exc}"
        _child_log(f"restart 失败: {error}\n{traceback.format_exc()}")
        _write_restart_log(False, victims, error, old_pid=old_pid, new_pid=None)
        return 1
    _child_log(f"已拉起新实例(shell pid={spawn_pid})，等待 /health 报出新 pid")

    new_pid = None
    deadline = time.time() + 18.0
    while time.time() < deadline:
        time.sleep(0.3)
        pid = _health_pid(host, port)
        if pid is not None and pid != old_pid and pid != me and pid not in victims:
            new_pid = pid
            break
    if new_pid is None:
        current = _health_pid(host, port)
        error = f"新实例没起来（/health 没报出新 pid，当前={current}）"
        _child_log(f"restart 失败: {error}")
        _write_restart_log(False, victims, error, old_pid=old_pid, new_pid=current)
        return 1

    _child_log(f"restart 成功: old_pid={old_pid} new_pid={new_pid}")
    _write_restart_log(True, victims, None, old_pid=old_pid, new_pid=new_pid)
    return 0


def _write_restart_log(
    ok: bool, victims: list[int], error: str | None, **extra: object
) -> None:
    """把重启结果落盘（前端也提示看这个文件）。

    写失败不再静默：至少把异常打进 panel_restart_child.log / stderr。
    """
    payload: dict[str, object] = {
        "ok": ok,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "killed": victims,
        "error": error,
    }
    payload.update(extra)
    try:
        RESTART_LOG.parent.mkdir(parents=True, exist_ok=True)
        RESTART_LOG.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - 落盘失败也要留痕
        _child_log(f"写 {RESTART_LOG} 失败: {exc!r}\n{traceback.format_exc()}")


INDEX = """<!doctype html>
<html lang="zh-CN" style="background:#0b0b0e">
<head>
<meta charset="utf-8">
<meta name="color-scheme" content="dark">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>龙头面板</title>
<style>
html,body{margin:0;min-height:100vh;background:#0b0b0e;overflow:hidden}
</style>
</head>
<body class="dsh-longtou-standalone" style="background:#0b0b0e">
<script>
window.__DSH_LONGTOU_LATEST_URL = '/latest.md';
window.__DSH_LONGTOU_AUTO_OPEN = true;
window.__DSH_LONGTOU_TOPMOST = true;
</script>
<script src="/panel.js?v=__PANEL_V__"></script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "dsh-longtou-panel/1.0"

    def _send(
        self,
        status: int,
        content_type: str,
        body: bytes,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _cookie_token(self) -> str:
        raw = self.headers.get("Cookie", "") or ""
        match = re.search(rf"(?:^|;\s*){COOKIE_NAME}=([^;]+)", raw)
        return match.group(1) if match else ""

    def _gate(self) -> bool:
        """口令闸：返回 True 表示继续处理；False 表示已经回好了响应。

        未配置 TOKEN 时永远放行。带 ?k=<口令> 进来时种 Cookie 并 302 跳到
        去掉口令的同一地址，避免口令长期留在手机地址栏/历史里。
        """
        if not TOKEN:
            return True
        query = parse_qs(urlparse(self.path).query)
        supplied = query.get("k", [""])[0]
        if supplied and hmac.compare_digest(supplied, TOKEN):
            if self.command == "GET":
                clean = {key: value[0] for key, value in query.items() if key != "k"}
                target = urlparse(self.path).path
                if clean:
                    target += "?" + urlencode(clean)
                # 记住 7 天：公网窗口期（最长 5 天）内不用再贴带口令的链接。
                cookie = f"{COOKIE_NAME}={TOKEN}; Path=/; Max-Age=604800; HttpOnly; SameSite=Lax"
                self._send(
                    302,
                    "text/html; charset=utf-8",
                    b"",
                    {"Location": target, "Set-Cookie": cookie},
                )
                return False
            return True
        if hmac.compare_digest(self._cookie_token(), TOKEN):
            return True
        body = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>需要口令</title>
<body style="font-family:system-ui;background:#111;color:#eee;padding:40px 20px;line-height:1.7">
<h3>这个面板需要口令</h3>
<p>请用完整链接打开（包含 <code>?k=</code> 后面的口令）；打开一次后会记住，之后直接访问即可。</p>
</body></html>""".encode("utf-8")
        self._send(401, "text/html; charset=utf-8", body)
        return False

    def do_GET(self) -> None:  # noqa: N802
        if not self._gate():
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            # 用 panel.js 的修改时间做缓存版本号，避免窗口重启后仍命中旧 JS。
            try:
                ver = str(int(PANEL.stat().st_mtime))
            except OSError:
                ver = "0"
            body = INDEX.replace("__PANEL_V__", ver).encode("utf-8")
            self._send(200, "text/html; charset=utf-8", body)
            return
        if path == "/panel.js":
            try:
                body = PANEL.read_bytes()
            except OSError as exc:
                self._send(500, "text/plain; charset=utf-8", str(exc).encode("utf-8"))
                return
            self._send(200, "application/javascript; charset=utf-8", body)
            return
        if path == "/latest.stamp":
            # 轻量版本戳（mtime+size）：前端每分钟轮询先比它，值没变就不必拉 50MB 全量。
            # mtime 足以覆盖「重跑但内容相同」「重跑且内容变了」两种情况。
            try:
                st = LATEST.stat()
                stamp = f"{int(st.st_mtime)}-{st.st_size}"
            except OSError:
                stamp = "0-0"
            self._send(200, "text/plain; charset=utf-8", stamp.encode("utf-8"))
            return
        if path == "/latest.md":
            if LATEST.exists():
                body = LATEST.read_bytes()
            else:
                body = b"# dsh-longtou\n\nNo report yet.\n"
            self._send(200, "text/markdown; charset=utf-8", body)
            return
        if path == "/report.md":
            # 单日日报（回测看盘用）：点情绪波浪里的某一天时，面板下方要显示**那一天**的完整
            # 日报（含当日 09:25 竞价口径的候选表），而不只是梯队快照。数据源就是
            # output/reports/<YYYYMMDD>.md（与拼接 latest.md 用的是同一批文件）。
            # GET 必须无副作用且快：文件不存在直接 404，绝不在这里懒生成日报。
            query = parse_qs(urlparse(self.path).query)
            date_text = query.get("date", [""])[0]
            if not re.fullmatch(r"\d{8}", date_text):
                self._send(
                    400,
                    "application/json; charset=utf-8",
                    json.dumps({"ok": False, "reason": "bad-date"}, ensure_ascii=False).encode("utf-8"),
                )
                return
            # 防目录穿越：日期正则已经卡死 8 位数字，这里再断言取到的就是裸文件名本身。
            name = os.path.basename(date_text)
            target = REPORTS / f"{name}.md"
            if name != date_text or not target.is_file():
                self._send(
                    404,
                    "application/json; charset=utf-8",
                    json.dumps(
                        {"ok": False, "reason": "missing", "date": date_text},
                        ensure_ascii=False,
                    ).encode("utf-8"),
                )
                return
            try:
                body = target.read_bytes()
            except OSError:
                # 读失败按「这天没有日报」处理：前端回落梯队快照，不影响其它区块
                self._send(
                    404,
                    "application/json; charset=utf-8",
                    json.dumps(
                        {"ok": False, "reason": "missing", "date": date_text},
                        ensure_ascii=False,
                    ).encode("utf-8"),
                )
                return
            self._send(
                200,
                "text/markdown; charset=utf-8",
                body,
                {"X-Report-Date": date_text},
            )
            return
        if path == "/cycle.json":
            # 情绪周期数据（tools/cycle_collect.py 生成）。文件缺失或半截时返回 {}，
            # 绝不 500：前端一个字段取不到只丢一块，不该整页崩。
            body = b"{}"
            try:
                candidate = CYCLE.read_bytes()
                json.loads(candidate)  # 校验完整性，避免读到写一半的文件
                body = candidate
            except Exception:
                body = b"{}"
            self._send(200, "application/json; charset=utf-8", body)
            return
        if path == "/auction.json":
            # 09:25 集合竞价成交额（亿元）。codes 可省略：服务端从最新报告候选里取。
            # 任何异常都回 200 + 空 items，绝不 500（与 /cycle.json 同一容错口径）。
            query = parse_qs(urlparse(self.path).query)
            codes_arg = query.get("codes", [""])[0]
            date_text = query.get("date", [""])[0].strip()
            if date_text and not (len(date_text) == 8 and date_text.isdigit()):
                # 显式给了 date 却不合法 → 400，绝不静默换成今天（那正是历史日报串味的老毛病）
                _bad = json.dumps(
                    {"ok": False, "date": date_text, "items": {}, "reason": "bad-date"},
                    ensure_ascii=False,
                ).encode("utf-8")
                self._send(400, "application/json; charset=utf-8", _bad)
                return
            if not date_text:
                # 不带 date 保持旧行为：今天。
                date_text = time.strftime("%Y%m%d")
            try:
                payload = auction_payload(codes_arg, date_text)
            except Exception as exc:  # noqa: BLE001 - 兜底：连 helper 崩了也要 200
                payload = {
                    "ok": False,
                    "date": date_text,
                    "items": {},
                    "error": f"{type(exc).__name__}: {exc}",
                }
            self._send(
                200,
                "application/json; charset=utf-8",
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )
            return
        if path == "/health":
            # 必须带 body：前端靠 started/pid 判断「是不是换了一个进程」，
            # 只回 {"ok": true} 的话重启前后一模一样，判定必然失效。
            payload = {"ok": True, "pid": os.getpid(), "started": STARTED_AT}
            self._send(200, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))
            return
        if path == "/topmost":
            # 面板“置顶”按钮：把独立面板窗口设为系统级置顶（等同微信窗口置顶）。
            # 桌面 API 交给独立子进程执行，避免任何卡顿拖住页面请求。
            query = parse_qs(urlparse(self.path).query)
            on = query.get("on", ["1"])[0] != "0"
            try:
                subprocess.Popen(
                    [_launch_exe(), str(Path(__file__).resolve()), "--set-topmost", "1" if on else "0"],
                    cwd=str(Path(__file__).resolve().parents[1]),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                )
                payload = {"ok": True, "topmost": on, "queued": True}
            except Exception as exc:  # noqa: BLE001 - 起进程失败时如实回报
                payload = {"ok": False, "topmost": on, "error": str(exc)}
            self._send(200, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))
            return
        if path == "/refresh.json":
            # 面板「数据刷新」状态：最近一次 /refresh 触发的进程状态 + 数据新鲜度。
            # 与 /cycle.json 同一容错口径：字段尽量齐、绝不 500（出错则附带 error 说明）。
            try:
                payload = refresh_status()
            except Exception as exc:  # noqa: BLE001 - 兜底：连 helper 崩了也要回 JSON
                payload = {
                    "running": False,
                    "phase": None,
                    "started_at": None,
                    "finished_at": None,
                    "ok": False,
                    "rc": None,
                    "log": "output/refresh_manual.log",
                    "data_date": "",
                    "expected_date": "",
                    "stale": False,
                    "trading_day": False,
                    "auto_times": list(AUTO_TIMES),
                    "catchup_times": list(CATCHUP_TIMES),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            self._send(
                200,
                "application/json; charset=utf-8",
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )
            return
        if path == "/refresh":
            # GET 也能触发（便于浏览器地址栏/curl 手工补一次数据），行为与 POST 完全一致。
            try:
                payload = start_refresh()
            except Exception as exc:  # noqa: BLE001 - 触发失败只回 JSON 错误，绝不 500
                payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            self._send(
                200,
                "application/json; charset=utf-8",
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )
            return
        if path == "/favicon.ico":
            self._send(204, "image/x-icon", b"")
            return
        self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self) -> None:  # noqa: N802
        if not self._gate():
            return
        path = urlparse(self.path).path
        if path == "/refresh":
            # 面板「立刻刷新」按钮：非阻塞起后台进程，立即返回；已有一次在跑就不重复起。
            try:
                payload = start_refresh()
            except Exception as exc:  # noqa: BLE001 - 触发失败只回 JSON 错误，绝不 500
                payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            self._send(
                200,
                "application/json; charset=utf-8",
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )
            return
        if path == "/restart":
            # 面板右上角「重启」按钮：拉起一个独立子进程去杀老实例再拉新实例。
            # 这里只负责受理，真正的重启在 restart_server() 里做（它要先等本响应刷出去）。
            try:
                host, port = self.server.server_address[0], self.server.server_address[1]
                CHILD_LOG.parent.mkdir(parents=True, exist_ok=True)
                # 子进程的输出必须落到文件：重定向到 DEVNULL 的话，辅助进程崩了我们将
                # 拿不到任何 traceback（历史上正是「exit=1、stdout 全空」的元凶之一）。
                with CHILD_LOG.open("a", encoding="utf-8") as child_log:
                    subprocess.Popen(
                        [
                            _launch_exe(),
                            str(Path(__file__).resolve()),
                            RESTART_FLAG,
                            str(host),
                            str(port),
                        ]
                        + (["--token", TOKEN] if TOKEN else []),
                        cwd=str(ROOT),
                        stdin=subprocess.DEVNULL,
                        stdout=child_log,
                        stderr=child_log,
                        creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                    )
                payload = {"ok": True, "restarting": True}
                self._send(200, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))
            except Exception as exc:  # noqa: BLE001 - 起进程失败时如实回报
                payload = {"ok": False, "error": str(exc)}
                self._send(500, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))
            return
        self._send(404, "text/plain; charset=utf-8", b"not found")

    def log_message(self, format: str, *args: object) -> None:
        # Keep the background server quiet; failures are still visible in the
        # process exit/output if the port is occupied.
        return


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--token",
        default="",
        help="公网口令闸：给出后所有请求都必须带 ?k=<口令>（首次访问后种 Cookie）",
    )
    parser.add_argument("--set-topmost", choices=("0", "1"), default=None)
    parser.add_argument(
        "--restart-server",
        nargs=2,
        metavar=("HOST", "PORT"),
        default=None,
        help="内部用：杀掉所有 panel_server 实例并重新拉起唯一一个",
    )
    args = parser.parse_args()
    # 口令是进程级全局：重启子进程链也要继承，否则重启后面板会变成无闸状态。
    global TOKEN
    TOKEN = args.token or ""
    if args.restart_server is not None:
        # 由 /restart 路由拉起的短命子进程：负责完成一次真正的重启
        try:
            return restart_server(args.restart_server[0], int(args.restart_server[1]))
        except Exception as exc:  # noqa: BLE001 - 任何异常都必须可见地失败
            message = f"{type(exc).__name__}: {exc}"
            _child_log(f"restart_server 异常: {message}\n{traceback.format_exc()}")
            _write_restart_log(False, [], message)
            return 1
    if args.set_topmost is not None:
        # 由 /topmost 路由拉起的短命子进程：只做一次桌面置顶设置
        count = set_topmost(args.set_topmost == "1")
        print(json.dumps({"windows": count}), flush=True)
        return 0
    try:
        server = PanelServer((args.host, args.port), Handler)
    except OSError as exc:
        print(f"panel server failed to bind {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 1
    print(f"dsh-longtou standalone panel: http://{args.host}:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
