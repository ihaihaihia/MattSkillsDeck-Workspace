"""龙虎榜段落（engine.attach_lhb + cli.render_markdown）的单测。

全离线：``lhb.fetch_billboard`` 与 ``lhb.t1_relimit_rate`` 全部 mock 掉，
不发网络请求、不读 TDX .day 文件。
"""

from __future__ import annotations

import unittest
from unittest import mock

from longtou import engine, lhb
from longtou.cli import render_markdown

TARGET_DATE = 20261020

# 榜单样本：净买额用元，验证 → 亿 的换算（8800 万 → 0.88 亿）
BILLBOARD = [
    {
        "code": "600001",
        "name": "甲股份",
        "net_amt": 88000000.0,
        "reason": "日涨幅偏离值达到7%的前5只证券",
    },
    {
        "code": "600002",
        "name": "乙科技",
        "net_amt": -150000000.0,  # 净卖出 1.5 亿
        "reason": "连续三个交易日涨幅偏离值累计达20%",
    },
]

# 再板率样本：rate 是 0~1 的小数，渲染时乘 100
RATE_STAT = {
    "code": "600001",
    "samples": 12,
    "relimit": 3,
    "rate": 0.25,
    "threshold_pct": 9.8,
    "window": 250,
    "start_date": "20251020",
    "end_date": "20261020",
    "last_limit_up": 20261019,
    "samples_insufficient": False,
    "note": "",
}

GOOD_ROWS = [
    {"stage": "2→3", "stage_n": 2, "code": "600001", "name": "甲股份",
     "float_mcap_yi": 80.0, "cap_tier": "中盘", "turnover_pct": 10.0,
     "turnover_state": "健康区", "open_gap_pct": 4.0,
     "base_grade": "A", "final_grade": "A", "action": "可研究", "reasons": ["理由一"]},
    {"stage": "3→4", "stage_n": 3, "code": "600002", "name": "乙科技",
     "float_mcap_yi": 45.5, "cap_tier": "小盘", "turnover_pct": 18.0,
     "turnover_state": "健康区", "open_gap_pct": 3.0,
     "base_grade": "B", "final_grade": "B", "action": "观察", "reasons": ["理由二"]},
    {"stage": "3→4", "stage_n": 3, "code": "600003", "name": "丙材料",
     "float_mcap_yi": 60.0, "cap_tier": "中盘", "turnover_pct": 12.0,
     "turnover_state": "健康区", "open_gap_pct": 2.0,
     "base_grade": "B", "final_grade": "B", "action": "观察", "reasons": ["理由二"]},
]

ERROR_ROW = {"stage": "4→5", "stage_n": 4, "code": "600009", "name": "丁资源",
             "error": "日线不足 20 日"}
MARKET = {
    "limit_up": 55,
    "limit_down": 3,
    "broken_limit": 12,
    "seal_rate": 82,
    "limit_down_intensity": 0.4,
    "high_board_limit_down": 0,
    "one_word_limit_down": 0,
    "max_height": 4,
    "leader_codes": [],
    "leader_one_word_ratio": 0.5,
}


def make_report(rows, lhb_info) -> dict:
    """构造最小 report：四问给空 verdict，避免渲染路径读本地统计文件。"""
    return {
        "market": dict(MARKET),
        "target_date": TARGET_DATE,
        "next_date_text": "2026-10-21",
        "leader": None,
        "leader_break": None,
        "candidates": rows,
        "four_questions": {
            "emotion": {"missing": True, "verdict": "跳过"},
            "catalyst": {"missing": True, "verdict": "跳过"},
            "backtest": {"verdict": "跳过"},
            "memory": {"verdict": "跳过"},
        },
        "lhb": lhb_info,
    }


def split_cells(line: str) -> list[str]:
    parts = line.split("|")
    assert parts[0].strip() == "" and parts[-1].strip() == "", f"行未以 | 收尾: {line!r}"
    return [part.strip() for part in parts[1:-1]]


class AttachLhbTests(unittest.TestCase):
    """attach_lhb：字段写入、亿换算、异常降级。"""

    def _attach(self, rows, billboard=BILLBOARD, rate=RATE_STAT):
        with mock.patch.object(lhb, "fetch_billboard", return_value=billboard), \
                mock.patch.object(lhb, "t1_relimit_rate", return_value=rate):
            return engine.attach_lhb(rows, TARGET_DATE)

    def test_writes_four_fields(self):
        rows = [dict(row) for row in GOOD_ROWS] + [dict(ERROR_ROW)]
        info = self._attach(rows)
        up, down, miss, err = rows
        self.assertEqual(info["date"], "2026-10-20")
        self.assertTrue(info["published"])
        self.assertEqual(info["count"], 2)
        self.assertIsNone(info["error"])
        # 上榜：净买额元 → 亿 两位小数
        self.assertEqual(up["龙虎榜"], "上榜")
        self.assertEqual(up["龙虎榜净买亿"], 0.88)
        self.assertEqual(up["龙虎榜原因"], "日涨幅偏离值达到7%的前5只证券")
        # 净卖出照样上榜，负数保留
        self.assertEqual(down["龙虎榜"], "上榜")
        self.assertEqual(down["龙虎榜净买亿"], -1.5)
        # 榜单已发布但没上榜
        self.assertEqual(miss["龙虎榜"], "未上榜")
        self.assertIsNone(miss["龙虎榜净买亿"])
        self.assertEqual(miss["龙虎榜原因"], "")
        # T+1 再板率原样保留（0~1 小数，不在数据层换算）
        self.assertEqual(up["T1再板率"]["rate"], 0.25)
        self.assertEqual(up["T1再板率"]["samples"], 12)
        # error 行不写字段
        self.assertNotIn("龙虎榜", err)

    def test_empty_billboard_means_unpublished(self):
        rows = [dict(row) for row in GOOD_ROWS[:1]]
        info = self._attach(rows, billboard=[])
        self.assertFalse(info["published"])
        self.assertEqual(info["count"], 0)
        self.assertEqual(rows[0]["龙虎榜"], "未披露")
        self.assertIsNone(rows[0]["龙虎榜净买亿"])

    def test_fetch_error_degrades_without_raising(self):
        rows = [dict(row) for row in GOOD_ROWS[:1]]
        with mock.patch.object(
            lhb, "fetch_billboard", side_effect=lhb.LhbNetworkError("东财不可达")
        ), mock.patch.object(lhb, "t1_relimit_rate", return_value=RATE_STAT):
            info = engine.attach_lhb(rows, TARGET_DATE)  # 不得抛异常
        self.assertIn("LhbNetworkError", info["error"])
        self.assertIn("东财不可达", info["error"])
        self.assertFalse(info["published"])
        self.assertEqual(rows[0]["龙虎榜"], "未披露")
        # 榜单取数失败不影响本地再板率
        self.assertEqual(rows[0]["T1再板率"]["rate"], 0.25)

    def test_single_stock_rate_failure_isolated(self):
        rows = [dict(row) for row in GOOD_ROWS]

        def fake_rate(code: str, lookback: int = 250) -> dict:
            if code == "600001":
                raise FileNotFoundError(f"缺少 {code} 的 .day 文件")
            return RATE_STAT

        with mock.patch.object(lhb, "fetch_billboard", return_value=BILLBOARD), \
                mock.patch.object(lhb, "t1_relimit_rate", side_effect=fake_rate):
            engine.attach_lhb(rows, TARGET_DATE)
        self.assertIsNone(rows[0]["T1再板率"])
        self.assertEqual(rows[1]["T1再板率"]["rate"], 0.25)
        self.assertEqual(rows[0]["龙虎榜"], "上榜")  # 榜单字段仍正常


class RenderLhbSectionTests(unittest.TestCase):
    """render_markdown：段落开关 + 8 列表格列数。"""

    def _rows(self):
        rows = [dict(row) for row in GOOD_ROWS] + [dict(ERROR_ROW)]
        with mock.patch.object(lhb, "fetch_billboard", return_value=BILLBOARD), \
                mock.patch.object(lhb, "t1_relimit_rate", return_value=RATE_STAT):
            info = engine.attach_lhb(rows, TARGET_DATE)
        return rows, info

    @staticmethod
    def _lhb_block(text: str) -> list[str]:
        """取出龙虎榜表格（以「是否上榜」表头为界，到表格结束为止）。"""
        table = [line for line in text.splitlines() if line.startswith("|")]
        start = next(
            index for index, line in enumerate(table) if "是否上榜" in line
        )
        block = []
        for line in table[start:]:
            if len(split_cells(line)) != 8:
                break
            block.append(line)
        return block

    def _lhb_cells(self, text: str, code: str) -> list[str]:
        line = next(line for line in self._lhb_block(text) if f"| {code} |" in line)
        return split_cells(line)

    def test_section_rendered_with_8_columns(self):
        rows, info = self._rows()
        text = render_markdown(make_report(rows, info))
        self.assertIn("## 龙虎榜", text)
        self.assertIn("当日共 2 只上榜", text)
        self.assertIn("近 250 交易日、样本 <3 次不给值", text)
        block = self._lhb_block(text)
        self.assertEqual(len(block), 5)  # 表头 + 分隔 + 3 个非 error 行
        for line in block:
            self.assertEqual(len(split_cells(line)), 8, line)
        cells = self._lhb_cells(text, "600001")
        self.assertEqual(cells[3], "上榜")
        self.assertEqual(cells[4], "0.88")     # 净买额(亿)
        self.assertEqual(cells[5], "日涨幅偏离值达到7%的前5只证券")
        self.assertEqual(cells[6], "25.00%")   # rate 0~1 → 百分比
        self.assertEqual(cells[7], "12 次")
        miss = self._lhb_cells(text, "600003")
        self.assertEqual(miss[3], "未上榜")
        self.assertEqual(miss[4], "—")   # 净买额缺失
        self.assertEqual(miss[5], "—")   # 上榜原因缺失

    def test_unpublished_note(self):
        rows = [dict(row) for row in GOOD_ROWS[:1]]
        with mock.patch.object(lhb, "fetch_billboard", return_value=[]), \
                mock.patch.object(lhb, "t1_relimit_rate", return_value=RATE_STAT):
            info = engine.attach_lhb(rows, TARGET_DATE)
        text = render_markdown(make_report(rows, info))
        self.assertIn("## 龙虎榜", text)
        self.assertIn("当日榜单尚未披露", text)
        self.assertEqual(self._lhb_cells(text, "600001")[3], "未披露")

    def test_error_note_and_insufficient_samples(self):
        rows = [dict(row) for row in GOOD_ROWS[:1]]
        short = dict(RATE_STAT, samples=1, relimit=1, rate=None, samples_insufficient=True)
        with mock.patch.object(
            lhb, "fetch_billboard", side_effect=lhb.LhbNetworkError("超时")
        ), mock.patch.object(lhb, "t1_relimit_rate", return_value=short):
            info = engine.attach_lhb(rows, TARGET_DATE)
        text = render_markdown(make_report(rows, info))
        self.assertIn("取数失败", text)
        cells = self._lhb_cells(text, "600001")
        self.assertEqual(cells[6], "—")               # 样本不足不给值
        self.assertEqual(cells[7], "1 次（样本不足）")

    def test_no_section_without_lhb_info(self):
        text = render_markdown(make_report([dict(row) for row in GOOD_ROWS], None))
        self.assertNotIn("## 龙虎榜", text)

    def test_no_section_when_rows_have_no_fields(self):
        # lhb 结构在、但行上没有龙虎榜字段（例如手搓 report）：整段跳过
        text = render_markdown(
            make_report([dict(row) for row in GOOD_ROWS],
                        {"date": "2026-10-20", "published": True, "count": 2, "error": None})
        )
        self.assertNotIn("## 龙虎榜", text)


if __name__ == "__main__":
    unittest.main()
