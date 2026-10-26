"""지수·수급·공시·관세청·검색관심도 파서 오프라인 테스트 (DB·네트워크 불필요)."""
import unittest
from datetime import date
from decimal import Decimal

import pandas as pd

from kic_data.collectors import customs, dart, indices, sec, trends
from kic_data.util import date_windows, drop_incomplete_today


class WindowTest(unittest.TestCase):
    def test_windows_cover_range_without_overlap(self):
        w = date_windows(date(2021, 1, 1), date(2026, 1, 1), 330)
        self.assertEqual(w[0][0], date(2021, 1, 1))
        self.assertEqual(w[-1][1], date(2026, 1, 1))
        for (a, b), (c, d) in zip(w, w[1:]):
            self.assertEqual((c - b).days, 1)  # 구간이 빈틈·겹침 없이 이어짐
        self.assertTrue(all((b - a).days + 1 <= 330 for a, b in w))

    def test_single_day(self):
        self.assertEqual(date_windows(date(2026, 1, 1), date(2026, 1, 1), 330), [(date(2026, 1, 1), date(2026, 1, 1))])


class IncompleteTodayTest(unittest.TestCase):
    ROWS = [{"trade_date": date(2026, 10, 20)}, {"trade_date": date(2026, 10, 21)}]

    def test_us_premarket_row_dropped(self):
        from datetime import datetime, timezone

        now = datetime(2026, 10, 21, 8, 48, tzinfo=timezone.utc)  # = 04:48 ET, 한국 17:48
        out = drop_incomplete_today(self.ROWS, "US", now)
        self.assertEqual([r["trade_date"] for r in out], [date(2026, 10, 20)])

    def test_us_after_close_kept(self):
        from datetime import datetime, timezone

        now = datetime(2026, 10, 21, 21, 0, tzinfo=timezone.utc)  # = 17:00 ET (마감 후)
        self.assertEqual(len(drop_incomplete_today(self.ROWS, "US", now)), 2)

    def test_kr_cutoff(self):
        from datetime import datetime, timezone

        before = datetime(2026, 10, 21, 5, 0, tzinfo=timezone.utc)   # 14:00 KST
        after = datetime(2026, 10, 21, 8, 48, tzinfo=timezone.utc)   # 17:48 KST
        self.assertEqual(len(drop_incomplete_today(self.ROWS, "KR", before)), 1)
        self.assertEqual(len(drop_incomplete_today(self.ROWS, "KR", after)), 2)


class IndexTest(unittest.TestCase):
    def test_rows_from_history(self):
        df = pd.DataFrame(
            {"Open": [1.0, 2.0], "High": [1.5, 2.5], "Low": [0.9, 1.9], "Close": [1.2, float("nan")], "Volume": [100, 0]},
            index=pd.to_datetime(["2026-10-20", "2026-10-21"]),
        )
        rows = indices.rows_from_history(df)
        self.assertEqual(len(rows), 1)  # 종가 없는 행 제외
        self.assertEqual(rows[0]["trade_date"], date(2026, 10, 20))
        self.assertEqual(rows[0]["volume"], 100)


class DartDisclosureTest(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(dart.classify_event("주요사항보고서(유상증자결정)"), "rights_issue")
        self.assertEqual(dart.classify_event("자기주식 취득 결정"), "buyback")
        self.assertEqual(dart.classify_event("연결재무제표기준영업(잠정)실적(공정공시)"), "earnings_prelim")
        self.assertEqual(dart.classify_event("사업보고서 (2025.12)"), "annual_report")
        self.assertIsNone(dart.classify_event("임원ㆍ주요주주특정증권등소유상황보고서"))

    def test_parse(self):
        payload = {"status": "000", "total_page": 2, "list": [
            {"rcept_no": "20261020000123", "report_nm": "주요사항보고서(자기주식취득결정)", "rcept_dt": "20261020", "flr_nm": "삼성전자"},
            {"rcept_no": "x", "report_nm": "날짜 이상", "rcept_dt": "2026", "flr_nm": "a"},
        ]}
        rows, pages = dart.parse_disclosures(payload, "B")
        self.assertEqual(pages, 2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rcept_dt"], "2026-10-20")
        self.assertEqual(rows[0]["event_type"], "buyback")
        self.assertEqual(rows[0]["category"], "B")
        self.assertIn("rcpNo=20261020000123", rows[0]["url"])

    def test_classify_new_keywords(self):
        cases = {
            "기업설명회(IR)개최(안내공시)": "ir_event",
            "최대주주등소유주식변동신고서": "major_holder_change",
            "조회공시요구(풍문또는보도)에대한답변(미확정)": "rumor_inquiry",
            "풍문또는보도에대한해명(미확정)": "rumor_clarification",
            "[기재정정]장래사업ㆍ경영계획(공정공시)": "guidance",
            "매출액또는손익구조30%(대규모법인은15%)이상변경": "earnings_change",
            "정기주주총회결과": "shareholders_meeting",
            "주식소각결정": "stock_retirement",
            "[기재정정]타법인주식및출자증권취득결정": "investment_acquisition",
        }
        for name, ev in cases.items():
            self.assertEqual(dart.classify_event(name), ev, name)
        # 기존 분류가 바뀌지 않았는지
        self.assertEqual(dart.classify_event("자기주식취득결정"), "buyback")
        self.assertEqual(dart.classify_event("현금ㆍ현물배당결정"), "dividend")

    def test_no_data_and_error(self):
        self.assertEqual(dart.parse_disclosures({"status": "013"}, "A"), ([], 0))
        with self.assertRaises(RuntimeError):
            dart.parse_disclosures({"status": "010"}, "A")


class SecFilingTest(unittest.TestCase):
    def test_parse_filings(self):
        payload = {"name": "NVIDIA CORP", "filings": {"recent": {
            "accessionNumber": ["0001-26-000001", "0001-26-000002", "0001-25-000009"],
            "filingDate": ["2026-10-15", "2026-10-10", "2025-01-01"],
            "form": ["8-K", "4", "10-K"],
            "primaryDocument": ["a.htm", "b.xml", "c.htm"],
            "items": ["2.02,9.01", "", ""],
        }}}
        rows = sec.parse_filings(payload, "0001045810", date(2026, 1, 1))
        self.assertEqual(len(rows), 1)  # Form 4 와 기간 밖 10-K 제외
        self.assertEqual(rows[0]["event_type"], "earnings")
        self.assertEqual(rows[0]["url"], "https://www.sec.gov/Archives/edgar/data/1045810/000126000001/a.htm")

    def test_classify(self):
        self.assertEqual(sec.classify_filing("8-K", "5.02"), "officer_change")
        self.assertEqual(sec.classify_filing("20-F", ""), "annual_report")
        self.assertIsNone(sec.classify_filing("8-K", "9.01"))


class CustomsTest(unittest.TestCase):
    XML = """<response><header><resultCode>00</resultCode></header><body><items>
      <item><year>2026.07</year><expDlr>12,345,678</expDlr><impDlr>2,000</impDlr></item>
      <item><year>총계</year><expDlr>99</expDlr><impDlr>99</impDlr></item>
    </items></body></response>"""

    def test_parse(self):
        rows = customs.parse_trade_xml(self.XML)
        self.assertEqual(rows, [(date(2026, 7, 1), Decimal("12345678"), Decimal("2000"))])

    def test_error(self):
        with self.assertRaises(RuntimeError):
            customs.parse_trade_xml("<response><header><resultCode>30</resultCode><resultMsg>키 오류</resultMsg></header></response>")


class TrendsTest(unittest.TestCase):
    def test_parse_and_body(self):
        payload = {"results": [{"title": "KR:005930", "data": [{"period": "2026-10-20", "ratio": 55.5}]}]}
        self.assertEqual(trends.parse_trend(payload), {"KR:005930": [(date(2026, 10, 20), 55.5)]})
        body = trends.build_body(date(2026, 10, 1), date(2026, 10, 20), [("A", ["가"])])
        self.assertEqual(body["timeUnit"], "date")
        self.assertEqual(body["keywordGroups"][0]["keywords"], ["가"])

    def test_error_and_batches(self):
        with self.assertRaises(RuntimeError):
            trends.parse_trend({"errorCode": "024", "errorMessage": "x"})
        self.assertEqual([len(b) for b in trends.batches(list(range(10)), 4)], [4, 4, 2])

    def test_endpoint_prefers_hub(self):
        from unittest import mock

        with mock.patch.dict("os.environ", {"NCP_APIHUB_KEY_ID": "a", "NCP_APIHUB_KEY": "b",
                                            "NAVER_CLIENT_ID": "x", "NAVER_CLIENT_SECRET": "y"}):
            url, h = trends.endpoint()
        self.assertEqual(url, trends.HUB_URL)
        self.assertEqual(h["X-NCP-APIGW-API-KEY-ID"], "a")
        with mock.patch.dict("os.environ", {"NCP_APIHUB_KEY_ID": "", "NCP_APIHUB_KEY": "",
                                            "NAVER_CLIENT_ID": "x", "NAVER_CLIENT_SECRET": "y"}):
            url, h = trends.endpoint()
        self.assertEqual(url, trends.URL)

    def test_stock_keywords_and_relative(self):
        self.assertEqual(trends.stock_keywords("엔비디아"), ["엔비디아 주가", "엔비디아 주식"])
        rows = [(date(2026, 10, 1), 10, 50), (date(2026, 10, 2), 5, 0), (date(2026, 10, 3), None, 40)]
        self.assertEqual(trends.relative(rows), [(date(2026, 10, 1), 0.2)])


class AttentionTest(unittest.TestCase):
    def test_search_spike(self):
        from datetime import timedelta
        from kic_data import features

        today = date(2026, 10, 21)
        search = [(today - timedelta(days=100 - i), 1.0) for i in range(93)] + \
                 [(today - timedelta(days=7 - i), 3.0) for i in range(7)]
        rows = {r["feature"]: r["value"] for r in features.attention_rows({"search": search, "today": today})}
        self.assertAlmostEqual(rows["search_attention_7d_vs_90d"], 3.0)
        self.assertAlmostEqual(rows["search_attention_level_7d"], 3.0)

    def test_mentions_from_totals(self):
        from datetime import timedelta
        from kic_data import features

        today = date(2026, 10, 21)
        # 누적 total: 하루 100건씩 늘다가 오늘 500건 증가
        news = [(today - timedelta(days=20 - i), 1000 + 100 * i) for i in range(20)] + [(today, 1000 + 100 * 19 + 500)]
        blog = [(d, t // 10) for d, t in news]
        rows = {r["feature"]: r for r in features.attention_rows(
            {"today": today, "mentions": {"naver_news": news, "naver_blog": blog}})}
        self.assertAlmostEqual(rows["mentions_1d_vs_30d"]["value"], 5.0)
        self.assertGreater(rows["news_7d_vs_30d"]["value"], 1)
        self.assertIsNotNone(rows["news_per_day_7d"]["value"])

    def test_increments_gap_and_drop(self):
        from kic_data import features

        s = [(date(2026, 10, 1), 100), (date(2026, 10, 3), 140), (date(2026, 10, 4), 130)]
        self.assertEqual(features.daily_increments(s), [(date(2026, 10, 3), 20.0), (date(2026, 10, 4), 0)])

    def test_missing(self):
        from kic_data import features

        rows = {r["feature"]: r for r in features.attention_rows(None)}
        self.assertIsNone(rows["search_attention_7d_vs_90d"]["value"])
        self.assertIsNone(rows["mentions_1d_vs_30d"]["value"])
        self.assertIn("누적 0일", rows["mentions_1d_vs_30d"]["note"])


if __name__ == "__main__":
    unittest.main()
