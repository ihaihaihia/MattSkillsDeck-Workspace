"""longtou.lhb 的单测：解析逻辑用内联样本，T+1 再板率用本地真实 .day。

除 ``t1_relimit_rate`` 外全部离线：龙虎榜解析与缓存翻页都用内存 payload，
不产生任何网络请求。
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from longtou import lhb

# 内联样本：模拟东财 RPT_DAILYBILLBOARD_DETAILSNEW 第 1 页（pages=2）
PAGE1 = {
    "version": "abc",
    "success": True,
    "message": "ok",
    "result": {
        "count": 3,
        "pages": 2,
        "data": [
            {
                "SECURITY_CODE": "600267",
                "SECURITY_NAME_ABBR": "海正药业",
                "TRADE_DATE": "2026-10-20 00:00:00",
                "BILLBOARD_NET_AMT": 88000000.0,
                "BILLBOARD_BUY_AMT": 120000000.0,
                "BILLBOARD_SELL_AMT": 32000000.0,
                "BILLBOARD_DEAL_AMT": 152000000.0,
                "ACCUM_AMOUNT": 980000000.0,
                "EXPLANATION": "日涨幅偏离值达到7%的前5只证券",
                "CHANGE_RATE": 10.02,
                "CLOSE_PRICE": 12.07,
                "TURNOVERRATE": 5.5,
                "FREE_MARKET_CAP": 9700000000.0,
            },
            # 同一只票第二个上榜原因：金额更小，应合并且不覆盖主金额
            {
                "SECURITY_CODE": "600267",
                "SECURITY_NAME_ABBR": "海正药业",
                "TRADE_DATE": "2026-10-20 00:00:00",
                "BILLBOARD_NET_AMT": 12000000.0,
                "BILLBOARD_BUY_AMT": 30000000.0,
                "BILLBOARD_SELL_AMT": 18000000.0,
                "EXPLANATION": "连续三个交易日内涨幅偏离值累计达到20%",
                "CHANGE_RATE": 10.02,
                "CLOSE_PRICE": 12.07,
            },
            # 字段缺失容错：无净买额/无名称/无上榜原因
            {
                "SECURITY_CODE": "000056",
                "TRADE_DATE": "2026-10-20 00:00:00",
                "CLOSE_PRICE": 2.09,
            },
        ],
    },
}

# 第 2 页：一只新票 + 复用 600267（验证跨页去重与原因合并）
PAGE2 = {
    "success": True,
    "result": {
        "count": 3,
        "pages": 2,
        "data": [
            {
                "SECURITY_CODE": "300750",
                "SECURITY_NAME_ABBR": "宁德时代",
                "TRADE_DATE": "2026-10-20 00:00:00",
                "BILLBOARD_NET_AMT": -5000000.0,
                "BILLBOARD_BUY_AMT": 10000000.0,
                "BILLBOARD_SELL_AMT": 15000000.0,
                "EXPLANATION": "日跌幅偏离值达到7%的前5只证券",
            },
            {
                "SECURITY_CODE": "600267",
                "TRADE_DATE": "2026-10-20 00:00:00",
                "BILLBOARD_NET_AMT": 1000000.0,
                "EXPLANATION": "换手率达到20%的前5只证券",
            },
        ],
    },
}


def empty_payloads() -> list[dict]:
    """各种「空 result」形状。"""
    return [
        {"success": True, "result": None},
        {"success": True, "result": {}},
        {"success": True, "result": {"data": None}},
        {"success": True},
        {},
    ]


class BillboardParseTests(unittest.TestCase):
    def test_single_page_fields(self):
        stocks = lhb.parse_billboard_payload(PAGE1)
        self.assertEqual(len(stocks), 2)
        by_code = {item["code"]: item for item in stocks}
        self.assertIn("600267", by_code)
        self.assertIn("000056", by_code)

        haizheng = by_code["600267"]
        self.assertEqual(haizheng["name"], "海正药业")
        self.assertEqual(haizheng["date"], "20261020")
        self.assertEqual(haizheng["net_amt"], 88000000.0)
        self.assertEqual(haizheng["buy_amt"], 120000000.0)
        self.assertEqual(haizheng["sell_amt"], 32000000.0)
        self.assertEqual(haizheng["rows"], 2)
        self.assertEqual(len(haizheng["explanations"]), 2)
        self.assertIn("；", haizheng["reason"])
        self.assertAlmostEqual(haizheng["free_mcap_yi"], 97.0)

    def test_missing_fields_tolerated(self):
        stocks = {item["code"]: item for item in lhb.parse_billboard_payload(PAGE1)}
        st = stocks["000056"]
        self.assertEqual(st["name"], "000056")  # 无名称回落代码
        self.assertEqual(st["net_amt"], 0.0)
        self.assertEqual(st["buy_amt"], 0.0)
        self.assertEqual(st["reason"], "")
        self.assertEqual(st["explanations"], [])

    def test_empty_result_returns_empty_list(self):
        for payload in empty_payloads():
            self.assertEqual(lhb.parse_billboard_payload(payload), [])

    def test_cross_page_merge_and_dedupe(self):
        stocks = lhb.parse_billboard_pages([PAGE1, PAGE2])
        self.assertEqual(len(stocks), 3)
        self.assertEqual([item["code"] for item in stocks], ["600267", "000056", "300750"])
        haizheng = stocks[0]
        self.assertEqual(haizheng["rows"], 3)
        self.assertEqual(len(haizheng["explanations"]), 3)
        # 金额仍取绝对值最大的那行
        self.assertEqual(haizheng["net_amt"], 88000000.0)
        self.assertEqual(stocks[2]["net_amt"], -5000000.0)


class BillboardCacheTests(unittest.TestCase):
    def test_fetch_pages_and_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            fetch = mock.Mock(side_effect=[PAGE1, PAGE2])
            with mock.patch.object(lhb, "CACHE_DIR", cache_dir), mock.patch.object(
                lhb, "_fetch_json", fetch
            ):
                first = lhb.fetch_billboard("20261020")
                self.assertEqual(len(first), 3)
                self.assertEqual(fetch.call_count, 2)  # 按 result.pages=2 翻页

                cache_file = cache_dir / "lhb_20261020.json"
                self.assertTrue(cache_file.is_file())
                saved = json.loads(cache_file.read_text(encoding="utf-8"))
                self.assertEqual(saved["date"], "20261020")
                self.assertEqual(saved["count"], 3)

                # 第二次（连日期格式都换成带横线）必须读缓存，不再请求网络
                second = lhb.fetch_billboard("2026-10-20")
                self.assertEqual(fetch.call_count, 2)
                self.assertEqual([item["code"] for item in second], [item["code"] for item in first])

    def test_bad_cache_is_ignored(self):
        single_page = json.loads(json.dumps(PAGE1))
        single_page["result"]["pages"] = 1
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            bad = cache_dir / "lhb_20261020.json"
            bad.write_text("{not json", encoding="utf-8")
            fetch = mock.Mock(return_value=single_page)
            with mock.patch.object(lhb, "CACHE_DIR", cache_dir), mock.patch.object(
                lhb, "_fetch_json", fetch
            ):
                stocks = lhb.fetch_billboard("20261020")
                self.assertEqual(len(stocks), 2)
                self.assertEqual(fetch.call_count, 1)  # 坏缓存被当作无缓存，重新联网


class DetailParseTests(unittest.TestCase):
    def test_detail_rows(self):
        payload = {
            "success": True,
            "result": {
                "count": 2,
                "pages": 1,
                "data": [
                    {
                        "OPERATEDEPT_NAME": "华泰证券股份有限公司深圳益田路营业部",
                        "BUY": 1000000.0,
                        "SELL": 200000.0,
                        "NET": 800000.0,
                        "EXPLANATION": "日涨幅偏离值达到7%的前5只证券",
                    },
                    {"OPERATEDEPT_NAME": "", "BUY": 500000.0},
                ],
            },
        }
        seats = lhb._parse_detail_rows(payload, "buy")
        self.assertEqual(len(seats), 2)
        self.assertEqual(seats[0]["dept"], "华泰证券股份有限公司深圳益田路营业部")
        self.assertEqual(seats[0]["rank"], 1)
        self.assertEqual(seats[0]["net_amt"], 800000.0)
        self.assertEqual(seats[1]["dept"], "")
        # NET 缺失时用 买入-卖出 兜底
        self.assertEqual(seats[1]["net_amt"], 500000.0)
        self.assertEqual(lhb._parse_detail_rows({"result": None}, "buy"), [])

    def test_detail_cache_roundtrip(self):
        page = {
            "result": {
                "pages": 1,
                "data": [{"OPERATEDEPT_NAME": "某营业部", "BUY": 100.0, "SELL": 40.0}],
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            fetch = mock.Mock(side_effect=[page, page])  # 买五 + 卖五各一次
            with mock.patch.object(lhb, "CACHE_DIR", Path(tmp)), mock.patch.object(
                lhb, "_fetch_json", fetch
            ):
                first = lhb.fetch_billboard_detail("000056", "20261020")
                second = lhb.fetch_billboard_detail("000056", "2026-10-20")
                self.assertEqual(fetch.call_count, 2)  # 第二次全走缓存
                self.assertEqual(first, second)  # 读缓存不得多出 version/fetched_at
                self.assertNotIn("version", second)
                self.assertEqual(first["seats"], 2)
                self.assertEqual(first["net_total"], 60.0)


class RelimitRateTests(unittest.TestCase):
    """T+1 再板率：用本地真实 .day，挑一只有足够涨停历史的票（600267）。"""

    CODE = "600267"

    @classmethod
    def setUpClass(cls):
        cls.stock = lhb.find_stock_file(cls.CODE)
        if not cls.stock.path.is_file():
            raise unittest.SkipTest("本地 TDX 缺少 .day 数据")

    def test_structure_and_samples(self):
        result = lhb.t1_relimit_rate(self.CODE)
        for key in ("code", "samples", "relimit", "rate", "threshold_pct", "note"):
            self.assertIn(key, result)
        self.assertEqual(result["code"], self.CODE)
        self.assertGreater(result["samples"], 0)
        self.assertLessEqual(result["relimit"], result["samples"])
        self.assertIn(result["threshold_pct"], (9.8, 19.8))
        if result["samples"] >= lhb.RELIMIT_MIN_SAMPLES:
            self.assertFalse(result["samples_insufficient"])
            self.assertIsNotNone(result["rate"])
            self.assertAlmostEqual(
                result["rate"], round(result["relimit"] / result["samples"], 4)
            )
            self.assertTrue(0.0 <= result["rate"] <= 1.0)

    def test_insufficient_samples_returns_none_rate(self):
        # lookback=1 时窗口只有 1 根 K 线，必然没有「涨停 + 次日」样本
        result = lhb.t1_relimit_rate(self.CODE, lookback=1)
        self.assertEqual(result["samples"], 0)
        self.assertIsNone(result["rate"])
        self.assertTrue(result["samples_insufficient"])
        self.assertIn("样本太少", result["note"])

    def test_find_stock_file(self):
        self.assertEqual(self.stock.market, "sh")
        self.assertEqual(lhb.find_stock_file("sh600267").full_code, "sh600267")
        with self.assertRaises(ValueError):
            lhb.find_stock_file("not-a-code")


if __name__ == "__main__":
    unittest.main()
