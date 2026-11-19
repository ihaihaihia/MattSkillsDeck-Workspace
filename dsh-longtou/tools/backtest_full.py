"""Full-gate sample backtest: engine candidate + final grade.

This reuses the actual ``build_candidate`` path (auction, tiered gate, turnover,
leader state, downgrades, limit-down proxy) and then measures 9:30-open returns.

Market-wide high-board / one-word limit-down counts are not in the saved
``每日情绪梯队.json`` yet; the limit-down gate therefore uses the limit-down
count proxy and marks that limitation in the output.
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
from longtou.engine import build_candidate, leader_state  # noqa: E402
from longtou.market import MarketDay  # noqa: E402
from longtou.names import name_for  # noqa: E402
from longtou.shares import EquitySource  # noqa: E402
from longtou.tdx import (  # noqa: E402
    TDX_VIPDOC,
    board_events,
    date_int_to_str,
    limit_pct,
    limit_price,
    list_stocks,
    next_bar,
    read_day_file,
)

LADDER_PATH = ROOT / "data" / "market_ladder.json"
STATS_PATH = ROOT / "data" / "market_stats.json"


@dataclass
class TradeRow:
    stage: int
    year: int
    code: str
    name: str
    event_date: int
    next_date: int
    final_grade: str
    action: str
    score: float
    auction_pass: bool
    auction_ratio_max: float
    gate_pct: float
    turnover_state: str
    cap_tier: str
    higher_leader: bool
    rotation_window: bool
    one_word: bool
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


def market_day(stats: dict, ladder: dict, date: int) -> MarketDay | None:
    row = stats.get(str(date))
    if row is not None:
        return MarketDay(
            date=date,
            limit_up=int(row.get("limit_up", 0)),
            limit_down=int(row.get("limit_down", 0)),
            touched_limit=int(row.get("touched_limit", 0)),
            broken_limit=int(row.get("broken_limit", 0)),
            one_word_limit_down=int(row.get("one_word_limit_down", 0)),
            high_board_limit_down=int(row.get("high_board_limit_down", 0)),
            consecutive_limit_down=int(row.get("consecutive_limit_down", 0)),
            max_height=int(row.get("max_height", 0)),
            leader_codes=list(row.get("leader_codes", [])),
            height_counts={int(key): int(value) for key, value in row.get("height_counts", {}).items()},
            one_word_ratio_by_code={
                str(key): float(value) for key, value in row.get("one_word_ratio_by_code", {}).items()
            },
        )
    fallback = ladder.get(str(date))
    if fallback is None:
        return None
    return MarketDay(
        date=date,
        limit_up=int(fallback.get("limit_up", 0)),
        limit_down=int(fallback.get("limit_down", 0)),
        max_height=int(fallback.get("max_height", 0)),
        leader_codes=list(fallback.get("dragon_codes", [])),
        height_counts={int(key): int(value) for key, value in fallback.get("height_count", {}).items()},
    )


def run(stage: int, per_year: int, seed: int, start: int, end: int) -> dict:
    ladder = json.loads(LADDER_PATH.read_text(encoding="utf-8"))
    stats = json.loads(STATS_PATH.read_text(encoding="utf-8")) if STATS_PATH.exists() else {}
    stocks = list_stocks(TDX_VIPDOC)
    stock_map = {stock.code: stock for stock in stocks}
    by_year: dict[int, list] = defaultdict(list)
    for stock in stocks:
        try:
            bars = read_day_file(stock.path)
        except Exception:
            continue
        if len(bars) < 30:
            continue
        for event in board_events(stock, bars, start_date=start):
            if event.height != stage or event.bar.date > end:
                continue
            nxt = next_bar(bars, event.index)
            if nxt is None:
                continue
            by_year[event.bar.date // 10000].append((stock, bars, event, nxt))

    rng = random.Random(seed)
    sample = []
    for year in sorted(by_year):
        rows = by_year[year]
        sample.extend(rng.sample(rows, min(per_year, len(rows))))
    sample.sort(key=lambda item: (item[2].bar.date, item[0].code))

    results: list[TradeRow] = []
    skipped = defaultdict(int)
    leader_cache: dict[int, object] = {}
    with AuctionSource(timeout=6.0) as auction_source, EquitySource(timeout=6.0) as equity_source:
        for number, (stock, bars, event, nxt) in enumerate(sample, 1):
            market = market_day(stats, ladder, event.bar.date)
            if market is None:
                skipped["no_market"] += 1
                continue
            try:
                if event.bar.date not in leader_cache:
                    leader_cache[event.bar.date] = leader_state(market, stock_map, event.bar.date)
                leader = leader_cache[event.bar.date]
                result = build_candidate(
                    stock,
                    stage,
                    event.bar.date,
                    market,
                    leader,
                    auction_source,
                    equity_source,
                    name_for(stock.code),
                )
            except Exception as exc:  # keep the sample running on a single bad event
                skipped[f"error:{type(exc).__name__}"] += 1
                continue
            if result.get("error"):
                skipped[result["error"]] += 1
                continue

            entry_limit = limit_price(event.bar.close, limit_pct(stock.code))
            buyable = nxt.open < entry_limit - 0.005 and not result["one_word"]
            row = TradeRow(
                stage=stage,
                year=event.bar.date // 10000,
                code=stock.code,
                name=result.get("name", ""),
                event_date=event.bar.date,
                next_date=nxt.date,
                final_grade=result["final_grade"],
                action=result["action"],
                score=result["score"],
                auction_pass=result["auction_pass"],
                auction_ratio_max=result["auction_ratio_max"],
                gate_pct=result["auction_gate"],
                turnover_state=result["turnover_state"],
                cap_tier=result["cap_tier"],
                higher_leader=result["higher_leader"],
                rotation_window=result["rotation_window"],
                one_word=result["one_word"],
                buyable=buyable,
            )
            if buyable:
                for horizon in (1, 2, 3, 5):
                    if event.index + horizon < len(bars):
                        setattr(
                            row,
                            f"ret_{horizon}",
                            round((bars[event.index + horizon].close / nxt.open - 1.0) * 100, 2),
                        )
            results.append(row)
            print(
                f"[{number}/{len(sample)}] {stock.code} {date_int_to_str(event.bar.date)} "
                f"{row.final_grade}/{row.action} auction {'PASS' if row.auction_pass else 'FAIL'} "
                f"{row.auction_ratio_max:.2f}% gate {row.gate_pct:.2f}%",
                flush=True,
            )

    def group(label: str, predicate) -> None:
        rows = [row for row in results if predicate(row)]
        if not rows:
            return
        print(f"\n## {label} n={len(rows)}")
        for h in (1, 2, 3, 5):
            values = [getattr(row, f"ret_{h}") for row in rows if getattr(row, f"ret_{h}") is not None]
            print(h, summarize(values))

    print("\n## Groups")
    group("all", lambda row: True)
    group("buyable", lambda row: row.buyable)
    group("action=可研究", lambda row: row.buyable and row.action == "可研究")
    group("grade A/B", lambda row: row.buyable and row.final_grade in ("A", "B"))
    group("grade C", lambda row: row.buyable and row.final_grade == "C")
    group("auction PASS", lambda row: row.buyable and row.auction_pass)
    group("auction FAIL", lambda row: row.buyable and not row.auction_pass)
    group("turnover healthy", lambda row: row.buyable and row.turnover_state == "健康区")
    group("turnover death", lambda row: row.buyable and row.turnover_state == "死亡换手")

    output = {
        "meta": {
            "stage": stage,
            "per_year": per_year,
            "seed": seed,
            "start": start,
            "end": end,
            "sample_events": len(sample),
            "rows": len(results),
            "skipped": dict(skipped),
            "limit_down_gate": "full market stats" if stats else "limit-down count proxy",
        },
        "groups": {
            "all": summarize([row.ret_1 for row in results if row.ret_1 is not None]),
        },
        "results": [asdict(row) for row in results],
    }
    return output


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Full-gate sample backtest")
    parser.add_argument("--stage", type=int, default=3, choices=(2, 3, 4))
    parser.add_argument("--per-year", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20261018)
    parser.add_argument("--start", type=int, default=20210802)
    parser.add_argument("--end", type=int, default=20260831)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    result = run(args.stage, args.per_year, args.seed, args.start, args.end)
    out = args.out or ROOT / "output" / f"full_backtest_stage{args.stage}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsaved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
