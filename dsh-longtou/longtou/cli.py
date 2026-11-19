"""CLI entry point for dsh-longtou."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .engine import build_report, four_questions, stage_label
from .names import name_for
from .tdx import date_int_to_str


def _parse_stages(text: str) -> list[int]:
    stages = []
    for part in text.split(","):
        value = int(part.strip())
        if value < 2:
            raise argparse.ArgumentTypeError("stages must be >= 2")
        stages.append(value)
    return sorted(set(stages))


def _fmt(value: object, digits: int = 2) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _money_text(value: object) -> str:
    """金额文本：≥1 亿用两位小数的亿，否则用一位小数的万（不会出现 10000万）。"""
    if value is None:
        return "-"
    amount = float(value)
    if abs(amount) >= 1e8:
        return f"{amount / 1e8:.2f}亿"
    return f"{amount / 1e4:.1f}万"


def _wan(value: object) -> str:
    """封单额（元）→ 万，取整；None/缺失/脏数据统一「—」（口径取自 depth.py）。"""
    if value is None or value == "":
        return "—"
    try:
        return f"{float(value) / 1e4:.0f}"
    except (TypeError, ValueError):
        return "—"


def _pct(value: object) -> str:
    """封流比（%）两位小数；缺失「—」；已知但 <0.01% 记「<0.01%」（不写成 0.00%）。"""
    if value is None or value == "":
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if 0 < number < 0.005:  # 两位小数会四舍五入成 0.00%，与「未知」混淆
        return "<0.01%"
    return f"{number:.2f}%"


def render_markdown(report: dict) -> str:
    market = report["market"]
    target_text = date_int_to_str(report["target_date"])
    trade_text = report.get("next_date_text") or ""
    title = (
        f"# dsh-longtou 候选表 — 交易日 {trade_text}（T-1 {target_text}）"
        if trade_text
        else f"# dsh-longtou 候选表 — {target_text}"
    )
    leader = report.get("leader")
    lines = [
        title,
        "",
        "## 市场",
        "",
        f"- 涨停 **{market['limit_up']}** / 跌停 **{market['limit_down']}** / "
        f"炸板 **{market['broken_limit']}** / 封板率 **{market['seal_rate']}%**",
        f"- 跌停强度 **{market['limit_down_intensity']}** / 高位跌停 "
        f"**{market['high_board_limit_down']}** / 一字跌停 **{market['one_word_limit_down']}**",
        "- 20日最大额口径：地天板/水下日按 **50%** 折算，表格中用 **†** 标注",
        "- 开盘判定：**-1%~+1%** 合理；**+1%~+4%** 加一档；**<-1% 或 >4%** 降一档",
        "- 最高空间板：停牌期间无更高板，仍算该停牌板的周期",
        "- 个股情绪（8.57%）：独立/助力N，按同板块或同概念低位首一二三板数量排序",
        "- 昨日全天成交额（10%）：≤3亿满分、3~10亿线性递减、≥10亿 0 分且最终档位降一档",
        "- 涨幅空间（不计分）：左=10日到100%、右=30日到200%的剩余空间；悬停看解析",
        "- 事件驱动（不计分）：新闻/大盘/热点三维度；点击个股查看详情",
        "- 时机（买卖纪律）：**打板**=连板≥2 且昨日成交额≤10亿、竞价 +2%~+7%、分歧可控；"
        "**半路**=昨日成交额>10亿或竞价高开>7%，等回踩不追高；"
        "**不追**=基础档≤C、昨日成交额>20亿或竞价高开>9%（接近一字）",
        "- 参与位：打板价=涨停价；止损=次日 -3% 或 破板；半路等回踩均价/分时均线再进；不追不参与",
        "- 结构定性：候选池有 4 板以上＝有高度龙头，否则防一日游；"
        "同题材出现≥2 只且题材内最高仅 2 板＝跟风票，防一日游",
        "- 加分项：题材新鲜度 / 竞价强度(2%~7%) / 成交额健康(3~10亿) / 换手适中(5%~20%) / "
        "连板位置(2~3板) / 流通市值(30~150亿) / 分歧适中；逐条理由见「打板纪律」",
    ]
    break_info = report.get("leader_break")
    if break_info:
        lines.append(
            f"- 最高板已断板第 **{break_info['break_day']}** 天："
            f"原最高板 **{break_info['height']}** "
            f"({break_info['code']} {break_info['name']})，"
            f"最后涨停日 {break_info['last_high_date']}"
        )
    else:
        suspended_note = ""
        if leader and leader.get("suspended"):
            suspended_note = f"，停牌中（最后交易日 {leader.get('last_trade_date')}）"
        lines.append(
            f"- 最高板 **{market['max_height']}** "
            f"({(str(market['leader_codes'][0]) + ' ' + name_for(market['leader_codes'][0])).strip() if market['leader_codes'] else '-'})"
            f"{suspended_note}，最高板一字占比 **{market['leader_one_word_ratio']:.0%}**"
        )
    if leader and not break_info:
        break_text = (
            f"断板后 {leader['post_break_days']} 日"
            if leader["post_break_days"] in (1, 2)
            else "断板窗口：无"
        )
        suspended_text = ""
        if leader.get("suspended"):
            suspended_text = (
                f"，停牌中（最后交易日 {leader.get('last_trade_date')}），"
                "停牌期间无更高板，仍算空间板周期"
            )
        lines.append(
            f"- 最高板 60 日最大成交额 **{leader['sixty_day_max_amount'] / 1e8:.2f} 亿** "
            f"({leader['sixty_day_max_date']})，"
            f"{'天量次日' if leader['is_60d_max_next_day'] else '非天量次日'}，"
            f"{break_text}{suspended_text}"
        )
    # 25 列 → 24 列：「基础」档加回（紧接「分数」之后、「最终」之前），
    # 仍删掉「涨幅空间 / 操作 / 参与位」三列，末尾保留龙虎榜两列
    # 「龙虎榜净买(亿) | T+1再板率%」（表头、分隔行、数据行、错误行都必须同口径；
    # 分隔行统一由列表 join 生成，右对齐列用 ---:，其余用 ---）
    header = (
        "| 阶段 | 代码 | 名称 | 流通市值(亿) | 档位 | 昨换手% | 换手状态 | 个股情绪 | 事件驱动 | 昨日全天成交额 | 竞额20日比% | 门槛% | 开盘% | 分数 | 基础 | 最终 | 时机 | 结构定性 | 加分项 | 竞价封单(万) | 实时封单(万) | 封流比% | 龙虎榜净买(亿) | T+1再板率% |",
        "|" + "|".join((
            ["---"] * 3          # 阶段、代码、名称
            + ["---:"]           # 流通市值(亿)
            + ["---"]            # 档位
            + ["---:"]           # 昨换手%
            + ["---"] * 4        # 换手状态、个股情绪、事件驱动、昨日全天成交额
            + ["---:"] * 3       # 竞额20日比%、门槛%、开盘%
            + ["---:"]           # 分数
            + ["---"]            # 基础（与「最终」同为档位字母，居中）
            + ["---"] * 4        # 最终、时机、结构定性、加分项
            + ["---", "---", "---:"]  # 竞价封单(万)、实时封单(万)、封流比%
            + ["---:", "---:"]   # 龙虎榜净买(亿)、T+1再板率%
        )) + "|",
    )
    # 阶段表按数据生成：2→3 / 3→4 / 4→5 三段常驻占位，
    # 候选里出现更高的进阶（stage_n=5 即 5→6、6 即 6→7 …）就一并列出；
    # 只列真实存在的 stage_n，绝不凭空造出没有数据的阶段。
    stage_set = {2, 3, 4}
    for row in report["candidates"]:
        value = row.get("stage_n")
        if isinstance(value, int) and value > 0:
            stage_set.add(value)
    stage_labels = {stage: f"{stage}→{stage + 1}" for stage in stage_set}
    lines.extend(["", "## 候选", ""])
    for stage in sorted(stage_set):
        label = stage_labels[stage]
        # 分数从高到低排列：同阶段内按 score 降序；取不到分数的行置底，错误行再置底
        def _score_key(row):
            score = row.get("score")
            score_val = score if isinstance(score, (int, float)) else float("-inf")
            return (0 if row.get("error") else 1, score_val)

        stage_rows = sorted(
            (
                row for row in report["candidates"]
                if row.get("stage_n") == stage or row.get("stage") == label
            ),
            key=_score_key,
            reverse=True,
        )
        lines.extend([f"### {label}", ""])
        if not stage_rows:
            lines.extend(["- 暂无候选", ""])
            continue
        lines.extend(header)
        for row in stage_rows:
            if row.get("error"):
                # 24 列：3 个实体列 + 20 个占位 + 错误信息
                lines.append(
                    f"| {row.get('stage', label)} | {row['code']} | {row.get('name', '')} | "
                    + " | ".join(["-"] * 20)
                    + f" | {row['error']} |"
                )
                continue
            ratio_text = f"{_fmt(row.get('auction_ratio_max'))}%"
            if row.get("max20_water_days"):
                ratio_text += "†"
            # 龙虎榜净买(亿)：engine.attach_lhb 写入，只前 12 行有该键，其余必须用 .get()
            lhb_net = row.get("龙虎榜净买亿")
            lhb_net_text = _fmt(lhb_net) if isinstance(lhb_net, (int, float)) else "—"
            # T+1 再板率：T1再板率 可能是 dict / None；rate 是 0~1 小数，显示时乘 100
            rate = (row.get("T1再板率") or {}).get("rate")
            t1_text = f"{_fmt(rate * 100)}%" if isinstance(rate, (int, float)) else "—"
            lines.append(
                "| {stage} | {code} | {name} | {mcap} | {tier} | {turn} | {tstate} | {emotion} | {event} | {prev_amount} | {ratio} | {gate} | {gap} | "
                "{score} | {base} | {final} | "
                "{timing} | {structure} | {bonus} | "
                "{seal_auction} | {seal_realtime} | {seal_ratio} | {lhb_net} | {t1_rate} |".format(
                    stage=row["stage"],
                    code=row["code"],
                    name=row.get("name", ""),
                    mcap=_fmt(row["float_mcap_yi"]),
                    tier=row["cap_tier"],
                    turn=f"{_fmt(row['turnover_pct'])}%",
                    tstate=row["turnover_state"],
                    emotion=row.get("stock_emotion", ""),
                    event=row.get("event_driver", ""),
                    prev_amount=_money_text(row.get("prev_amount")),
                    ratio=ratio_text,
                    gate=f"{_fmt(row.get('auction_gate'))}%",
                    gap=f"{_fmt(row['open_gap_pct'])}%",
                    score=_fmt(row.get("score"), 1),
                    # 基础档（engine.base_grade，取值 A/B/C/D）；旧缓存缺键时显示 -
                    base=row.get("base_grade") or "-",
                    final=row["final_grade"],
                    # 打板纪律 3 列（engine.annotate_discipline 写入）；缺失时留 -
                    timing=row.get("时机", "-"),
                    structure=row.get("结构定性", "-"),
                    bonus=row.get("加分项", "-"),
                    # 封单额双口径 + 封流比（engine.attach_seal_amounts 写入）；取不到一律「—」
                    seal_auction=_wan(row.get("竞价封单")),
                    seal_realtime=_wan(row.get("实时封单")),
                    seal_ratio=_pct(row.get("封流比")),
                    # 龙虎榜两列：缺失统一「—」
                    lhb_net=lhb_net_text,
                    t1_rate=t1_text,
                )
            )
        lines.append("")

    # 盘前四问：只读本地数据（market_stats.json + TDX 日线），不发网络请求。
    # 旧缓存 json 没有 four_questions 字段时按同一口径现算，保证离线渲染也有四问。
    answers = report.get("four_questions")
    if not answers:
        try:
            answers = four_questions(report["target_date"], market, report["candidates"])
        except Exception as exc:
            answers = {"error": f"四问统计失败：{type(exc).__name__}: {exc}"}
    lines.extend(["", "## 盘前四问", ""])
    if answers.get("error"):
        lines.append(f"- {answers['error']}")
    else:
        emotion = answers.get("emotion") or {}
        days = emotion.get("days") or []
        if days:
            parts = [
                f"{date_int_to_str(int(day['date']))} 涨停 {day['limit_up']} 家/最高 {day['max_height']} 板"
                for day in reversed(days)
            ]
            lines.append(
                f"1. **近 3 日涨停/连板家数**：{'；'.join(parts)}。情绪判断：{emotion.get('verdict', '-')}"
            )
        else:
            lines.append(f"1. **近 3 日涨停/连板家数**：{emotion.get('verdict', '无数据')}")
        catalyst = answers.get("catalyst") or {}
        themes = catalyst.get("themes") or []
        if catalyst.get("missing") or not themes:
            lines.append(
                f"2. **盘前催化**：{catalyst.get('verdict', '事件驱动数据缺失，本项不构成结论')}"
            )
        else:
            detail = "；".join(
                f"〔{item['theme']}〕{len(item['codes'])} 只"
                f"（{'、'.join(item['drivers']) or '无事件标签'}）"
                for item in themes
            )
            lines.append(f"2. **盘前催化**：{detail}。{catalyst.get('verdict', '')}")
        backtest = answers.get("backtest") or {}
        lines.append(
            f"3. **历史回溯**：{backtest.get('verdict', '无数据')}；{backtest.get('bar_check', '')}"
        )
        memory = answers.get("memory") or {}
        mem_themes = memory.get("themes") or []
        if mem_themes:
            detail = "；".join(
                f"〔{item['theme']}〕{item.get('label', '-')}" for item in mem_themes
            )
            lines.append(f"4. **题材记忆**：{detail}。{memory.get('verdict', '')}")
        else:
            lines.append(f"4. **题材记忆**：{memory.get('verdict', '无候选')}")
    # 封单额口径说明：两条口径 + 本地缺口，原样输出（engine.SEAL_NOTE）
    lines.extend(["", "## 封单口径", ""])
    lines.append(
        f"- {report.get('seal_note') or '口径说明缺失：竞价封单＝09:25 未匹配量×竞价价；'
        '实时封单＝五档买一价×买一量×100，仅盘中可用；封流比＝封单额÷流通市值；'
        '历史收盘封单额本地无数据源。'}"
    )
    for row in report["candidates"]:
        if row.get("error"):
            continue
        note = row.get("实时封单说明") or ""
        if note:
            lines.append(f"- {row.get('stage', '-')} {row['code']} 实时封单：{note}")
            break
    # 流通市值口径说明：历史报告必须用当日收盘×当日股本，不拿最新股本回填。
    # 换手率、档位、竞额门槛均由同一个历史股本派生，故此说明同时覆盖三者。
    lines.extend(["", "## 流通市值口径", ""])
    lines.append(
        "- 流通市值(亿) = **该交易日收盘价 × 该交易日当时的流通股本**"
        "（通达信股本变迁按变动日取目标日之前最近一条，单位股）；"
        "档位、换手率、竞额门槛与它同源，均按当日口径计算，"
        "不用今天的最新股本回填历史日期。"
    )
    lines.append("")

    # 打板纪律明细：表格里只放 ✓/✗ 短标签，逐条理由放这里（纯排版）
    discipline_rows = [row for row in report["candidates"] if not row.get("error")]
    if discipline_rows:
        lines.extend(["", "## 打板纪律", ""])
        for row in discipline_rows:
            lines.append(
                f"- **{row.get('stage', '-')} {row['code']} {row.get('name', '')}**："
                f"时机 **{row.get('时机', '-')}**；参与位：{row.get('参与位', '-')}；"
                f"结构定性：{row.get('结构定性', '-')}；加分项 {row.get('加分项', '-')} —— "
                f"{row.get('加分项理由', '')}"
            )

    # 龙虎榜 + T+1 再板率：只有报告真的取了该结构、且行上有字段时才输出整段
    # （老缓存 json / 手搓 report 没有这些键，整段跳过，不影响既有段落）
    lhb_info = report.get("lhb")
    lhb_rows = [
        row for row in report["candidates"]
        if not row.get("error") and "龙虎榜" in row
    ]
    if lhb_info and lhb_rows:
        day_text = lhb_info.get("date") or "-"
        lines.extend(["", "## 龙虎榜", ""])
        if lhb_info.get("error"):
            lines.append(
                f"- 榜单日期：{day_text}；取数失败：{lhb_info['error']}"
                "（本节其余口径不受影响）"
            )
        elif lhb_info.get("published"):
            lines.append(f"- 榜单日期：{day_text}；当日共 {lhb_info.get('count', 0)} 只上榜")
        else:
            lines.append(
                f"- 榜单日期：{day_text}；当日榜单尚未披露"
                "（龙虎榜为 T 日盘后数据，竞价模式下只能看 T+1 再板率）"
            )
        lines.extend(
            [
                "",
                "| 阶段 | 代码 | 名称 | 是否上榜 | 净买额(亿) | 上榜原因 | T+1 再板率 | 再板样本 |",
                "| --- | --- | --- | --- | ---: | --- | ---: | ---: |",
            ]
        )
        for row in lhb_rows:
            net = row.get("龙虎榜净买亿")
            rate_info = row.get("T1再板率") or {}
            rate = rate_info.get("rate")
            # rate 是 0~1 的小数，显示时乘 100，不再额外除
            rate_text = f"{_fmt(rate * 100)}%" if isinstance(rate, (int, float)) else "—"
            samples = rate_info.get("samples")
            if isinstance(samples, int):
                sample_text = f"{samples} 次"
                if rate_info.get("samples_insufficient"):
                    sample_text += "（样本不足）"
            else:
                sample_text = "—"
            lines.append(
                f"| {row.get('stage', '-')} | {row.get('code', '-')} | {row.get('name', '-')} | "
                f"{row.get('龙虎榜', '-')} | {_fmt(net) if net is not None else '—'} | "
                f"{row.get('龙虎榜原因') or '—'} | {rate_text} | {sample_text} |"
            )
        lines.extend(
            [
                "",
                "- 口径：龙虎榜＝东财当日盘后披露（T 日收盘后才有；竞价模式下当日榜为「未披露」）；"
                "T+1 再板率＝本地 TDX 日线统计「涨停日次日再涨停」的比例，"
                "近 250 交易日、样本 <3 次不给值。",
            ]
        )

    emotion_comments = []
    for row in report["candidates"]:
        if row.get("error") or not row.get("stock_emotion_detail"):
            continue
        payload = {
            "label": row.get("stock_emotion", ""),
            "text": row.get("stock_emotion_detail", ""),
            "items": row.get("stock_emotion_items", []),
        }
        emotion_comments.append(
            f"<!-- emotion:{row['code']} {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))} -->"
        )
    if emotion_comments:
        lines.extend(emotion_comments)
        lines.append("")

    event_comments = []
    for row in report["candidates"]:
        if row.get("error") or not row.get("event_detail"):
            continue
        payload = {
            "label": row.get("event_driver", ""),
            "detail": row.get("event_detail", ""),
            "news": row.get("event_news", ""),
            "market": row.get("event_market", ""),
            "hotspot": row.get("event_hotspot", ""),
        }
        event_comments.append(
            f"<!-- event:{row['code']} {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))} -->"
        )
    if event_comments:
        lines.extend(event_comments)
        lines.append("")

    if any(row.get("max20_water_days") for row in report["candidates"] if not row.get("error")):
        lines.extend(
            [
                "**† 20日最大额含地天板/水下日，已按 50% 折算（显示的是折算后金额）。**",
                "",
            ]
        )

    actionable = [
        row for row in report["candidates"]
        if not row.get("error") and row.get("action") == "可研究"
    ]
    if report["candidates"] and not actionable:
        rows = [row for row in report["candidates"] if not row.get("error")]
        rows.sort(key=lambda row: (row.get("stage_n", 0), row.get("score", 0)), reverse=True)
        # 阶段顺序文案由实际数据生成，不硬编码固定三段
        order_text = "、".join(
            stage_label(stage)
            for stage in sorted(
                {int(row.get("stage_n") or 0) for row in rows} - {0}, reverse=True
            )
        ) or "无"
        lines.extend(
            [
                "## 观察区（全梯队无可做）",
                "",
                f"> 排序：{order_text}；同梯队按分数降序。保留原结论，不构成买入。",
                "",
                "| 排序 | 阶段 | 代码 | 名称 | 分数 | 操作 | 原结论 |",
                "|---:|---|---|---|---:|---|---|",
            ]
        )
        for rank, row in enumerate(rows, 1):
            lines.append(
                f"| {rank} | {row.get('stage', '-')} | {row['code']} | {row.get('name', '')} | "
                f"{_fmt(row.get('score'), 1)} | 观察（回看） | {row['final_grade']}/{row['action']} |"
            )
        lines.append("")

    lines.extend(["", "## 原因", ""])
    for row in report["candidates"]:
        if row.get("error"):
            lines.append(f"- **{row['code']} {row.get('name', '')}**: {row['error']}")
        else:
            lines.append(
                f"- **{row['code']} {row.get('name', '')}** "
                f"({row['stage']}, {row['final_grade']}/{row['action']}): "
                + "；".join(row["reasons"])
            )
    return "\n".join(lines) + "\n"


def cmd_report(args: argparse.Namespace) -> int:
    report = build_report(
        args.date,
        _parse_stages(args.stages),
        with_lhb=True,
        auto_extend=not args.no_auto_extend,
    )
    output = Path(args.out) if args.out else Path("output") / f"report_{args.date}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown = render_markdown(report)
    md_path = output.with_suffix(".md")
    md_path.write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"JSON: {output}")
    print(f"Markdown: {md_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(prog="longtou", description="dsh-longtou research engine")
    parser.add_argument("--version", action="store_true", help="print version")
    subparsers = parser.add_subparsers(dest="command")

    report = subparsers.add_parser("report", help="build a candidate report for a T-1 close")
    report.add_argument("--date", type=int, required=True, help="T-1 date, YYYYMMDD")
    report.add_argument("--stages", default="2,3,4", help="comma-separated: 2,3,4")
    report.add_argument("--out", help="JSON output path")
    report.add_argument(
        "--no-auto-extend",
        action="store_true",
        help="do not auto-extend stages to the day's highest board",
    )
    report.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    if args.version:
        print(__version__)
        return 0
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
