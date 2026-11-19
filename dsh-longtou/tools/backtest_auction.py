"""Auction-gate backtest for 2→3 / 3→4 / 4→5 events.

This first backtest isolates the factor the strategy weights most: the tiered
09:25 auction amount / 20-day adjusted max amount gate. It deliberately does
not yet apply the leader-state downgrades or the limit-down gate; those are
added after the auction factor has a baseline.

Entry: 09:30 open on the next trading day (never the 09:25 price).
Exit: close after 1/2/3/5 trading days.
One-word T-1 boards and limit-up opens are not buyable and are skipped.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longtou.auction import AuctionSource  # noqa: E402
from longtou.rules import (  # noqa: E402
    adjusted_twenty_day_max,
    cap_tier,
    turnover_state,
)
from longtou.shares import EquitySource  # noqa: E402
from longtou.tdx import (  # noqa: E402
    TDX_VIPDOC,
    Bar,
    StockFile,
    board_events,
    date_int_to_str,
    limit_pct,
    limit_price,
    list_stocks,
    next_bar,
    read_day_file,
)


@dataclass
class EventRow:
    stage: int
    year: int
    code: str
    name: str
    event_date: int
    next_date: int
    cap_tier: str
    float_mcap_yi: float
    turnover_pct: float
    turnover_state: str
    max20_amount: float
    auction_amount: float
    auction_ratio_max: float
    gate_pct: float
    auction_pass: bool
    open_gap_pct: float
    entry_open: float
    one_word_t1: bool
    buyable: bool
    ret_1: float | None = None
    ret_2: float | None = None
    ret_3: float | None = None
    ret_5: float | None = None


def summarize(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "avg": None, "median": None, "win": None}
    wins = sum(1 for value in values if value > 0)
    return {
        "n": len(values),
        "avg": round(statistics.mean(values), 2),
        "median": round(statistics.median(values), 2),
        "win": round(wins / len(values) * 100, 1),
    }


def collect_events(stock: StockFile, bars: list[Bar], stage: int, start: int, end: int) -> list:
    rows = []
    for event in board_events(stock, bars, start_date=start):
        if event.height != stage or event.bar.date > end:
            continue
        nxt = next_bar(bars, event.index)
        if nxt is None:
            continue
        rows.append((event, nxt))
    return rows


def run(stage: int, per_year: int, seed: int, start: int, end: int) -> dict:
    stocks = list_stocks(TDX_VIPDOC)
    by_year: dict[int, list] = defaultdict(list)
    for stock in stocks:
        try:
            bars = read_day_file(stock.path)
        except Exception:
            continue
        if len(bars) < 30:
            continue
        for event, nxt in collect_events(stock, bars, stage, start, end):
            by_year[event.bar.date // 10000].append((stock, bars, event, nxt))

    rng = random.Random(seed)
    sample = []
    for year in sorted(by_year):
        rows = by_year[year]
        sample.extend(rng.sample(rows, min(per_year, len(rows))))
    sample.sort(key=lambda item: (item[2].bar.date, item[0].code))

    results: list[EventRow] = []
    skipped = defaultdict(int)
    with AuctionSource(timeout=6.0) as auction_source, EquitySource(timeout=6.0) as equity_source:
        for number, (stock, bars, event, nxt) in enumerate(sample, 1):
            bar = event.bar
            one_word = bar.low >= event.limit_price - 0.005
            if one_word:
                skipped["one_word_t1"] += 1
                continue
            capital = equity_source.circulating_shares(stock.code, bar.date)
            if capital is None:
                skipped["no_shares"] += 1
                continue
            turnover = bar.volume / capital.circulating_shares * 100.0
            float_mcap = capital.circulating_shares * bar.close / 1e8
            tier = cap_tier(float_mcap)
            max20 = adjusted_twenty_day_max(bars, event.index, stock.code)
            if max20.amount <= 0:
                skipped["no_max20"] += 1
                continue
            try:
                auction = auction_source.fetch(stock.code, date_int_to_str(nxt.date))
            except Exception:
                auction = None
            if auction is None:
                skipped["no_auction"] += 1
                continue
            ratio = auction.amount_yuan / max20.amount * 100.0 if max20.amount > 0 else 0.0
            gate = float(tier.auction_low)
            open_gap = (auction.price / bar.close - 1.0) * 100.0
            entry_limit = limit_price(bar.close, limit_pct(stock.code))
            buyable = nxt.open < entry_limit - 0.005
            if not buyable:
                skipped["limit_up_open"] += 1
            row = EventRow(
                stage=stage,
                year=bar.date // 10000,
                code=stock.code,
                name="",
                event_date=bar.date,
                next_date=nxt.date,
                cap_tier=tier.name,
                float_mcap_yi=round(float_mcap, 2),
                turnover_pct=round(turnover, 2),
                turnover_state=turnover_state(turnover, tier),
                max20_amount=round(max20.amount, 2),
                auction_amount=round(auction.amount_yuan, 2),
                auction_ratio_max=round(ratio, 2),
                gate_pct=round(gate, 2),
                auction_pass=ratio >= gate,
                open_gap_pct=round(open_gap, 2),
                entry_open=round(nxt.open, 2),
                one_word_t1=False,
                buyable=buyable,
            )
            idx = event.index
            for horizon in (1, 2, 3, 5):
                if idx + horizon < len(bars) and buyable:
                    ret = bars[idx + horizon].close / nxt.open - 1.0
                    setattr(row, f"ret_{horizon}", round(ret * 100, 2))
            results.append(row)
            print(
                f"[{number}/{len(sample)}] {stock.code} {date_int_to_str(bar.date)} "
                f"{'PASS' if row.auction_pass else 'FAIL'} {row.auction_ratio_max:.2f}% "
                f"gate {row.gate_pct:.2f}%",
                flush=True,
            )

    groups: dict[str, dict] = {}
    for label, predicate in (
        ("all_buyable", lambda r: r.buyable),
        ("auction_pass", lambda r: r.buyable and r.auction_pass),
        ("auction_fail", lambda r: r.buyable and not r.auction_pass),
        ("turnover_healthy", lambda r: r.buyable and r.turnover_state == "健康区"),
        ("turnover_gray", lambda r: r.buyable and r.turnover_state == "灰色区"),
        ("turnover_death", lambda r: r.buyable and r.turnover_state == "死亡换手"),
    ):
        rows = [row for row in results if predicate(row)]
        group = {"n": len(rows)}
        for h in (1, 2, 3, 5):
            group[f"ret_{h}"] = summarize(
                [getattr(row, f"ret_{h}") for row in rows if getattr(row, f"ret_{h}") is not None]
            )
        groups[label] = group

    by_year_out: dict[str, dict] = {}
    for year in sorted({row.year for row in results}):
        rows = [row for row in results if row.year == year]
        by_year_out[str(year)] = {
            "n": len(rows),
            "pass_n": sum(1 for row in rows if row.auction_pass),
            **{
                f"pass_ret_{h}": summarize(
                    [getattr(row, f"ret_{h}") for row in rows if row.auction_pass and getattr(row, f"ret_{h}") is not None]
                )
                for h in (1, 3)
            },
        }

    return {
        "meta": {
            "stage": stage,
            "per_year": per_year,
            "seed": seed,
            "start": start,
            "end": end,
            "sample_events": len(sample),
            "rows": len(results),
            "skipped": dict(skipped),
        },
        "groups": groups,
        "by_year": by_year_out,
        "results": [asdict(row) for row in results],
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Auction-gate backtest")
    parser.add_argument("--stage", type=int, default=3, choices=(2, 3, 4))
    parser.add_argument("--per-year", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20261018)
    parser.add_argument("--start", type=int, default=20210802)
    parser.add_argument("--end", type=int, default=20260831)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    result = run(args.stage, args.per_year, args.seed, args.start, args.end)
    out = args.out or ROOT / "output" / f"auction_backtest_stage{args.stage}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n## Summary", json.dumps(result["meta"], ensure_ascii=False))
    for label, group in result["groups"].items():
        if not group["n"]:
            continue
        parts = [f"{label}: n={group['n']}"]
        for h in (1, 2, 3, 5):
            s = group[f"ret_{h}"]
            parts.append(f"{h}d {s['avg']}%/{s['win']}%")
        print(" | ".join(parts))
    print(f"saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
