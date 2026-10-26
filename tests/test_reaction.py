"""개인 투자자 반응(블로그·카페)·감성 점수 오프라인 테스트."""
import unittest
from datetime import date, timedelta
from unittest import mock

from kic_data import features, sentiment
from kic_data.collectors import community


class SentimentTest(unittest.TestCase):
    def test_basic(self):
        self.assertGreater(sentiment.lexicon_score("삼성전자 신고가 돌파, 외인 순매수")[0], 0.5)
        self.assertLess(sentiment.lexicon_score("하이닉스 급락… 실적 쇼크 우려")[0], -0.5)
        self.assertEqual(sentiment.lexicon_score("삼성전자 주주총회 일정"), (None, 0))

    def test_longest_match_and_negation(self):
        self.assertEqual(sentiment.lexicon_score("외국인 순매도")[1], 1)  # '매도' 중복 없음
        self.assertGreater(sentiment.lexicon_score("이제 하락 안 한다")[0], 0)
        self.assertEqual(sentiment.lexicon_score("상승 기대하기 힘들다")[0], -1.0)  # 상승·기대 둘 다 반전
        self.assertLess(sentiment.lexicon_score("호재가 아니다")[0], 0)
        self.assertLess(sentiment.lexicon_score("결국 돌파 실패")[0], 0)
        # 문장부호를 넘어가면 반전하지 않음, '안정' 은 부정어 아님
        self.assertGreater(sentiment.lexicon_score("반등. 걱정 없다")[0], 0)
        self.assertGreater(sentiment.lexicon_score("상승 안정적")[0], 0)

    def test_weights(self):
        self.assertEqual(sentiment.lexicon_score("주가 상승")[0], 0.5)
        self.assertEqual(sentiment.lexicon_score("상한가")[0], 1.0)
        self.assertEqual(sentiment.lexicon_score("상승 우려")[0], 0.0)
        self.assertEqual(sentiment.lexicon_score("폭락 손절 하락")[0], -1.0)  # -1 로 자름

    def test_label(self):
        self.assertEqual(sentiment.label_of(None), "none")
        self.assertEqual(sentiment.label_of(0.5), "positive")
        self.assertEqual(sentiment.label_of(-0.5), "negative")
        self.assertEqual(sentiment.label_of(0.0), "neutral")


class CommunityTest(unittest.TestCase):
    PAYLOAD = {"items": [
        {"title": "<b>삼성전자</b> 주가 반등", "link": "https://blog.naver.com/a/1", "description": "HBM", "postdate": "20261021"},
        {"title": "리딩방 무료추천 삼성전자", "link": "https://blog.naver.com/b/2", "description": "", "postdate": "20261021"},
        {"title": "옛날 글 삼성전자", "link": "https://blog.naver.com/c/3", "description": "", "postdate": "20250101"},
        {"title": "애플 아이폰 후기", "link": "https://blog.naver.com/d/4", "description": "", "postdate": "20261021"},
    ]}

    def test_parse_blog_and_cafe_dates(self):
        rows = community.parse_items(self.PAYLOAD, "naver_blog")
        self.assertEqual(rows[0]["published_at"], date(2026, 10, 21))
        self.assertEqual(rows[0]["title"], "삼성전자 주가 반등")
        cafe = community.parse_items({"items": [{"title": "t", "link": "https://cafe.naver.com/x/1"}]},
                                     "naver_cafe", today=date(2026, 10, 22))
        self.assertEqual(cafe[0]["published_at"], date(2026, 10, 22))  # 카페는 수집일
        with self.assertRaises(RuntimeError):
            community.parse_items({"errorCode": "024", "errorMessage": "auth"}, "naver_blog")

    def test_html_cleaned_before_matching(self):
        rows = community.parse_items({"items": [{"title": "<b>삼성전자</b> <b>주가</b> &quot;반등&quot;",
                                                 "link": "https://blog.naver.com/z/9", "postdate": "20261021",
                                                 "description": "&lt;HBM&gt; 기대"}]}, "naver_blog")
        self.assertEqual(rows[0]["title"], '삼성전자 주가 "반등"')
        self.assertEqual(rows[0]["summary"], "<HBM> 기대")

    def test_spam(self):
        self.assertTrue(community.is_spam("삼성전자 주가 보고 소액결제 현금화"))
        self.assertTrue(community.is_spam("히든종목 공개합니다 삼성전자"))
        self.assertFalse(community.is_spam("삼성전자 주가 HBM 기대감"))

    def test_collect_filters(self):
        rows = community.parse_items(self.PAYLOAD, "naver_blog")
        with mock.patch.object(community, "fetch", lambda s, src, q, start=1, display=100: rows if start == 1 else []):
            got = community.collect(None, "naver_blog", "삼성전자", ("삼성전자",), date(2026, 10, 1), pages=2)
        self.assertEqual([r["url"] for r in got], ["https://blog.naver.com/a/1"])  # 광고·기간밖·무관 제외


class ReactionFeatureTest(unittest.TestCase):
    def test_crowd_tone(self):
        today = date(2026, 10, 22)
        recent = [{"source": "naver_blog", "published_at": today - timedelta(days=i % 7), "score": 0.6} for i in range(10)]
        prev = [{"source": "naver_cafe", "published_at": today - timedelta(days=10 + i % 15), "score": -0.2} for i in range(20)]
        news = [{"source": "naver_news", "published_at": today, "score": None}]
        rows = {r["feature"]: r["value"] for r in features.attention_rows(
            {"today": today, "reactions": recent + prev + news})}
        self.assertAlmostEqual(rows["crowd_sentiment_7d"], 0.6)  # 모두 같은 점수라 가중해도 0.6
        self.assertAlmostEqual(rows["crowd_sentiment_chg"], 0.8)
        self.assertIsNone(rows["news_sentiment_7d"])  # 점수 있는 뉴스 5건 미만


class SameDayTest(unittest.TestCase):
    TODAY = date(2026, 10, 22)

    def rows(self, items):
        return {r["feature"]: r["value"] for r in features.attention_rows({"today": self.TODAY, "reactions": items})}

    def test_recency_weight_and_1d(self):
        d = lambda n: self.TODAY - timedelta(days=n)
        week = [{"source": "naver_blog", "published_at": d(3 + i % 4), "score": 0.5} for i in range(12)]
        today = [{"source": "naver_news", "published_at": d(0), "score": -0.5} for _ in range(4)]
        cafe_today = [{"source": "naver_cafe", "published_at": d(0), "score": 1.0} for _ in range(5)]
        r = self.rows(week + today + cafe_today)
        self.assertAlmostEqual(r["sentiment_1d"], -0.5)  # 카페(수집일 기준)는 당일 지표에서 제외
        # 7일(뉴스·블로그, 가중): (12×0.5×1 + 4×(-0.5)×3) / (12 + 12) = 0.0
        self.assertAlmostEqual(r["sentiment_1d_vs_7d"], -0.5)
        # 단순 평균이면 0.25 였을 값이 가중으로 0.0 → 당일 악화가 덜 희석됨

    def test_1d_needs_three(self):
        r = self.rows([{"source": "naver_news", "published_at": self.TODAY, "score": 1.0}] * 2)
        self.assertIsNone(r["sentiment_1d"])


if __name__ == "__main__":
    unittest.main()
