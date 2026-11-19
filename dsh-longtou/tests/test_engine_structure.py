"""engine 的四连问 / 结构定性 / 时机 / 加分项纯函数单测。

全离线：行情统计、候选池都用内联 dict 构造；``four_questions`` 里的本地统计入口
``_stats_pairs`` 用 mock 替换，因此不读 data/market_stats.json、不读 TDX 日线、不联网。
"""

from __future__ import annotations

import unittest
from unittest import mock

from longtou import engine
from longtou.engine import (
    TIMING_HALF_WAY,
    TIMING_LIMIT_UP,
    TIMING_NO_CHASE,
    _catalyst_question,
    _emotion_question,
    bonus_items,
    bonus_reason_text,
    bonus_text,
    four_questions,
    pool_stats,
    structure_for,
    theme_key,
    timing_for,
)

# 合成 market_stats：20261019 / 20261020 两天，涨停家数 40→55，最高板 3→4
PAIRS = [
    (20261018, {"limit_up": 30, "max_height": 2, "broken_limit": 5,
                "height_stocks": {"2": ["600900"]}}),
    (20261019, {"limit_up": 40, "max_height": 3, "broken_limit": 8,
                "height_stocks": {"3": ["600901"]}}),
    (20261020, {"limit_up": 55, "max_height": 4, "broken_limit": 12,
                "height_stocks": {"4": ["600902"]}}),
]


class ThemeKeyTests(unittest.TestCase):
    def test_sector_wins_over_event_driver(self):
        row = {"stock_sector": "AI算力", "event_driver": "机器人·主线"}
        self.assertEqual(theme_key(row), "AI算力")

    def test_split_event_driver_theme_part(self):
        self.assertEqual(theme_key({"event_driver": "机器人·新题材"}), "机器人")

    def test_unclassified_when_both_missing(self):
        self.assertEqual(theme_key({}), "未分类")


class PoolStatsTests(unittest.TestCase):
    def test_counts_and_max_height(self):
        rows = [
            {"code": "600001", "stage_n": 2, "stock_sector": "AI"},
            {"code": "600002", "stage_n": 4, "stock_sector": "AI"},
            {"code": "600003", "stage_n": 3, "event_driver": "机器人·主线"},
            {"code": "600004", "error": "日线不足"},          # 错误行不参与统计
            {"code": "600005", "stage_n": 2},                 # 无题材标签：只贡献高度
        ]
        pool_max, stats = pool_stats(rows)
        self.assertEqual(pool_max, 4)
        self.assertEqual(stats["AI"], {"count": 2, "max_height": 4,
                                       "codes": ["600001", "600002"]})
        self.assertEqual(stats["机器人"]["count"], 1)
        self.assertEqual(stats["机器人"]["max_height"], 3)
        # 无标签行不得被聚合成一个假题材（否则会输出「跟风票」假结论）
        self.assertEqual(set(stats), {"AI", "机器人"})
        self.assertNotIn("600004", [code for item in stats.values() for code in item["codes"]])

    def test_unclassified_rows_contribute_height_only(self):
        rows = [
            {"code": "600001", "stage_n": 3},
            {"code": "600002", "stage_n": 2},
        ]
        pool_max, stats = pool_stats(rows)
        self.assertEqual(pool_max, 3)
        self.assertEqual(stats, {})

    def test_empty_pool(self):
        self.assertEqual(pool_stats([]), (0, {}))


class StructureForTests(unittest.TestCase):
    def test_pool_with_4_board_has_height_leader(self):
        rows = [{"code": "600002", "stage_n": 4, "stock_sector": "AI"}]
        pool_max, stats = pool_stats(rows)
        text = structure_for(rows[0], pool_max, stats)
        self.assertIn("有高度龙头（池内最高 4 板）", text)
        self.assertIn("题材〔AI〕独苗", text)

    def test_pool_with_3_board_is_medium(self):
        rows = [{"code": "600001", "stage_n": 3, "stock_sector": "AI"}]
        pool_max, stats = pool_stats(rows)
        text = structure_for(rows[0], pool_max, stats)
        self.assertIn("高度一般（池内最高 3 板，无 4 板以上高标）", text)

    def test_follow_leader_theme_without_high_board(self):
        # 同题材 2 只、题材内最高仅 2 板 → 跟风票，防一日游
        rows = [
            {"code": "600001", "stage_n": 2, "stock_sector": "AI"},
            {"code": "600002", "stage_n": 2, "stock_sector": "AI"},
        ]
        pool_max, stats = pool_stats(rows)
        text = structure_for(rows[0], pool_max, stats)
        self.assertIn("无高标（池内最高 2 板），一日游风险高", text)
        self.assertIn("题材〔AI〕2 只且最高 2 板 → 跟风票，防一日游", text)

    def test_theme_resonance_with_high_board(self):
        rows = [
            {"code": "600001", "stage_n": 4, "stock_sector": "AI"},
            {"code": "600002", "stage_n": 2, "stock_sector": "AI"},
        ]
        pool_max, stats = pool_stats(rows)
        text = structure_for(rows[0], pool_max, stats)
        self.assertIn("题材〔AI〕2 只共振（题材内最高 4 板）", text)

    def test_empty_pool_gives_neutral_text_without_raising(self):
        pool_max, stats = pool_stats([])
        text = structure_for({}, pool_max, stats)
        self.assertEqual(
            text,
            "无高标（池内最高 0 板），一日游风险高；无题材标签（未归类），不做题材共振判断",
        )

    def test_multiple_unlabeled_rows_are_not_reported_as_followers(self):
        # 回归：多只无题材标签的票不得被聚合成同一题材 → 不得输出「跟风票」
        rows = [
            {"code": "600001", "stage_n": 2},
            {"code": "600002", "stage_n": 2},
        ]
        pool_max, stats = pool_stats(rows)
        text = structure_for(rows[0], pool_max, stats)
        self.assertNotIn("跟风票", text)
        self.assertIn("无题材标签（未归类），不做题材共振判断", text)
        self.assertIn("无高标（池内最高 2 板），一日游风险高", text)

    def test_single_labeled_row_is_lone_theme_even_with_other_unlabeled(self):
        pool_max, stats = pool_stats([
            {"code": "600001", "stage_n": 2, "stock_sector": "AI"},
            {"code": "600002", "stage_n": 3},
        ])
        text = structure_for({"code": "600001", "stock_sector": "AI"}, pool_max, stats)
        self.assertIn("题材〔AI〕独苗", text)


class TimingTests(unittest.TestCase):
    def test_perfect_setup_is_limit_up(self):
        result = timing_for(stage=3, base_grade="A", open_gap_pct=4.0,
                            prev_amount=8e8, turnover_state="健康区")
        self.assertEqual(result["timing"], TIMING_LIMIT_UP)
        self.assertIn("打板价=涨停价", result["position"])
        self.assertIn("可打板", result["reasons"][0])
        self.assertIn("竞价 +4.00% 在 +2%~+7%", result["reasons"][0])
        self.assertIn("成交额 8.00亿 ≤ 10亿", result["reasons"][0])

    def test_weak_grade_vetoes_first(self):
        result = timing_for(stage=4, base_grade="C", open_gap_pct=4.0, prev_amount=5e8)
        self.assertEqual(result["timing"], TIMING_NO_CHASE)
        self.assertIn("基础档 C ≤ C", result["reasons"][0])

    def test_heavy_amount_vetoes(self):
        result = timing_for(stage=3, base_grade="A", open_gap_pct=4.0, prev_amount=25e8)
        self.assertEqual(result["timing"], TIMING_NO_CHASE)
        self.assertIn("昨日成交额 25.00亿 > 20亿（巨量）", result["reasons"][0])

    def test_near_one_word_gap_vetoes(self):
        result = timing_for(stage=3, base_grade="A", open_gap_pct=9.5, prev_amount=8e8)
        self.assertEqual(result["timing"], TIMING_NO_CHASE)
        self.assertIn("竞价高开 +9.50% > +9%，接近一字", result["reasons"][0])

    def test_low_height_falls_to_half_way(self):
        result = timing_for(stage=1, base_grade="B", open_gap_pct=4.0,
                            prev_amount=8e8, turnover_state="健康区")
        self.assertEqual(result["timing"], TIMING_HALF_WAY)
        self.assertIn("连板高度 1 < 2", result["reasons"][0])
        self.assertIn("等回踩均价/分时均线再进", result["position"])

    def test_missing_data_degrades_to_half_way(self):
        result = timing_for(stage=3, base_grade="B", open_gap_pct=None, prev_amount=None)
        self.assertEqual(result["timing"], TIMING_HALF_WAY)
        self.assertIn("昨日成交额缺失，无法确认量能，按半路等回踩", result["reasons"])
        self.assertIn("竞价涨幅缺失，无法确认强度，按半路等回踩", result["reasons"])

    def test_one_word_board_is_half_way(self):
        result = timing_for(stage=3, base_grade="A", open_gap_pct=4.0,
                            prev_amount=8e8, turnover_state="健康区", one_word=True)
        self.assertEqual(result["timing"], TIMING_HALF_WAY)
        self.assertIn("T-1 一字板，无有效分歧可打", result["reasons"])

    def test_dirty_numbers_do_not_raise(self):
        result = timing_for(stage="3", base_grade="A", open_gap_pct="x", prev_amount="")
        self.assertEqual(result["timing"], TIMING_HALF_WAY)
        self.assertIn("竞价涨幅缺失", " ".join(result["reasons"]))


class BonusItemTests(unittest.TestCase):
    ALL_OK = {
        "event_driver": "AI·主线",
        "open_gap_pct": 4.0,
        "prev_amount": 6e8,
        "turnover_pct": 10.0,
        "float_mcap_yi": 80.0,
        "stage_n": 2,
        "turnover_state": "健康区",
        "one_word": False,
    }

    def test_seven_items_all_pass(self):
        items = bonus_items(self.ALL_OK)
        self.assertEqual(len(items), 7)
        self.assertEqual([item["key"] for item in items], [
            "题材新鲜度", "竞价强度", "成交额健康", "昨日换手率适中",
            "连板位置", "流通市值适中", "分歧适中",
        ])
        self.assertTrue(all(item["ok"] for item in items))
        self.assertEqual(bonus_text(items), "7/7 ✓题材 ✓竞价 ✓量能 ✓换手 ✓连板 ✓市值 ✓分歧")
        self.assertIn("题材新鲜度加分（主线 属新题材/主线，题材有新鲜度）", bonus_reason_text(items))

    def test_empty_row_fails_every_item_with_readable_reason(self):
        items = bonus_items({})
        self.assertEqual(len(items), 7)
        self.assertFalse(any(item["ok"] for item in items))
        self.assertTrue(bonus_text(items).startswith("0/7 ✗"))
        reasons = bonus_reason_text(items)
        self.assertIn("竞价涨幅缺失", reasons)
        self.assertIn("成交额缺失", reasons)
        self.assertIn("换手率缺失", reasons)
        self.assertIn("流通市值缺失", reasons)
        self.assertIn("换手状态缺失", reasons)
        self.assertIn("0 板，不在 2~3 板最优区", reasons)

    def test_partial_hits_count_in_text(self):
        row = dict(self.ALL_OK, open_gap_pct=1.0, stage_n=5)
        items = bonus_items(row)
        self.assertEqual(sum(1 for item in items if item["ok"]), 5)
        self.assertIn("5/7", bonus_text(items))
        self.assertIn("高开 +1.00%，不在 +2%~+7%", bonus_reason_text(items))

    def test_empty_items_text(self):
        self.assertEqual(bonus_text([]), "-")
        self.assertEqual(bonus_reason_text([]), "")


class EmotionQuestionTests(unittest.TestCase):
    def test_numbers_in_verdict(self):
        answer = _emotion_question(PAIRS, 20261020)
        self.assertFalse(answer["missing"])
        self.assertEqual(len(answer["days"]), 3)
        self.assertEqual([day["date"] for day in answer["days"]],
                         [20261018, 20261019, 20261020])
        self.assertEqual(answer["verdict"],
                         "高度抬升（修复）、涨停家数回升（涨停 40→55 家，最高板 3→4 板）")
        self.assertEqual(answer["days"][-1]["limit_up"], 55)
        self.assertEqual(answer["days"][-1]["max_height"], 4)
        self.assertEqual(answer["days"][-1]["height_counts"], {})

    def test_future_rows_are_excluded_by_target_date(self):
        pairs = PAIRS + [(20261021, {"limit_up": 99, "max_height": 9})]
        answer = _emotion_question(pairs, 20261020)
        self.assertEqual([day["date"] for day in answer["days"]],
                         [20261018, 20261019, 20261020])
        self.assertIn("涨停 40→55 家", answer["verdict"])

    def test_decline_text(self):
        pairs = [
            (20261019, {"limit_up": 60, "max_height": 6}),
            (20261020, {"limit_up": 45, "max_height": 5}),
        ]
        answer = _emotion_question(pairs, 20261020)
        self.assertEqual(answer["verdict"],
                         "高度下降（退潮）、涨停家数回落（涨停 60→45 家，最高板 6→5 板）")

    def test_single_sample_degrades(self):
        answer = _emotion_question([(20261020, {"limit_up": 55, "max_height": 4})], 20261020)
        self.assertEqual(answer["verdict"], "仅 1 个交易日样本，不做情绪判断")
        self.assertEqual(len(answer["days"]), 1)

    def test_missing_data_degrades_without_raising(self):
        answer = _emotion_question([], 20261020)
        self.assertTrue(answer["missing"])
        self.assertEqual(answer["days"], [])
        self.assertEqual(answer["verdict"],
                         "market_stats.json 无目标日及之前的数据，本项不构成结论")

    def test_rows_without_fields_do_not_raise(self):
        answer = _emotion_question([(20261019, {}), (20261020, {"limit_up": "7"})], 20261020)
        self.assertEqual(answer["days"][0]["limit_up"], 0)
        self.assertEqual(answer["days"][1]["limit_up"], 7)
        self.assertEqual(answer["verdict"],
                         "高度维持、涨停家数回升（涨停 0→7 家，最高板 0→0 板）")


class CatalystQuestionTests(unittest.TestCase):
    def test_themes_sorted_by_candidate_count(self):
        candidates = [
            {"code": "600001", "stock_sector": "AI", "event_driver": "AI·主线"},
            {"code": "600002", "stock_sector": "AI", "event_driver": "AI·新题材"},
            {"code": "600003", "stock_sector": "机器人", "event_driver": "机器人·主线"},
            {"code": "600004", "error": "日线不足", "event_driver": "AI·主线"},
        ]
        answer = _catalyst_question(candidates)
        self.assertFalse(answer["missing"])
        self.assertEqual([item["theme"] for item in answer["themes"]], ["AI", "机器人"])
        self.assertEqual(answer["themes"][0]["codes"], ["600001", "600002"])
        self.assertEqual(answer["themes"][0]["drivers"], ["AI·主线", "AI·新题材"])
        self.assertEqual(answer["verdict"],
                         "2 个题材有事件标签，按候选集中度排序"
                         "（只是候选池内的题材分布，不代表全市场催化）")

    def test_no_event_data_degrades(self):
        answer = _catalyst_question([{"code": "600001", "stock_sector": "AI"}])
        self.assertTrue(answer["missing"])
        self.assertEqual(answer["verdict"], "事件驱动数据缺失，本项不构成结论")

    def test_empty_pool_degrades(self):
        answer = _catalyst_question([])
        self.assertEqual(answer["themes"], [])
        self.assertEqual(answer["verdict"], "候选池为空，无题材可汇总")


class FourQuestionsTests(unittest.TestCase):
    @staticmethod
    def _market() -> dict:
        return {"max_height": 4, "limit_up": 55, "leader_codes": ["600902"]}

    def test_all_four_answers_offline(self):
        candidates = [
            {"code": "600001", "stage_n": 2, "stock_sector": "AI", "event_driver": "AI·主线"},
            {"code": "600901", "stage_n": 3, "stock_sector": "机器人", "event_driver": "机器人·主线"},
        ]
        with mock.patch.object(engine, "_stats_pairs", return_value=list(PAIRS)):
            answers = four_questions(20261020, self._market(), candidates)
        self.assertEqual(set(answers), {"emotion", "catalyst", "backtest", "memory"})
        self.assertIn("涨停 40→55 家", answers["emotion"]["verdict"])
        self.assertEqual(answers["catalyst"]["themes"][0]["theme"], "AI")
        self.assertEqual(answers["backtest"]["bar_check"], "日线复核不可用（未传入股票表）")
        self.assertEqual(answers["backtest"]["height"], 4)
        # 题材记忆：600901 在 20261019 的 height_stocks 里上过板
        robot = next(item for item in answers["memory"]["themes"] if item["theme"] == "机器人")
        self.assertEqual(robot["hits"], 1)
        self.assertIn("老题材反复", robot["label"])

    def test_failed_item_degrades_to_verdict(self):
        with mock.patch.object(engine, "_stats_pairs", return_value=list(PAIRS)), \
                mock.patch.object(engine, "_emotion_question", side_effect=ValueError("boom")):
            answers = four_questions(20261020, self._market(), [])
        self.assertTrue(answers["emotion"]["missing"])
        self.assertEqual(answers["emotion"]["verdict"], "本项统计失败：ValueError: boom")


if __name__ == "__main__":
    unittest.main()
