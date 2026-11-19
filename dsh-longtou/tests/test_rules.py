from __future__ import annotations

import unittest

from longtou.rules import (
    adjusted_twenty_day_max,
    auction_state,
    cap_grade,
    cap_tier,
    downgrade_grade,
    gap_adjustment,
    grade_for_score,
    limit_down_intensity,
    one_word_run,
    sixty_day_max,
    stage_cap,
    turnover_state,
    upgrade_grade,
)
from longtou.tdx import Bar


def bar(date: int, close: float, low: float | None = None, amount: float = 100.0) -> Bar:
    return Bar(
        date=date,
        open=close,
        high=close,
        low=close if low is None else low,
        close=close,
        amount=amount,
        volume=1000.0,
    )


class CapTierTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(cap_tier(3000).name, "超大盘")
        self.assertEqual(cap_tier(2000).name, "超大盘")
        self.assertEqual(cap_tier(1999).name, "大盘股")
        self.assertEqual(cap_tier(500).name, "大盘股")
        self.assertEqual(cap_tier(100).name, "中盘股")
        self.assertEqual(cap_tier(30).name, "小盘股")
        self.assertEqual(cap_tier(29.9).name, "微盘股")

    def test_auction_gate(self):
        tier = cap_tier(80)  # 小盘股: 8-12
        self.assertEqual(auction_state(7.99, tier), "不通过")
        self.assertEqual(auction_state(8.0, tier), "通过")
        self.assertEqual(auction_state(12.1, tier), "通过但过热")

    def test_turnover_state(self):
        tier = cap_tier(80)  # healthy 15-25, death 35
        self.assertEqual(turnover_state(10, tier), "缩量")
        self.assertEqual(turnover_state(15, tier), "健康区")
        self.assertEqual(turnover_state(25, tier), "灰色区")
        self.assertEqual(turnover_state(35, tier), "死亡换手")


class GradeTests(unittest.TestCase):
    def test_grades(self):
        self.assertEqual(grade_for_score(80), "A")
        self.assertEqual(grade_for_score(65), "B")
        self.assertEqual(grade_for_score(50), "C")
        self.assertEqual(grade_for_score(49.9), "D")
        self.assertEqual(downgrade_grade("A"), "B")
        self.assertEqual(downgrade_grade("B", 2), "D")
        self.assertEqual(upgrade_grade("C"), "B")
        self.assertEqual(upgrade_grade("A"), "A")
        self.assertEqual(cap_grade("A", "B"), "B")
        self.assertEqual(gap_adjustment(-1.5), -1)
        self.assertEqual(gap_adjustment(-1.0), 0)
        self.assertEqual(gap_adjustment(0.0), 0)
        self.assertEqual(gap_adjustment(1.0), 0)
        self.assertEqual(gap_adjustment(2.0), 1)
        self.assertEqual(gap_adjustment(4.0), 1)
        self.assertEqual(gap_adjustment(4.1), -1)
        self.assertEqual(stage_cap(2), "B")
        self.assertEqual(stage_cap(3), "A")
        self.assertEqual(stage_cap(4), "B")
        # 高阶段（>=5）沿用 stage 4 的封顶，不再一律压到 C
        self.assertEqual(stage_cap(5), "B")
        self.assertEqual(stage_cap(6), "B")
        self.assertEqual(stage_cap(7), "B")

    def test_limit_down_intensity(self):
        self.assertEqual(limit_down_intensity(0, 0, 0, 0), 0)
        self.assertEqual(limit_down_intensity(30, 6, 3, 5), 100)


class WindowTests(unittest.TestCase):
    def test_sixty_day_max(self):
        bars = [bar(20260101 + i, 10.0, amount=float(i + 1)) for i in range(70)]
        result = sixty_day_max(bars, 69)
        self.assertTrue(result.is_latest)
        self.assertEqual(result.amount, 70.0)
        self.assertEqual(result.lookback, 60)

    def test_water_day_half_weight(self):
        # Day 1: 地天板 (low 9, close 11), amount 100 -> adjusted 50.
        # Day 2: normal, amount 80 -> becomes the adjusted max.
        bars = [
            bar(20260101, 10.0, amount=50.0),
            bar(20260102, 11.0, low=9.0, amount=100.0),
            bar(20260103, 11.5, amount=80.0),
        ]
        result = adjusted_twenty_day_max(bars, 2, "000001")
        self.assertEqual(result.amount, 80.0)
        self.assertEqual(result.raw_amount, 80.0)
        self.assertEqual(result.water_days, 1)

    def test_one_word_run(self):
        # three consecutive 10% limit-ups; first two are one-word, third opens.
        bars = [
            bar(20260101, 10.0),
            bar(20260102, 11.0, low=11.0),
            bar(20260103, 12.1, low=12.1),
            bar(20260104, 13.31, low=12.5),
        ]
        run = one_word_run(bars, "000001", 3)
        self.assertEqual(run.run_length, 3)
        self.assertEqual(run.one_word_days, 2)
        self.assertAlmostEqual(run.ratio, 2 / 3)


if __name__ == "__main__":
    unittest.main()
