"""History report storage and combined latest.md for the DSH panel.

Each report is stored under ``output/reports/<trade_date>.md``.  ``latest.md`` is
rebuilt as a sequence of marker-delimited reports so the panel can switch dates
with previous/next buttons without a host route restart.
"""

from __future__ import annotations

import json
from pathlib import Path

from .cli import render_markdown
from .engine import build_report
from .tdx import date_int_to_str

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "output" / "reports"
LATEST = ROOT / "output" / "latest.md"
STATS = ROOT / "data" / "market_stats.json"


def trade_calendar() -> list[int]:
    try:
        data = json.loads(STATS.read_text(encoding="utf-8"))
    except Exception:
        return []
    return sorted(int(key) for key in data)


def save_report(trade_date: int, markdown: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{trade_date}.md"
    path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    rebuild_latest()
    return path


def rebuild_latest() -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    parts: list[str] = []
    for path in sorted(REPORT_DIR.glob("*.md"), key=lambda item: item.stem):
        if not path.stem.isdigit():
            continue
        body = path.read_text(encoding="utf-8").strip()
        parts.append(f"<!-- report:{path.stem} -->\n{body}\n<!-- /report -->")
    LATEST.parent.mkdir(parents=True, exist_ok=True)
    LATEST.write_text("\n\n".join(parts) + ("\n" if parts else ""), encoding="utf-8")
    return LATEST


def build_one(trade_date: int, force: bool = False) -> str:
    path = REPORT_DIR / f"{trade_date}.md"
    if path.exists() and not force:
        return path.read_text(encoding="utf-8")
    calendar = trade_calendar()
    if trade_date not in calendar:
        raise ValueError(f"{trade_date} is not in the market stats calendar")
    index = calendar.index(trade_date)
    if index == 0:
        raise ValueError("no previous trading day for the first calendar date")
    target = calendar[index - 1]
    report = build_report(
        target,
        [2, 3, 4],
        next_date_override=trade_date,
        trade_date=trade_date,
        display_date=trade_date,
    )
    report["next_date_text"] = date_int_to_str(trade_date)
    markdown = render_markdown(report)
    save_report(trade_date, markdown)
    return markdown
