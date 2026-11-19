"""cli.render_markdown 的 24 列表格单测。

全离线：报告 dict 全部内存构造，不跑 build_report、不读 output/、不读 TDX。
四问部分要么直接放进 report["four_questions"]，要么 mock 掉 cli.four_questions，
确保渲染路径不触发本地统计文件读取。
"""

from __future__ import annotations

import unittest
from unittest import mock

from longtou.cli import render_markdown

MARKET = {
    "limit_up": 55,
    "limit_down": 3,
    "broken_limit": 12,
    "seal_rate": 82,
    "limit_down_intensity": 0.4,
    "high_board_limit_down": 0,
    "one_word_limit_down": 0,
    "max_height": 4,
    "leader_codes": [],  # 空：避免 name_for 读本地名称表
    "leader_one_word_ratio": 0.5,
}

FOUR_ANSWERS = {
    "emotion": {
        "missing": False,
        "days": [
            {"date": 20261020, "limit_up": 55, "max_height": 4},
            {"date": 20261019, "limit_up": 40, "max_height": 3},
        ],
        "verdict": "高度抬升（修复）、涨停家数回升",
    },
    "catalyst": {
        "missing": False,
        "themes": [{"theme": "AI", "drivers": ["AI·主线"], "codes": ["600001", "600002"]}],
        "verdict": "1 个题材有事件标签。",
    },
    "backtest": {"verdict": "最高板 4 板出现后，次日涨停家数平均变化 +3.0 家",
                 "bar_check": "日线复核不可用（未传入股票表）"},
    "memory": {"themes": [{"theme": "AI", "label": "新题材（近 20 日未上板）"}],
               "verdict": "1 个题材中 0 个近 20 日有成员上板。"},
}

# 新版行：24 列所需字段齐全（含基础档 + 封单双口径 + 打板纪律 + 龙虎榜两列）
NEW_ROW = {
    "stage": "2→3", "stage_n": 2, "code": "600001", "name": "甲股份",
    "float_mcap_yi": 80.0, "cap_tier": "中盘",
    "turnover_pct": 10.0, "turnover_state": "健康区",
    "stock_emotion": "独立3", "event_driver": "AI·主线", "prev_amount": 6e8,
    "auction_ratio_max": 55.0, "auction_gate": 30.0, "open_gap_pct": 4.0,
    "gain_space": "左 10%", "score": 72.5,
    "base_grade": "A", "final_grade": "A", "action": "可研究",
    "reasons": ["理由一", "理由二"],
    "时机": "打板", "参与位": "打板价=涨停价", "结构定性": "有高度龙头", "加分项": "7/7",
    "加分项理由": "题材新鲜度加分",
    "竞价封单": 15000000.0,   # 1500 万
    "实时封单": None,
    "封流比": 1.234,
    "实时封单说明": "非交易时段，实时封单不可得",
    "龙虎榜": "上榜", "龙虎榜净买亿": 1.10,
    "T1再板率": {"rate": 0.2857, "samples": 7, "samples_insufficient": False},
}

# 旧版行：完全没有 depth / 时机 / 结构定性 / 加分项 等新键
OLD_ROW = {
    "stage": "3→4", "stage_n": 3, "code": "600002", "name": "乙科技",
    "float_mcap_yi": 45.5, "cap_tier": "小盘",
    "turnover_pct": 18.0, "turnover_state": "健康区",
    "open_gap_pct": 3.0,
    "base_grade": "B", "final_grade": "B", "action": "观察",
    "reasons": ["旧缓存行（无新增字段）"],
}

ERROR_ROW = {"stage": "4→5", "stage_n": 4, "code": "600003", "name": "丙材料",
             "error": "日线不足 20 日"}


def split_cells(line: str) -> list[str]:
    """把 `| a | b |` 拆成单元格文本；要求首尾都是竖线。"""
    parts = line.split("|")
    assert parts[0].strip() == "" and parts[-1].strip() == "", f"行未以 | 收尾: {line!r}"
    return [part.strip() for part in parts[1:-1]]


def pipe_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("|")]


def make_report(candidates, *, four=None, seal_note=None) -> dict:
    report = {
        "market": dict(MARKET),
        "target_date": 20261020,
        "next_date_text": "2026-10-21",
        "leader": None,
        "leader_break": None,
        "candidates": candidates,
        "four_questions": dict(FOUR_ANSWERS) if four is None else four,
    }
    if seal_note is not None:
        report["seal_note"] = seal_note
    return report


class CandidateTableColumnTests(unittest.TestCase):
    """表头 / 分隔行 / 数据行 / 错误行必须同为 24 列。"""

    def setUp(self):
        self.text = render_markdown(make_report([NEW_ROW, OLD_ROW, ERROR_ROW]))
        self.lines = pipe_lines(self.text)

    def test_header_has_24_cells(self):
        header = next(line for line in self.lines if line.startswith("| 阶段 "))
        cells = split_cells(header)
        self.assertEqual(len(cells), 24)
        self.assertEqual(cells[:3], ["阶段", "代码", "名称"])
        self.assertEqual(cells[13:19], ["分数", "基础", "最终", "时机", "结构定性", "加分项"])
        self.assertEqual(cells[19:], ["竞价封单(万)", "实时封单(万)", "封流比%",
                                       "龙虎榜净买(亿)", "T+1再板率%"])

    def test_deleted_columns_absent(self):
        header = next(line for line in self.lines if line.startswith("| 阶段 "))
        cells = split_cells(header)
        for gone in ("操作", "涨幅空间", "参与位"):
            self.assertNotIn(gone, cells)
        # 「基础」已加回，且位置严格在「分数」之后、「最终」之前
        self.assertEqual(cells[13:16], ["分数", "基础", "最终"])

    def test_separator_row_has_24_cells(self):
        header_index = next(
            index for index, line in enumerate(self.lines) if line.startswith("| 阶段 ")
        )
        cells = split_cells(self.lines[header_index + 1])
        self.assertEqual(len(cells), 24)
        self.assertEqual(set(cells), {"---", "---:"})
        # 右对齐列（数值/百分比/金额）必须是 ---:；其余（含「基础」档位列）必须 ---
        right_aligned = {3, 5, 10, 11, 12, 13, 21, 22, 23}
        for index, cell in enumerate(cells):
            expected = "---:" if index in right_aligned else "---"
            self.assertEqual(cell, expected, f"第 {index} 列对齐符应为 {expected}")

    def test_every_table_line_has_24_cells(self):
        # 3 个梯队各一组（表头 + 分隔 + 数据），共 9 行；每行都必须是 24 列
        self.assertEqual(len(self.lines), 9)
        for line in self.lines:
            self.assertEqual(len(split_cells(line)), 24, line)
            self.assertEqual(line.count("|"), 25, line)

    def test_new_row_renders_seal_values(self):
        row = next(line for line in self.lines if "| 600001 |" in line)
        cells = split_cells(row)
        self.assertEqual(len(cells), 24)
        self.assertEqual(cells[0], "2→3")
        self.assertEqual(cells[5], "10.00%")
        self.assertEqual(cells[13], "72.5")     # 分数保留一位小数
        self.assertEqual(cells[14], "A")        # 基础档
        self.assertEqual(cells[15], "A")        # 最终档
        self.assertEqual(cells[16], "打板")
        self.assertEqual(cells[19], "1500")     # 1500 万元 → 1500
        self.assertEqual(cells[20], "—")        # 实时封单缺失
        self.assertEqual(cells[21], "1.23%")    # 封流比两位小数

    def test_new_row_renders_lhb_columns(self):
        row = next(line for line in self.lines if "| 600001 |" in line)
        cells = split_cells(row)
        self.assertEqual(cells[22], "1.10")     # 龙虎榜净买 1.10 亿
        self.assertEqual(cells[23], "28.57%")   # 0.2857 → 28.57%

    def test_lhb_columns_placeholder_when_missing(self):
        # 旧缓存行（无 龙虎榜净买亿 / T1再板率 键）与 None / 空 dict 都必须渲染「—」
        rows = [
            dict(OLD_ROW),
            dict(OLD_ROW, code="600004", name="丁设备", stage="2→3", stage_n=2,
                 T1再板率=None),
            dict(OLD_ROW, code="600005", name="戊软件", stage="2→3", stage_n=2,
                 T1再板率={}, 龙虎榜净买亿=None),
            dict(OLD_ROW, code="600006", name="己电子", stage="2→3", stage_n=2,
                 T1再板率={"rate": None, "samples": 0}),
        ]
        text = render_markdown(make_report(rows))
        lines = pipe_lines(text)
        for code in ("600002", "600004", "600005", "600006"):
            row = next(line for line in lines if f"| {code} |" in line)
            cells = split_cells(row)
            self.assertEqual(len(cells), 24)
            self.assertEqual(cells[22], "—", row)
            self.assertEqual(cells[23], "—", row)

    def test_old_row_uses_placeholders(self):
        row = next(line for line in self.lines if "| 600002 |" in line)
        cells = split_cells(row)
        self.assertEqual(len(cells), 24)
        self.assertEqual(cells[14], "B")                        # 基础档沿用旧缓存键
        self.assertEqual(cells[16:19], ["-", "-", "-"])         # 新纪律列占位
        self.assertEqual(cells[19:22], ["—", "—", "—"])         # 封单三列占位
        self.assertEqual(cells[0], "3→4")
        self.assertEqual(cells[3], "45.50")

    def test_base_grade_placeholder_when_missing(self):
        # 旧缓存行没有 base_grade 键时必须显示 "-"，不能留空导致列错位
        row = dict(NEW_ROW, code="600007", name="庚材料", stage="2→3", stage_n=2)
        row.pop("base_grade")
        text = render_markdown(make_report([row]))
        line = next(ln for ln in pipe_lines(text) if "| 600007 |" in ln)
        cells = split_cells(line)
        self.assertEqual(len(cells), 24)
        self.assertEqual(cells[13:16], ["72.5", "-", "A"])

    def test_error_row_has_24_cells_and_error_text(self):
        row = next(line for line in self.lines if "| 600003 |" in line)
        cells = split_cells(row)
        self.assertEqual(len(cells), 24)
        self.assertEqual(cells[:3], ["4→5", "600003", "丙材料"])
        self.assertEqual(cells[3:23], ["-"] * 20)
        self.assertEqual(cells[23], "日线不足 20 日")


class LegacyReportTests(unittest.TestCase):
    """只有旧版行（无任何新增字段）时也必须渲染成 24 列表，并额外给出观察区。"""

    def setUp(self):
        self.text = render_markdown(make_report([OLD_ROW]))
        self.lines = pipe_lines(self.text)

    def test_candidate_rows_still_24_cells(self):
        candidate_lines = [
            line for line in self.lines if "| 600002 |" in line and line.count("|") == 25
        ]
        self.assertEqual(len(candidate_lines), 1)
        self.assertEqual(split_cells(candidate_lines[0])[22:], ["—", "—"])

    def test_watch_area_table_rendered_with_7_columns(self):
        index = next(
            i for i, line in enumerate(self.lines) if line.startswith("| 排序 |")
        )
        cells = split_cells(self.lines[index])
        self.assertEqual(cells, ["排序", "阶段", "代码", "名称", "分数", "操作", "原结论"])
        self.assertEqual(len(split_cells(self.lines[index + 1])), 7)
        self.assertIn("观察（回看）", self.lines[index + 2])
        self.assertIn("## 观察区（全梯队无可做）", self.text)


class FourQuestionsSectionTests(unittest.TestCase):
    def test_four_answers_from_report_rendered(self):
        text = render_markdown(make_report([NEW_ROW]))
        self.assertIn("## 盘前四问", text)
        self.assertIn("2026-10-20 涨停 55 家/最高 4 板", text)
        self.assertIn("情绪判断：高度抬升（修复）、涨停家数回升", text)
        self.assertIn("2. **盘前催化**：〔AI〕2 只（AI·主线）。1 个题材有事件标签。", text)
        self.assertIn("3. **历史回溯**：最高板 4 板出现后", text)
        self.assertIn("日线复核不可用（未传入股票表）", text)
        self.assertIn("4. **题材记忆**：〔AI〕新题材（近 20 日未上板）。", text)

    def test_missing_answers_are_computed_via_four_questions(self):
        # report 里没有 four_questions 时走 cli 的现算分支；这里 mock 掉避免读本地数据
        report = make_report([NEW_ROW], four={})
        with mock.patch("longtou.cli.four_questions", return_value=FOUR_ANSWERS) as spy:
            text = render_markdown(report)
        spy.assert_called_once()
        self.assertIn("情绪判断：高度抬升（修复）、涨停家数回升", text)

    def test_four_questions_failure_degrades_to_one_line(self):
        report = make_report([NEW_ROW], four={})
        with mock.patch("longtou.cli.four_questions", side_effect=RuntimeError("磁盘缺失")):
            text = render_markdown(report)
        self.assertIn("- 四问统计失败：RuntimeError: 磁盘缺失", text)

    def test_empty_catalyst_and_memory_degrade(self):
        answers = {
            "emotion": {"missing": True, "days": [], "verdict": "无目标日数据"},
            "catalyst": {"missing": True, "themes": [],
                         "verdict": "事件驱动数据缺失，本项不构成结论"},
            "backtest": {},
            "memory": {"themes": [], "verdict": "候选池为空，本项不构成结论"},
        }
        text = render_markdown(make_report([NEW_ROW], four=answers))
        self.assertIn("1. **近 3 日涨停/连板家数**：无目标日数据", text)
        self.assertIn("2. **盘前催化**：事件驱动数据缺失，本项不构成结论", text)
        self.assertIn("4. **题材记忆**：候选池为空，本项不构成结论", text)


class SealNoteSectionTests(unittest.TestCase):
    def test_custom_seal_note_and_realtime_note_rendered(self):
        text = render_markdown(make_report([NEW_ROW], seal_note="口径说明：竞价封单=09:25 未匹配量。"))
        self.assertIn("## 封单口径", text)
        self.assertIn("- 口径说明：竞价封单=09:25 未匹配量。", text)
        self.assertIn("- 2→3 600001 实时封单：非交易时段，实时封单不可得", text)

    def test_default_seal_note_when_missing(self):
        text = render_markdown(make_report([OLD_ROW]))
        self.assertIn("历史收盘封单额本地无数据源", text)


if __name__ == "__main__":
    unittest.main()
