"""장중 여론 펄스 오프라인 테스트 (DB·네트워크 없이)."""
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from kic_data import pulse
from kic_data.collectors import community, news

TODAY = date(2026, 10, 22)


class SummarizeTest(unittest.TestCase):
    def test_today_only_and_cafe_excluded(self):
        rs = ([{"source": "naver_news", "published_at": TODAY, "score": 0.5}] * 3
              + [{"source": "naver_blog", "published_at": TODAY, "score": -0.5}]
              + [{"source": "naver_blog", "published_at": TODAY, "score": None}]
              + [{"source": "naver_cafe", "published_at": TODAY, "score": 1.0}] * 5
              + [{"source": "naver_news", "published_at": TODAY - timedelta(days=i), "score": 0.0} for i in range(1, 26)])
        s = pulse.summarize(rs, TODAY)
        self.assertEqual((s["n_today"], s["n_news"], s["n_blog"]), (5, 3, 2))
        self.assertAlmostEqual(s["sentiment"], 0.25)  # (0.5×3 − 0.5) / 4, 점수 없는 글 제외
        self.assertEqual((s["n_positive"], s["n_negative"]), (3, 1))

    def test_too_few(self):
        s = pulse.summarize([{"source": "naver_news", "published_at": TODAY, "score": 1.0}], TODAY)
        self.assertIsNone(s["sentiment"])


class FetchTodayTest(unittest.TestCase):
    def test_filters(self):
        n = [{"title": "삼성전자 HBM 공급", "url": "n1", "published_at": TODAY, "summary": ""},
             {"title": "삼성전자 HBM 공급!", "url": "n2", "published_at": TODAY, "summary": ""},   # 재전송
             {"title": "코스피 마감 삼성전자", "url": "n3", "published_at": TODAY, "summary": ""},  # 시황
             {"title": "삼성전자 어제 기사", "url": "n4", "published_at": TODAY - timedelta(days=1), "summary": ""}]
        b = [{"title": "삼성전자 주가 전망", "url": "b1", "published_at": TODAY, "summary": ""},
             {"title": "삼성전자 대출 상담", "url": "b2", "published_at": TODAY, "summary": ""}]
        with mock.patch.object(news, "fetch_news_page", lambda *a, **k: (n, 5000)), \
             mock.patch.object(community, "fetch_page", lambda s, src, q, **k: (b if src == "naver_blog" else [], 70 if src == "naver_blog" else 30)):
            got, totals = pulse.fetch_today(None, "KR", "005930", "삼성전자", TODAY)
        self.assertEqual([r["url"] for r in got["naver_news"]], ["n1"])
        self.assertEqual([r["url"] for r in got["naver_blog"]], ["b1"])
        self.assertEqual(totals["naver_news"], ("삼성전자", 5000))
        self.assertEqual(totals["naver_cafe"], ("삼성전자 주가", 30))

    def test_new_since(self):
        totals = {"naver_news": ("q", 5010), "naver_blog": ("q", 69), "naver_cafe": ("q", None)}
        self.assertEqual(pulse.new_since({"naver_news": 5000, "naver_blog": 70}, totals), 10)  # 감소분은 0
        self.assertIsNone(pulse.new_since({}, totals))


class CacheTest(unittest.TestCase):
    def test_recent_pulse_skips_api(self):
        now = pulse.kst_now()
        last = {"captured_at": now - timedelta(minutes=2), "trade_date": now.date(), "sentiment": 0.1}
        with mock.patch.object(pulse.db, "last_pulse", lambda conn, cid: last), \
             mock.patch.object(pulse, "fetch_today", side_effect=AssertionError("API 호출하면 안 됨")):
            snap = pulse.refresh(None, None, 1, "KR", "005930", "삼성전자", max_age_min=5)
        self.assertTrue(snap["cached"])
        self.assertEqual(snap["sentiment"], 0.1)


if __name__ == "__main__":
    unittest.main()
