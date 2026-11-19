#!/usr/bin/env python3
"""Scheduled A-share data/report refresh for the dsh-longtou panel.

Modes
-----
auction (09:25):
    Build today's pre-open report from the previous trading day's close and
    today's 09:25 auction, then rebuild output/latest.md history.

close (15:30):
    Pull today's daily bars via eltdx, rebuild the one-pass market stats cache,
    and re-render today's report with the close market/leader state (including
    the "highest board broken day 1/2" state).

The script checks the TDX trading calendar, so a weekday holiday exits cleanly.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eltdx import TdxClient  # noqa: E402

from longtou.cli import render_markdown  # noqa: E402
from longtou.engine import build_report  # noqa: E402
from longtou.history import save_report  # noqa: E402
from longtou.tdx import date_int_to_str  # noqa: E402

CN = timezone(timedelta(hours=8), "Asia/Shanghai")


def calendar_info(day: date) -> tuple[bool, date | None]:
    with TdxClient(timeout=10) as client:
        workdays = client.workdays
        is_open = bool(workdays.is_workday(day))
        previous = workdays.previous_workday(day, include_self=False) if is_open else None
        return is_open, previous


def _refresh_overlay() -> str | None:
    """刷新 eltdx 日线 overlay；失败只打印告警并返回失败描述，不中断后续步骤。"""
    command = [sys.executable, str(ROOT / "tools" / "update_daily_overlay.py")]
    print("running", " ".join(command), flush=True)
    try:
        subprocess.run(command, cwd=ROOT, check=True)
    except subprocess.CalledProcessError as exc:
        print(
            f"warning: update_daily_overlay.py failed, continuing with existing overlay (exit {exc.returncode})",
            file=sys.stderr,
        )
        return f"update_daily_overlay.py: exit {exc.returncode}"
    except OSError as exc:
        print(
            f"warning: update_daily_overlay.py not started, continuing with existing overlay ({exc})",
            file=sys.stderr,
        )
        return f"update_daily_overlay.py: {type(exc).__name__}"
    return None


def run_auction(day: date, previous: date) -> int:
    # 09:25 补一次刷新：若前一日 15:00 刷新失败，这里可以自愈分母数据。
    _refresh_overlay()
    target = int(previous.strftime("%Y%m%d"))
    trade = int(day.strftime("%Y%m%d"))
    report = build_report(target, [2, 3, 4], next_date_override=trade, with_lhb=True)
    report["next_date_text"] = date_int_to_str(trade)
    markdown = render_markdown(report)
    save_report(trade, markdown)
    candidates = [row for row in report.get("candidates", []) if not row.get("error")]
    counts: dict[str, int] = {}
    for row in candidates:
        counts[row.get("final_grade", "?")] = counts.get(row.get("final_grade", "?"), 0) + 1
    print(
        f"auction_ok trade={trade} target={target} candidates={len(candidates)} "
        f"grades={json.dumps(counts, ensure_ascii=False)}"
    )
    return 0


def run_close(day: date) -> int:
    failures: list[str] = []
    overlay_failure = _refresh_overlay()
    if overlay_failure:
        failures.append(overlay_failure)
    commands = [
        [sys.executable, str(ROOT / "tools" / "build_market_stats.py")],
        [sys.executable, str(ROOT / "tools" / "build_suspended_stats.py"), "--days", "80"],
        [sys.executable, str(ROOT / "tools" / "build_ladder_cache.py"), "--days", "40"],
    ]
    for command in commands:
        print("running", " ".join(command), flush=True)
        try:
            subprocess.run(command, cwd=ROOT, check=True)
        except subprocess.CalledProcessError as exc:
            name = Path(command[1]).name if len(command) > 1 else "command"
            failures.append(f"{name}: exit {exc.returncode}")
            print(f"warning: {name} failed, continuing with existing cache", file=sys.stderr)
    is_open, previous = calendar_info(day)
    close_report = ""
    if is_open and previous is not None:
        try:
            trade = int(day.strftime("%Y%m%d"))
            report = build_report(
                int(previous.strftime("%Y%m%d")),
                [2, 3, 4],
                next_date_override=trade,
                trade_date=trade,
                display_date=trade,
                with_lhb=True,
            )
            report["next_date_text"] = date_int_to_str(trade)
            save_report(trade, render_markdown(report))
            close_report = "rebuilt"
            print(f"close_report trade={trade} target={previous} status=rebuilt", flush=True)
        except Exception as exc:
            print(f"close_report failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            close_report = f"failed: {type(exc).__name__}: {exc}"

    status = {
        "date": int(day.strftime("%Y%m%d")),
        "updated_at": datetime.now(CN).isoformat(timespec="seconds"),
        "mode": "close",
        "report": close_report,
        "failures": failures,
    }
    out = ROOT / "output" / "close_status.json"
    out.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"close_ok date={status['date']} status={out}")
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("auction", "close"), required=True)
    parser.add_argument("--date", type=int, default=0, help="optional test date YYYYMMDD")
    args = parser.parse_args()

    day = (
        datetime.strptime(str(args.date), "%Y%m%d").date()
        if args.date
        else datetime.now(CN).date()
    )
    is_open, previous = calendar_info(day)
    if not is_open:
        print(f"not trading day date={day.isoformat()} mode={args.mode}")
        return 0
    if args.mode == "auction":
        if previous is None:
            print(f"no previous trading day for {day.isoformat()}", file=sys.stderr)
            return 1
        return run_auction(day, previous)
    return run_close(day)


if __name__ == "__main__":
    raise SystemExit(main())
