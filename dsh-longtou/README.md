# dsh-longtou

A-share **2→3 / 3→4 / 4→5** leading-stock research plugin for DeepSeek Harness (DSH).

This repository is the implementation of the v0.5 rule set. It is split into a
Python research engine plus a thin DSH host tool.

## v0.5 rules (baseline)

- Universe: Shanghai/Shenzhen A-shares; exclude ST, Beijing Stock Exchange, and stocks listed
  for fewer than 60 trading days.
- Stages: exactly 2, 3, or 4 consecutive limit-ups on T-1.
- Auction data: **09:25 official opening match** from `eltdx` (TDX 7709 protocol), historical
  and live. It is not the 09:25 virtual auction snapshot and not the 09:31 first minute.
- Execution: decision at 09:25, fill at **09:30 open**; one-word boards are observation-only.
- Float-cap tiers from the supplied table:
  - auction gate: 1–3% / 3–5% / 5–8% / 8–12% / 12–15% of the **20-day maximum daily amount**;
  - healthy turnover: 1–3% / 3–8% / 8–15% / 15–25% / 20–30%;
  - death turnover: 8% / 12% / 25% / 35% / 40%.
- 60-day max-volume rule: the highest board's T-1 must be the **60-trading-day maximum
  amount** and a limit-up board for the "next-day rotation" window.
- Auction denominator: the **adjusted maximum daily amount** over T-1 and the previous
  19 trading days (20 sessions); a 地天板 / deep-water day is weighted at **50%**.
  Rows whose window contains such a day are marked with **†**, and the report explains
  the adjusted max amount/date.
- Highest-space-board suspension: if the highest board is halted and no stock
  trades above its height during the halt, it remains the space board and the
  report marks it as **停牌中**; the halt is not treated as a break. The market
  card shows only the actual space board, not every stock tied at that height.
- Highest-board break: after the space board breaks, the close refresh and the
  next two reports show **最高板已断板第1天/第2天** with the former code/height;
  from the third trading day the new highest board takes over. The close task
  re-renders the current report with the close market/leader state.
- Open-gap grade adjustment: **-1% to +1%** is neutral; **+1% to +4%** upgrades
  one grade; **below -1% or above +4%** downgrades one grade.
- **昨换手%** is the previous session's full-day turnover (volume ÷ float shares),
  not today's auction or intraday turnover; the panel header has a 3-second tooltip.
- **竞额** is the 09:25 auction amount in 万, or in 亿 when it reaches 1 亿元
  (e.g. `2636.6万`, `1.2亿`); clicking the panel header shows the explanation.
- **个股情绪 (10%)**: **独立** when no other consecutive limit-up exists in the same
  concept/industry; **助力N** when N low-position 1st–3rd-board stocks are still
  limit-up in the same concept/industry; more assists rank higher. Concept tags
  from the F10 limit-board ladder (`limit_reason`/`limit_reason_extra`) are tried
  first and the industry (`industry`) is only a fallback, so a broad industry
  does not inflate the count. Clicking the badge lists each assist with the
  stock name, its board level, and the concept, e.g.
  `新经典 1板 出版业` or `中鼎股份 1板 数据中心液冷`.
- **涨幅空间 (not scored)**: remaining close-to-close room before the regulatory
  lines — 100% within 10 trading days and 200% within 30 trading days. The cell is
  `10日空间/30日空间`; hover the header for the rule.
- Leader-state downgrades:
  - higher leader and no rotation window → down one grade;
  - 60-day-volume next day / post-break 1–2 days → no higher-leader downgrade;
  - leader mostly one-word → cap B; suspended leader → no downgrade;
  - leader limit-down break → block;
  - 地天板 next-day auction pass → down one more grade;
  - post-break re-seal → `leader_valid=false` ("反包降级，不配龙头").
- Limit-down gate: intensity score 0–100 from limit-down count, one-word limit-down count,
  high-board limit-down count, and consecutive limit-down count.

## Alpha composition

| Factor | Weight | Meaning |
|---|---:|---|
| 竞额20日比 | 25% | 09:25 auction amount ÷ adjusted 20-day max daily amount |
| 市场情绪 | 20% | limit-down intensity, limit-up/down, rotation window |
| 分歧 | 20% | previous-session turnover state; one-word = 0 |
| 题材/地位 | 10% | placeholder |
| 个股情绪 | 10% | 独立 / 助力N, same-concept low 1–3-board count |
| 事件驱动 | 10% | 新闻 / 大盘 / 热点 classification |
| 开盘% | 5% | -1%~+1% full; +1%~+4% partial; outside = 0 |

Not scored: **涨幅空间** (remaining room to 10-day 100% / 30-day 200%).
Opening gap is also a one-grade adjustment: -1%~+1% neutral; +1%~+4% up one;
outside -1%~+4% down one.

**仓位**: 禁止/不做/观察 = 0; 可研究 by score: 80→80%, 70→60%, 65→50%,
60→40%, 55→30%, 50→20%, below 50→10%. The system gives the position; risk is
left to time.

**事件驱动** uses the F10 limit-board ladder: the stock's concept tag is the
news/theme driver, market breadth/intensity is the 大盘 read, and the
current/previous-day same-concept board count is classified as
主线 / 新题材 / 发酵 / 退潮 / 个股.

## Layout

```text
dsh-longtou/
├─ longtou/
│  ├─ tdx.py                 # TDX .day reader, limit-up/board events
│  ├─ auction.py             # eltdx 09:25 official match adapter
│  ├─ shares.py              # historical circulating shares from TDX corporate data
│  ├─ market.py              # market-wide limit-up/down/ladder snapshot
│  ├─ rules.py               # tiers, 60-day max, turnover states, downgrades
│  ├─ engine.py              # candidate/score assembly
│  ├─ names.py               # local name map + ST filter
│  ├─ history.py             # report files + combined latest.md history
│  └─ cli.py                 # CLI entry point
├─ tools/
│  ├─ dsh_bridge.py          # Python bridge for the DSH host tool
│  ├─ audit_auction_coverage.py
│  ├─ backtest_auction.py
│  ├─ backtest_full.py
│  ├─ build_history.py       # pre-generate panel history
│  ├─ auto_task.py           # 09:25 / 15:30 scheduler entry
│  ├─ panel_server.py        # standalone local panel server (port 8765)
│  ├─ open_panel.ps1         # launch Edge/Chrome app window
│  ├─ build_suspended_stats.py # suspended space-board cache
│  ├─ build_ladder_cache.py  # F10 limit-board industry/concept cache
│  ├─ update_daily_overlay.py # latest daily bars via eltdx (TDX .day is stale)
│  └─ build_market_stats.py  # one-pass full-market stats cache
├─ lib/
│  ├─ index.js               # DSH host tool + latest-report panel routes
│  └─ panel.js               # floating history panel (left-bottom "龙")
├─ 启动独立面板.cmd          # standalone panel launcher
├─ data/                     # stock name map / cache
└─ output/                   # reports and audit results
```

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Audit 09:25 historical coverage (sample, not full market)
.\.venv\Scripts\python.exe tools\audit_auction_coverage.py --stage 3 --per-year 10

# Pull the latest daily bars via eltdx into data/daily_overlay.json
.\.venv\Scripts\python.exe tools\update_daily_overlay.py

# Build the one-pass market stats cache (optional; reports fall back to a scan)
.\.venv\Scripts\python.exe tools\build_market_stats.py

# Pre-generate 40 trading days of panel history
.\.venv\Scripts\python.exe tools\build_history.py --days 40

# Test the scheduled entry points
.\.venv\Scripts\python.exe tools\auto_task.py --mode auction --date 20261015
.\.venv\Scripts\python.exe tools\auto_task.py --mode close --date 20261016

# CLI report for a T-1 close
.\.venv\Scripts\python.exe -m longtou.cli report --date 20260828 --stages 2,3,4

# Standalone panel window (outside the DSH window)
.\启动独立面板.cmd

# Unit tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Standalone panel (outside DSH)

`启动独立面板.cmd` starts a local server at `http://127.0.0.1:8765/` and opens an
Edge/Chrome **app window** with the same `lib/panel.js` and `output/latest.md`.
The window is a separate process, so it can be moved to another monitor, kept
beside another app, or left open when the DSH main window is minimized.

- Data is read directly from `output/latest.md`, not through a DSH web route.
- The panel still refreshes every 60 seconds when open on the latest day.
- The 09:25/15:30 schedules are DSH reminders; the standalone panel can display
  data while DSH is closed, but the scheduled jobs themselves need DSH running.
- Font/layout settings are stored per origin; the standalone window keeps its
  own layout.

## DSH installation (desktop profile)

The plugin is installed as a link package in the desktop profile:

```text
pnpm add link:D:\deepseek-harness\default-workspace\dsh-longtou
```

and `dsh-longtou` is appended to `dsh.profile.bundles`. After a host reload/reopen,
the agent gets a `longtou_report` tool:

```text
longtou_report(date="20260828", stages="2,3,4")
```

The tool runs the Python engine (TDX daily bars + eltdx 09:25 + historical share capital)
and returns a Markdown candidate table with reasons.

## Current status

- [x] Project scaffold and v0.5 rule documentation
- [x] eltdx 09:25 coverage audit: **60/60** sampled 3-board events across 2021–2026 returned data
- [x] TDX daily data + 60-day max-volume and float-cap tiering
- [x] Historical circulating shares from TDX corporate data (no current-snapshot look-ahead)
- [x] 2→3/3→4/4→5 candidate engine, leader-state downgrades, limit-down gate
- [x] Candidate-table CLI and Markdown output
- [x] DSH host tool `longtou_report` installed and verified end-to-end
- [x] Scorecard: 竞额20日比 35 / 市场情绪 25 / 分歧 20 / 题材占位 10 / 个股情绪 10
- [x] Alpha composition modal, event-driven column (news/market/hotspot, click details)
- [x] 昨换手% (previous full-day turnover, tooltip), 开盘% rename, 个股情绪 独立/助力N column
- [x] 涨幅空间 column (not scored): remaining room to 10-day 100% / 30-day 200% regulatory lines
- [x] Auction-only backtest sample (180 three-board events, 2021–2026)
- [x] Floating history panel live: previous/next date buttons, latest button, copy, red/green cards
- [x] Standalone panel window outside DSH (`启动独立面板.cmd`, port 8765, Edge/Chrome app mode);
      desktop shortcut `龙头独立面板.lnk`
- [x] Panel is draggable by its header and has a maximize/restore button; layout persists in localStorage
- [x] The left-bottom "龙" FAB is freely draggable; settings gear sits after the panel title and
      font size ranges 8–24px
- [x] Panel has edge/corner resize handles and a visible bottom-right grip; dragging to the left/right
      edge snaps to a half-screen panel, and dragging the header restores the pre-snap size
- [x] Header/cell alignment in narrow/half-screen mode; minimize button collapses the panel to its header
- [x] Auction column renamed to **竞额20日比%** (09:25 amount ÷ adjusted 20-day max daily amount);
      water-day amounts are weighted at 50%, marked with **†**, and the header has a 3-second tooltip

- [x] Full names from eltdx (5,581 names) filled in all 40 history reports; highest board deep red,
      eligible candidates light red, watch candidates light green
- [x] Candidate tables split into 2→3 / 3→4 / 4→5 with stage headings and spacing
- [x] Stage priority: higher actionable tier downgrades lower tiers one grade; if no tier is
      actionable, all valid candidates move to a sorted 观察区 (4→5, 3→4, 2→3; score desc)
- [x] 40 trading days of pre-generated reports (`output/reports`, 2026-08-14..2026-10-16)
- [x] One-pass `data/market_stats.json` cache (1,260 days) replaces the 17 s per-date scan
- [x] 09:25 and 15:30 A-share weekday schedules; holiday check via TDX workday calendar
- [x] Live 09:25 path with a provisional next-date bar (no duplicate/polluted daily data)
- [ ] Full event-level backtest with leader/limit-down gates (120-event sample done)
- [ ] Former-leader break/suspension edge cases and theme scoring

## Stage priority / all-blocked fallback

- Candidate tables are separate: **2→3**, **3→4**, **4→5**.
- If at least one **可研究** candidate exists in a higher stage, lower-stage A/B/C candidates are
  downgraded one grade (one-word candidates stay observation-only).
- If **no** 2→3/3→4/4→5 candidate is 可研究, a **观察区（全梯队无可做）** is appended:
  sorted 4→5, 3→4, 2→3, then by score descending; each row keeps its original grade/action
  and gets the display action **观察（回看）**. This is a watchlist, not a buy signal.

## Automation

One **Windows scheduled task** `dsh-longtou-panel-refresh` carries four daily triggers
(Asia/Shanghai, Monday–Friday). All four share one action, `tools\panel_refresh.cmd` →
`tools\panel_refresh.py --auto`, which picks the phase from the current clock:

| Time | Phase | What it does |
|---|---|---|
| 09:25 | open (集合竞价结束) | `cycle_collect.py` (live emotion snapshot) + `auto_task.py --mode auction` (open-price report) |
| 11:30 | noon (上午收盘) | `cycle_collect.py` only — refreshes `/cycle.json` |
| 13:00 | pm (下午开盘) | `cycle_collect.py` only |
| 15:00 | close (下午收盘) | `cycle_collect.py` + `auto_task.py --mode close` (eltdx daily bars → `market_stats.json` + rebuilt report) |

`auto_task.py` checks the TDX workday calendar first and prints `not trading day` on
holidays; `cycle_collect.py` is tolerance-first, so a weekend/holiday run only rewrites the
previous base-date snapshot. Weekends are excluded by the triggers, holidays still cause one
harmless no-op run each. The close phase probes eltdx read-only and retries up to 3 × 60 s
if today's daily bar is not published yet (measured 2026-10-22: the bar was already there at
15:06).

Re-registering is idempotent (`Register-ScheduledTask -Force`); the task logs to
`output/panel_refresh.log`. It runs as the logged-on user (Interactive token) and does not
restart the 8765 panel server — the panel re-reads `/latest.md` and `/cycle.json` itself.

Requirements: the PC must be on with the user logged on, and the TDX/eltdx data connection
must be available. No DSH session is needed for the task to fire.

> **Legacy backtest note:** the checkpoints below were produced with the earlier
> 20-day-max-scaled auction gate. After the 2026-10-19 switch to **竞额20日比%**
> (09:25 amount ÷ 20-day max daily amount), those numbers are historical and
> not directly comparable; rerun `backtest_full.py` for the new definition.

## Backtest checkpoint (auction gate only)

Sample: 180 three-board events, 2021–2026, 121 rows, 100 buyable. Entry at 09:30
open; exit at close; no leader/limit-down gates yet; gross of costs.

| Group | n | 1-day avg / win | 5-day avg / win |
|---|---:|---:|---:|
| All buyable | 100 | -0.86% / 49.0% | -5.99% / 28.0% |
| Auction pass | 26 | -1.82% / 57.7% | -7.56% / 30.8% |
| Auction fail | 74 | -0.53% / 45.9% | -5.44% / 27.0% |
| Turnover healthy | 32 | -0.69% / 56.2% | -3.86% / 40.6% |
| Turnover death | 14 | -0.42% / 35.7% | -11.61% / 14.3% |

This sample does **not** validate the auction gate as a standalone edge. The pass
group has a higher 1-day win rate but a worse average, and both groups lose over
5 days. The next backtest must add the leader-state, limit-down, and stage gates;
do not treat the current gate as proven.

## Full-gate checkpoint (120-event sample, full market stats)

Sample: 120 three-board events, 109 rows, 70 buyable; entry at 09:30 open; exit close;
gross of costs. The market stats cache now includes high-board and one-word limit-down
counts, so the limit-down gate uses the full intensity formula.

| Group | n | 1-day avg / win | 5-day avg / win |
|---|---:|---:|---:|
| All buyable | 70 | -0.27% / 42.9% | -5.61% / 28.6% |
| Auction pass | 21 | -1.55% / 47.6% | -3.81% / 38.1% |
| Auction fail | 49 | +0.28% / 40.8% | -6.38% / 24.5% |
| Grade A/B | 3 | +1.82% / 66.7% | -2.34% / 33.3% |
| Grade C | 7 | -1.30% / 57.1% | -2.96% / 28.6% |
| Turnover healthy | 20 | +1.02% / 50.0% | -2.17% / 35.0% |
| Turnover death | 6 | +1.73% / 33.3% | -9.68% / 33.3% |

This sample still does **not** validate a positive edge. The auction-pass group has
a worse 1-day average than the fail group, and the A/B grade has only three trades.
The turnover filter is the most consistent separator (healthy 5-day -2.17% vs death
-9.68%), but the base rate is negative. Do not treat the current gate set as proven;
a full-event or multi-year sample is still required.

## Known limitations

- Local TDX `.day` files currently end at 2026-10-13; `data/daily_overlay.json` fills
  2026-10-14..16 from `eltdx` daily bars. Run `update_daily_overlay.py` and rebuild
  `market_stats.json` after each close (or before the next 09:25 run).
- The 09:25 collector is historical from `eltdx`; the live path still needs a daily update
  routine.
- ST filtering uses the local name snapshot; historical ST status changes are not fully
  modeled yet.
- The leader-state engine currently focuses on the current market leader. A former leader's
  break window / suspension is a next-step refinement.
- The panel is live after a clean host restart. The current page may need one refresh if
  it was loaded before the host restart.

## Notes

- `eltdx` is a third-party open-source TDX protocol client; its license is "Other" /
  NOASSERTION. Keep it for personal research and check the license before redistribution.
- The data is for research/review only, not investment advice.
