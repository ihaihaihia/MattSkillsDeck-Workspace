#!/usr/bin/env python3
"""A 股面板定时刷新入口：每天四个时点（本地时区 Asia/Shanghai，周一至周五）。

时点与动作
----------
09:25  集合竞价结束（开盘价确定）  cycle_collect.py + auto_task.py --mode auction + 盘中快照
11:30  上午收盘                    cycle_collect.py + 盘中快照
13:00  下午开盘                    cycle_collect.py + 盘中快照
15:00  下午收盘                    cycle_collect.py + auto_task.py --mode close + 清盘中标记

「盘中快照」= ``tools/intraday_snapshot.py``：cycle 的基准日取自 market_stats 最新交易日
（只有收盘后才有 T），盘中那次 cycle_collect 只能拿到 T-1。所以 open/noon/pm 三个时点在
cycle_collect 之后用东财实时数据（指数/成交额/涨跌家数/涨停跌停池含连板数）算当天值，
把「当日行」并入 ``data/cycle/latest.json``（行级与顶层都带 ``intraday=true`` + ``asof``），
面板据此照常渲染 series/ladder，只是最新一根是盘中值。close 阶段正式行重建后会兜底清标记。

由 Windows 计划任务 ``dsh-longtou-panel-refresh`` 调用（动作固定，``--auto`` 按
当前时间判阶段；四个触发器共用同一个动作，所以阶段判定必须放在脚本里）。
15:30 / 16:30 另有两个兜底触发器（同样跑 ``--auto``，落在 close 阶段）：15:00 那次
被中断或 eltdx 当时还没出日线时，这两次会把收盘数据补上。

设计约束
--------
* **不重启 8765 面板服务**：面板每 60 秒自己重读 ``/cycle.json`` 与 ``/latest.md``，
  本脚本只写数据文件，不碰进程。
* **幂等**：同一时点重复执行只是重写 ``data/cycle/*.json`` 与当天报告，不叠加任务/线程。
* **周末由计划任务触发器排除**；法定节假日无内置日历，仍会空跑一次：
  ``auto_task.py`` 会先查 TDX 交易日历并打印 ``not trading day`` 后正常退出，
  ``cycle_collect.py`` 只是把同一基准日的数据重写一遍。
* 15:00 收盘时 eltdx 日线可能还差几十秒，close 阶段会先做一次只读探测，
  缺当天日线时最多重试 3 次（每次 60 秒）。

补跑（catchup）
--------------
``data/market_stats.json`` 的最新交易日落后于「eltdx 今日日线」时，说明某次收盘重建
没能落盘（历史上 15:00 那次被外部中断过）。此时先按 close 阶段补跑一次
``auto_task.py --mode close``：

* ``--catchup``：总是判断一次，落后就补，不落后秒级退出 0；
* ``--auto`` 在 open / close 阶段开头也做同样判断（幂等，正常无落后时几乎零开销）。

用法::

    .venv\\Scripts\\python.exe tools\\panel_refresh.py --auto        # 计划任务用
    .venv\\Scripts\\python.exe tools\\panel_refresh.py --phase close  # 手工指定阶段
    .venv\\Scripts\\python.exe tools\\panel_refresh.py --catchup      # 只补落后数据
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
CN = timezone(timedelta(hours=8), "Asia/Shanghai")
# 交易日重建的落盘结果：它落后就是「收盘数据没补上」的判据。
MARKET_STATS = ROOT / "data" / "market_stats.json"

# (时:分, 阶段名) —— 顺序即时间顺序
PHASES: list[tuple[tuple[int, int], str]] = [
    ((9, 25), "open"),
    ((11, 30), "noon"),
    ((13, 0), "pm"),
    ((15, 0), "close"),
]


def resolve_phase(now: datetime) -> str:
    """自动模式：取「已经过的最近一个时点」的阶段；早于 09:25 时返回最小阶段。"""
    minutes = now.hour * 60 + now.minute
    chosen = PHASES[0][1]
    for (hh, mm), name in PHASES:
        if minutes >= hh * 60 + mm:
            chosen = name
    return chosen


def run_step(label: str, argv: list[str]) -> tuple[bool, int]:
    print(f"--- step={label} cmd={' '.join(argv)}", flush=True)
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    for stream, text in (("out", proc.stdout), ("err", proc.stderr)):
        for line in (text or "").splitlines():
            print(f"[{label}:{stream}] {line}", flush=True)
    print(f"--- step={label} exit={proc.returncode}", flush=True)
    return proc.returncode == 0, proc.returncode


def today_bar_ready(day: int, retries: int = 3, wait_seconds: int = 60) -> bool:
    """只读探测 eltdx 是否已返回当天日线（收盘重建的前提）。"""
    code = "sh600519"
    script = (
        "import sys;sys.path.insert(0, r'%s');"
        "from eltdx import TdxClient;"
        "c=TdxClient(timeout=10);r=c.bars.get(['%s'],period='day',start=0,count=2,batch_size=1) or {};"
        "s=r.get('%s');"
        "days=[int(b.time.strftime('%%Y%%m%%d')) for b in (s.bars if s else [])];"
        "print('bar_days',days);"
        "print('ready',%d in days);"
        "c.__exit__(None,None,None)"
    ) % (str(ROOT), code, code, day)
    for attempt in range(1, retries + 1):
        proc = subprocess.run(
            [str(PYTHON), "-c", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        ready = "ready True" in (proc.stdout or "")
        print(f"--- close_bar_probe attempt={attempt} ready={ready} {(proc.stdout or '').strip()}", flush=True)
        if ready:
            return True
        if attempt < retries:
            time.sleep(wait_seconds)
    return False


def latest_stats_day() -> int | None:
    """``data/market_stats.json`` 里最新的交易日（YYYYMMDD 整数）；读不到返回 None。"""
    try:
        raw = json.loads(MARKET_STATS.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 缺文件/半截 JSON 都按「没有落盘记录」处理
        return None
    if not isinstance(raw, dict):
        return None
    days = [int(key) for key in raw if isinstance(key, str) and len(key) == 8 and key.isdigit()]
    return max(days) if days else None


def catchup_needed(now: datetime) -> bool:
    """是否需要补跑收盘重建：market_stats 最新交易日落后于 eltdx 今日日线。

    ``today_bar_ready`` 用 ``retries=1`` 的轻量调用（不重试、不等待）：
    补跑判断只是「加分项」，绝不能为了它让计划任务长时间阻塞。
    """
    day = int(now.strftime("%Y%m%d"))
    stats_day = latest_stats_day()
    if stats_day is not None and stats_day >= day:
        return False
    return today_bar_ready(day, retries=1)


def run_catchup(now: datetime) -> tuple[bool, int] | None:
    """落后时按 close 阶段补跑一次 ``auto_task.py --mode close``。

    返回 run_step 的 (是否成功, 退出码)；不需要补跑时返回 None（调用方据此决定是否秒退）。
    """
    day = int(now.strftime("%Y%m%d"))
    stats_day = latest_stats_day()
    if stats_day is not None and stats_day >= day:
        print(f"--- catchup 跳过：market_stats 最新={stats_day} 已到今日 {day}", flush=True)
        return None
    if not today_bar_ready(day, retries=1):
        print(f"--- catchup 跳过：eltdx 尚无 {day} 日线（market_stats 最新={stats_day}）", flush=True)
        return None
    print(f"--- catchup 补跑：market_stats 最新={stats_day} < 今日 {day}，先按 close 阶段重建", flush=True)
    return run_step(
        "catchup-report-close",
        [str(PYTHON), str(ROOT / "tools" / "auto_task.py"), "--mode", "close"],
    )


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="A 股面板四个时点刷新")
    parser.add_argument("--auto", action="store_true", help="按当前时间判定阶段（计划任务用）")
    parser.add_argument("--phase", choices=[name for _, name in PHASES], help="手工指定阶段")
    parser.add_argument("--skip-report", action="store_true", help="只刷新 cycle 数据，跳过报告重建")
    parser.add_argument("--no-retry", action="store_true", help="close 阶段不做日线探测重试")
    parser.add_argument("--catchup", action="store_true", help="只补落下的收盘数据（不需要补时秒级退出）")
    parser.add_argument("--skip-intraday", action="store_true", help="跳过盘中快照（排查用）")
    args = parser.parse_args(argv)

    now = datetime.now(CN)
    phase = args.phase or (resolve_phase(now) if args.auto else resolve_phase(now))
    print(f"== panel_refresh phase={phase} at={now.isoformat(timespec='seconds')} auto={args.auto}", flush=True)

    results: list[tuple[str, bool, int]] = []

    # 补跑：--catchup 总是判断；--auto 只在 open / close 阶段判断，让「15:00 那次死了」
    # 也能在次日 09:25 或当天 15:30 兜底时被自动补上。正常无落后时只有一次只读探测。
    catchup_done = False
    if args.catchup or (args.auto and phase in ("open", "close")):
        outcome = run_catchup(now)
        if outcome is None:
            if args.catchup:
                # 不落后（或今日日线还没出）：立即退出 0，不做全量重建。
                print("== panel_refresh catchup 无需补跑，退出", flush=True)
                return 0
        else:
            results.append(("catchup-report-close", outcome[0], outcome[1]))
            catchup_done = True

    # 顺序很关键：cycle_collect 的基准日取自 market_stats 的最新交易日（longtou/cycle_source.py），
    # 而 market_stats 由 auto_task 重建。close 阶段若先采集后重建，当天收盘的 cycle 仍会停在上一交易日。
    if phase in ("open", "close") and not args.skip_report:
        if phase == "close" and not args.no_retry:
            day = int(now.strftime("%Y%m%d"))
            if not today_bar_ready(day):
                print(f"warning: eltdx 未返回 {day} 日线，仍按现有数据重建（报告可能停在上一交易日）", flush=True)
        mode = "auction" if phase == "open" else "close"
        if catchup_done and mode == "close":
            # catchup 刚跑过同一条命令，别在一分钟内重复全量重建。
            print("--- report-close 已在 catchup 阶段跑过，跳过重复重建", flush=True)
        else:
            ok, code = run_step(f"report-{mode}", [str(PYTHON), str(ROOT / "tools" / "auto_task.py"), "--mode", mode])
            results.append((f"report-{mode}", ok, code))

    ok, code = run_step("cycle", [str(PYTHON), str(ROOT / "tools" / "cycle_collect.py")])
    results.append(("cycle", ok, code))

    # 盘中快照：cycle 的基准日取自 market_stats 最大交易日，盘中只有 T-1，所以上面这次
    # cycle_collect 拿不到当天行。open/noon/pm 在它之后用东财实时数据算当天值，把「当日行」
    # 并入 cycle/latest.json（intraday 标记 + asof）；close 阶段正式行已重建，只兜底清标记。
    # 官方行优先：若当日行已是正式值（无 intraday 标记），快照模块会跳过、不覆盖。
    if not args.skip_intraday:
        snap_phase = "close" if phase == "close" else phase
        snap_argv = [str(PYTHON), str(ROOT / "tools" / "intraday_snapshot.py"), "--phase", snap_phase]
        if phase == "close":
            snap_argv.append("--strip")
        ok, code = run_step("intraday", snap_argv)
        results.append(("intraday", ok, code))

    failed = [name for name, ok, _ in results if not ok]
    print(f"== panel_refresh done phase={phase} results={[(n, c) for n, _, c in results]} failed={failed}", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
