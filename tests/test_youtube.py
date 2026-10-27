import unittest
from datetime import date

from kic_data.collectors import youtube


class YoutubeParseTest(unittest.TestCase):
    def test_search(self):
        payload = {"items": [
            {"id": {"videoId": "abc"}, "snippet": {"title": "삼성전자 주가 전망", "description": "설명",
                                                   "publishedAt": "2026-10-25T23:30:00Z", "channelTitle": "채널"}},
            {"id": {"channelId": "x"}, "snippet": {"title": "채널 결과"}},
        ]}
        self.assertEqual(youtube.parse_search(payload), [
            {"video_id": "abc", "title": "삼성전자 주가 전망", "summary": "설명", "published_at": date(2026, 10, 25), "channel": "채널"}])

    def test_stats(self):
        payload = {"items": [{"id": "abc", "statistics": {"viewCount": "1200", "likeCount": "30"}}]}
        self.assertEqual(youtube.parse_stats(payload), {"abc": {"view_count": 1200, "like_count": 30, "comment_count": None}})

    def test_comments_drop_author(self):
        payload = {"items": [
            {"snippet": {"topLevelComment": {"id": "c1", "snippet": {
                "textOriginal": "오늘 반등 가즈아", "publishedAt": "2026-10-26T01:00:00Z",
                "authorDisplayName": "누군가", "authorChannelId": {"value": "UC1"}}}}},
            {"snippet": {"topLevelComment": {"id": "c2", "snippet": {"textOriginal": "  "}}}},
        ]}
        rows = youtube.parse_comments(payload, "abc")
        self.assertEqual(rows, [{"title": "", "url": "https://www.youtube.com/watch?v=abc&lc=c1",
                                 "published_at": date(2026, 10, 26), "summary": "오늘 반등 가즈아"}])
        self.assertNotIn("누군가", str(rows))

    def test_search_orders_by_view_count_by_default(self):
        seen = {}

        def fake_get_json(session, name, url, params=None, limiter=None):
            seen.update(params)
            return {"items": []}

        from unittest import mock

        with mock.patch.object(youtube, "get_json", fake_get_json), \
                mock.patch.dict("os.environ", {"YOUTUBE_API_KEY": "test"}):
            youtube.search_videos(None, "삼성전자 주가", date(2026, 10, 20))
            self.assertEqual(seen["order"], "viewCount")
            youtube.search_videos(None, "삼성전자 주가", date(2026, 10, 20), order="date")
            self.assertEqual(seen["order"], "date")

    def test_error(self):
        with self.assertRaises(RuntimeError):
            youtube.parse_search({"error": {"code": 403, "errors": [{"reason": "quotaExceeded"}]}})


if __name__ == "__main__":
    unittest.main()
