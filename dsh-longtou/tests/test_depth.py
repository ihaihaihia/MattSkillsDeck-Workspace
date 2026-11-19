"""longtou.depth 封单额 / 封流比纯函数单测。

全离线：只构造内存里的竞价快照、五档记录对象，不发任何网络请求、不读 TDX 本地数据。
覆盖点（对应复审必修缺口）：
- 历史日期服务端只回 14:57–15:00 收盘竞价序列 → 必须「无值」而不是拿 14:5x 冒充 09:25；
- 未匹配量为 None / 价格为 0 → 「无值」并给出可读原因；
- 正常 09:25 快照 → 封单额 = 手数 × 100 × 价；
- 五档买一价 × 买一量 × 100；深度为空 → 「无值」；
- 封流比在流通市值为 0 / None / 脏字符串时不得除零抛异常。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from longtou.depth import (
    auction_seal_from_series,
    is_trading_time,
    realtime_seal_from_depth,
    seal_flow_ratio,
)

_CN = timezone(timedelta(hours=8), "Asia/Shanghai")


class Point:
    """最小化 AuctionPoint 替身：time_seconds / time_label / price / unmatched_volume。"""

    def __init__(self, time_label: str = "", price: float = 0.0, lots=None, seconds=None):
        self.time_label = time_label
        self.price = price
        self.unmatched_volume = lots
        if seconds is not None:
            self.time_seconds = seconds


class Series:
    """最小化 AuctionSeries 替身。"""

    def __init__(self, points):
        self.points = points


class Bid:
    def __init__(self, price: float, volume: int):
        self.price = price
        self.volume = volume


class Record:
    def __init__(self, code: str, buy_levels):
        self.code = code
        self.buy_levels = buy_levels


class Page:
    def __init__(self, records):
        self.records = records


class AuctionSealTests(unittest.TestCase):
    """口径①：竞价封单额。"""

    def test_close_auction_series_is_unavailable_not_fabricated(self):
        # 历史日期实测返回 14:57–15:00 收盘集合竞价，不得当成 09:25 封单
        series = Series([
            Point("14:57:00", price=10.0, lots=9999, seconds=14 * 3600 + 57 * 60),
            Point("15:00:00", price=10.0, lots=8888, seconds=15 * 3600),
        ])
        value, note = auction_seal_from_series(series)
        self.assertIsNone(value)
        self.assertIn("该日无 09:25 竞价序列", note)
        self.assertIn("14:57:00–15:00:00", note)

    def test_empty_series_is_unavailable(self):
        value, note = auction_seal_from_series(Series([]))
        self.assertIsNone(value)
        self.assertEqual(note, "无竞价快照")

    def test_none_lots_is_unavailable(self):
        series = Series([Point("09:25:00", price=10.0, lots=None, seconds=9 * 3600 + 25 * 60)])
        value, note = auction_seal_from_series(series)
        self.assertIsNone(value)
        self.assertEqual(note, "快照无未匹配量字段")

    def test_zero_price_is_unavailable(self):
        series = Series([Point("09:25:00", price=0.0, lots=800, seconds=9 * 3600 + 25 * 60)])
        value, note = auction_seal_from_series(series)
        self.assertIsNone(value)
        self.assertIn("未匹配量 800 手", note)
        self.assertIn("价 0.0", note)

    def test_normal_point_multiplies_lots_100_and_price(self):
        # 800 手 × 100 股/手 × 8.5 元 = 680000 元
        series = Series([Point("09:25:00", price=8.5, lots=800, seconds=9 * 3600 + 25 * 60)])
        value, note = auction_seal_from_series(series)
        self.assertEqual(value, 680000.0)
        self.assertEqual(note, "09:25:00 未匹配 800 手 × 8.50")

    def test_picks_latest_point_inside_window(self):
        series = Series([
            Point("09:18:00", price=10.0, lots=100, seconds=9 * 3600 + 18 * 60),
            Point("09:24:58", price=10.5, lots=300, seconds=9 * 3600 + 24 * 60 + 58),
        ])
        value, note = auction_seal_from_series(series)
        self.assertEqual(value, 300 * 100 * 10.5)  # 315000.0
        self.assertIn("09:24:58", note)

    def test_window_boundary_excludes_after_092559(self):
        # 09:26:00 已过 09:25:59，不属于开盘集合竞价窗口，必须被丢弃
        series = Series([
            Point("", price=9.0, lots=5000, seconds=9 * 3600 + 26 * 60),
            Point("", price=20.0, lots=10, seconds=9 * 3600 + 25 * 60),
        ])
        value, note = auction_seal_from_series(series)
        self.assertEqual(value, 10 * 100 * 20.0)  # 20000.0
        self.assertIn("未匹配 10 手", note)

    def test_time_seconds_preferred_over_label(self):
        # 有 time_seconds 时以它为准：label 写 14:57 但秒数落在 09:25 窗口
        series = Series([Point("14:57:00", price=12.0, lots=50, seconds=9 * 3600 + 25 * 60)])
        value, note = auction_seal_from_series(series)
        self.assertEqual(value, 50 * 100 * 12.0)  # 60000.0
        self.assertIn("14:57:00", note)


class RealtimeSealTests(unittest.TestCase):
    """口径②：五档实时封单额。"""

    def test_bid_one_price_times_lots(self):
        page = Page([Record("600001", [Bid(10.5, 3000), Bid(10.4, 500)])])
        value, note = realtime_seal_from_depth(page, "600001")
        self.assertEqual(value, 10.5 * 3000 * 100.0)  # 3150000.0
        self.assertEqual(note, "买一 10.50 × 3000 手")

    def test_matches_requested_code_among_multiple_records(self):
        page = Page([
            Record("600001", [Bid(5.0, 100)]),
            Record("000002", [Bid(20.0, 700)]),
        ])
        value, note = realtime_seal_from_depth(page, "000002")
        self.assertEqual(value, 20.0 * 700 * 100.0)  # 1400000.0
        self.assertIn("买一 20.00", note)

    def test_empty_depth_is_unavailable(self):
        value, note = realtime_seal_from_depth(Page([]), "600001")
        self.assertIsNone(value)
        self.assertEqual(note, "五档无返回记录")

    def test_no_buy_levels_is_unavailable(self):
        value, note = realtime_seal_from_depth(Page([Record("600001", [])]), "600001")
        self.assertIsNone(value)
        self.assertEqual(note, "五档无买盘")

    def test_zero_bid_volume_is_unavailable(self):
        page = Page([Record("600001", [Bid(10.0, 0)])])
        value, note = realtime_seal_from_depth(page, "600001")
        self.assertIsNone(value)
        self.assertEqual(note, "买一档无量")


class SealFlowRatioTests(unittest.TestCase):
    """封流比：封单额(元) ÷ 流通市值(元) × 100%。"""

    def test_ratio_value(self):
        # 5000 万元 ÷ 50 亿元 = 1.0%
        self.assertAlmostEqual(seal_flow_ratio(5e7, 50), 1.0, places=9)
        # 3000 万元 ÷ 30 亿元 = 1.0%；再取 2.5 亿元 ÷ 50 亿元 = 5.0%
        self.assertAlmostEqual(seal_flow_ratio(2.5e8, 50.0), 5.0, places=9)

    def test_zero_mcap_returns_none_without_zero_division(self):
        self.assertIsNone(seal_flow_ratio(5e7, 0))
        self.assertIsNone(seal_flow_ratio(5e7, 0.0))

    def test_none_or_dirty_mcap_returns_none(self):
        self.assertIsNone(seal_flow_ratio(5e7, None))
        self.assertIsNone(seal_flow_ratio(5e7, "abc"))

    def test_none_seal_returns_none(self):
        self.assertIsNone(seal_flow_ratio(None, 50))


class TradingTimeTests(unittest.TestCase):
    """盘中判定（显式传入 now，结果与时区/运行时刻无关）。"""

    def test_weekday_sessions(self):
        self.assertTrue(is_trading_time(datetime(2026, 10, 21, 9, 20, tzinfo=_CN)))
        self.assertTrue(is_trading_time(datetime(2026, 10, 21, 10, 0, tzinfo=_CN)))
        self.assertFalse(is_trading_time(datetime(2026, 10, 21, 12, 0, tzinfo=_CN)))
        self.assertTrue(is_trading_time(datetime(2026, 10, 21, 14, 30, tzinfo=_CN)))

    def test_weekend_never_trading(self):
        self.assertFalse(is_trading_time(datetime(2026, 10, 17, 10, 0, tzinfo=_CN)))


if __name__ == "__main__":
    unittest.main()
