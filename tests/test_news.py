"""네이버 뉴스 파서 오프라인 테스트."""
import unittest
from datetime import date

from kic_data.collectors import news

SAMPLE = {
    "items": [
        {
            "title": "<b>삼성전자</b>, HBM4 &quot;양산&quot; 임박",
            "originallink": "https://example.com/a",
            "link": "https://n.news.naver.com/a",
            "description": "<b>삼성전자</b>가 &amp; 반도체 투자를 확대한다.",
            "pubDate": "Tue, 21 Oct 2026 16:30:00 +0900",
        },
        {
            "title": "무관한 기사",
            "originallink": "",
            "link": "https://n.news.naver.com/b",
            "description": "다른 내용",
            "pubDate": "Mon, 20 Oct 2026 09:00:00 +0900",
        },
        {"title": "링크 없음", "originallink": "", "link": "", "description": "", "pubDate": ""},
    ]
}


class NewsParseTest(unittest.TestCase):
    def test_parse_cleans_and_skips_missing_url(self):
        rows = news.parse_news(SAMPLE)
        self.assertEqual(len(rows), 2)  # url 없는 항목 제외
        self.assertEqual(rows[0]["title"], '삼성전자, HBM4 "양산" 임박')
        self.assertEqual(rows[0]["summary"], "삼성전자가 & 반도체 투자를 확대한다.")
        self.assertEqual(rows[0]["url"], "https://example.com/a")  # originallink 우선
        self.assertEqual(rows[0]["published_at"], date(2026, 10, 21))
        self.assertEqual(rows[1]["url"], "https://n.news.naver.com/b")  # 대체 링크

    def test_bad_date_becomes_none(self):
        rows = news.parse_news({"items": [{"title": "t", "link": "https://x", "pubDate": "??"}]})
        self.assertIsNone(rows[0]["published_at"])

    def test_error_payload_raises(self):
        with self.assertRaises(RuntimeError):
            news.parse_news({"errorCode": "024", "errorMessage": "Authentication failed"})

    def test_matches_filter(self):
        rows = news.parse_news(SAMPLE)
        self.assertTrue(news.matches(rows[0], ("삼성전자",)))
        self.assertFalse(news.matches(rows[1], ("삼성전자",)))
        self.assertTrue(news.matches({"title": "NVIDIA up", "summary": ""}, ("엔비디아", "Nvidia")))
        # 요약에서만 언급된 기사는 제외 (제목 기준)
        self.assertFalse(news.matches({"title": "코스피 하락", "summary": "AMD 약세"}, ("AMD",)))

    def test_market_wrap(self):
        self.assertTrue(news.is_market_wrap("뉴욕증시, AI 투자 우려에 혼조"))
        self.assertTrue(news.is_market_wrap("코스피 5700선 회복"))
        self.assertFalse(news.is_market_wrap("마이크론 대만 노조 22일 파업"))

    def test_terms_for(self):
        self.assertEqual(news.terms_for("US", "NVDA", "NVIDIA")[0], "엔비디아")
        self.assertEqual(news.terms_for("KR", "005930", "삼성전자"), ("삼성전자", ("삼성전자",)))


class CollectTest(unittest.TestCase):
    def test_topics_dedupe_and_since(self):
        from unittest import mock

        def fake(session, q, display=100, start=1, sort="date"):
            base = [
                {"title": "삼성전자 실적 발표", "url": "u1", "published_at": date(2026, 10, 20), "summary": ""},
                {"title": "삼성전자 실적 발표", "url": "u2", "published_at": date(2026, 10, 20), "summary": ""},  # 재전송
                {"title": "삼성전자 옛 기사", "url": "u3", "published_at": date(2026, 8, 1), "summary": ""},  # 기간 밖
                {"title": f"삼성전자 {q}", "url": f"x-{q}-{sort}", "published_at": date(2026, 10, 18), "summary": ""},
            ]
            return base

        with mock.patch.object(news, "fetch_news", fake):
            rows = news.collect_company_news(None, "삼성전자", ("삼성전자",), date(2026, 10, 1), max_items=50)
        urls = [r["url"] for r in rows]
        self.assertIn("u1", urls)
        self.assertNotIn("u2", urls)  # 제목 중복 제거
        self.assertNotIn("u3", urls)  # since 이전 제외
        self.assertEqual(rows[0]["published_at"], date(2026, 10, 20))  # 최신순
        self.assertGreater(len(rows), 4)  # 주제어마다 다른 기사가 모임


if __name__ == "__main__":
    unittest.main()
