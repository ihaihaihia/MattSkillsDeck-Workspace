#!/usr/bin/env python3
"""Audit historical 09:25 coverage from eltdx.

This is deliberately a sample audit, not a full-market download. It finds
N-board events in local TDX daily files, samples across years, then asks
``eltdx`` for the next trading day's official 09:25 opening match.

Usage:
    python tools/audit_auction_coverage.py --stage 3 --per-year 10
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longtou.auction import AuctionSource  # noqa: E402
from longtou.tdx import (  # noqa: E402
    TDX_VIPDOC,
    board_events,
    date_int_to_str,
    list_stocks,
    next_bar,
    read_day_file,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit eltdx 09:25 historical coverage")
    parser.add_argument("--stage", type=int, default=3, choices=(2, 3, 4))
    parser.add_argument("--per-year", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20261018)
    parser.add_argument("--start", type=int, default=20210802)
    parser.add_argument("--end", type=int, default=20260831)
    parser.add_argument("--limit", type=int, default=0, help="optional cap after sampling")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "output" / "auction_coverage.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stocks = list_stocks(TDX_VIPDOC)
    print(f"TDX stocks: {len(stocks)}", flush=True)

    by_year: dict[int, list[dict]] = defaultdict(list)
    scanned = 0
    for stock in stocks:
        try:
            bars = read_day_file(stock.path)
        except Exception as exc:
            print(f"read failed {stock.full_code}: {exc}", file=sys.stderr)
            continue
        if len(bars) < 30:
            continue
        scanned += 1
        for event in board_events(stock, bars, start_date=args.start):
            if event.height != args.stage or event.bar.date > args.end:
                continue
            nxt = next_bar(bars, event.index)
            if nxt is None:
                continue
            by_year[event.bar.date // 10000].append(
                {
                    "code": stock.code,
                    "market": stock.market,
                    "event_date": event.bar.date,
                    "next_date": nxt.date,
                    "height": event.height,
                    "one_word": event.is_one_word,
                    "event_amount": event.bar.amount,
                }
            )

    print(f"scanned with data: {scanned}; years: {sorted(by_year)}", flush=True)
    for year in sorted(by_year):
        print(f"  {year}: {len(by_year[year])} events", flush=True)

    rng = random.Random(args.seed)
    sample: list[dict] = []
    for year in sorted(by_year):
        rows = by_year[year]
        take = min(args.per_year, len(rows))
        sample.extend(rng.sample(rows, take))
    sample.sort(key=lambda row: (row["next_date"], row["code"]))
    if args.limit > 0:
        sample = sample[: args.limit]

    print(f"sample: {len(sample)} events", flush=True)
    results: list[dict] = []
    started = time.perf_counter()
    with AuctionSource(timeout=5.0) as source:
        for number, row in enumerate(sample, 1):
            date_text = date_int_to_str(row["next_date"])
            status = "ok"
            error = None
            auction = None
            try:
                auction = source.fetch(row["code"], date_text)
                if auction is None:
                    status = "none"
            except Exception as exc:  # noqa: BLE001 - audit must keep going
                status = "error"
                error = f"{type(exc).__name__}: {exc}"
            results.append(
                {
                    **row,
                    "next_date_text": date_text,
                    "status": status,
                    "error": error,
                    "auction_price": None if auction is None else auction.price,
                    "auction_lots": None if auction is None else auction.volume_lots,
                    "auction_amount": None if auction is None else auction.amount_yuan,
                }
            )
            print(
                f"[{number}/{len(sample)}] {row['market']}{row['code']} {date_text} "
                f"{status}"
                + (f" price={auction.price} lots={auction.volume_lots}" if auction else ""),
                flush=True,
            )

    elapsed = time.perf_counter() - started
    summary: dict[str, dict[str, int]] = {}
    for year in sorted({row["next_date"] // 10000 for row in results}):
        rows = [row for row in results if row["next_date"] // 10000 == year]
        counts = defaultdict(int)
        for row in rows:
            counts[row["status"]] += 1
        summary[str(year)] = {key: counts[key] for key in ("ok", "none", "error")}
        total = sum(summary[str(year)].values())
        ok = summary[str(year)]["ok"]
        print(f"coverage {year}: {ok}/{total} = {ok / total * 100:.1f}%", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "meta": {
                    "stage": args.stage,
                    "per_year": args.per_year,
                    "seed": args.seed,
                    "sample_size": len(results),
                    "elapsed_seconds": round(elapsed, 1),
                    "start": args.start,
                    "end": args.end,
                },
                "summary": summary,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"saved: {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
