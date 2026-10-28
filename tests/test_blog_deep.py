"""블로그 과거분 깊게 받기: 페이지 상한(검색 API start ≤ 1,000)과 기간 밖 페이지에서 멈추는지."""
from datetime import date, timedelta

from kic_data import jobs, quality
from kic_data.collectors import community

TODAY = date(2026, 10, 28)
SINCE = TODAY - timedelta(days=30)


def _page(start: int, posted: date) -> list[dict]:
    return [{"title": f"삼성전자 글 {start + i}", "url": f"https://blog/{start + i}", "published_at": posted,
             "summary": "삼성전자 주가"} for i in range(100)]


def test_pages_capped_at_api_limit(monkeypatch):
    calls = []

    def fake_fetch(session, source, query, start=1, display=100):
        calls.append(start)
        return _page(start, TODAY)

    monkeypatch.setattr(community, "fetch", fake_fetch)
    rows = community.collect(None, "naver_blog", "삼성전자", ("삼성전자",), SINCE, pages=30)
    assert max(calls) == 901  # 10페이지째 (start 1~1000 범위)
    assert len(calls) == 2 * community.MAX_PAGES  # 검색어 2개 × 10페이지
    assert len(rows) == 1000  # 두 검색어 결과는 URL 로 합쳐짐


def test_stops_when_page_older_than_since(monkeypatch):
    calls = []

    def fake_fetch(session, source, query, start=1, display=100):
        calls.append(start)
        posted = TODAY - timedelta(days=start // 100 * 10)  # 페이지마다 10일씩 과거
        return _page(start, posted)

    monkeypatch.setattr(community, "fetch", fake_fetch)
    rows = community.collect(None, "naver_blog", "삼성전자", ("삼성전자",), SINCE, pages=10)
    assert sorted(set(calls)) == [1, 101, 201, 301, 401]  # 401(40일 전)에서 전부 기간 밖 → 멈춤
    assert all(r["published_at"] >= SINCE for r in rows)


def test_deep_job_is_blog_only_naver_lane():
    job = jobs.JOB_BY_NAME["backfill-blog-deep"]
    assert job.kwargs["sources"] == "naver_blog"
    assert job.kwargs["pages"] == community.MAX_PAGES
    assert job.every is jobs.ONCE
    assert quality.API_OF_JOB[job.name] == "naver"
    assert jobs.lane_of(job) == "naver"
    assert job.name in jobs.PER_TICKER_BACKFILLS  # 새 종목이 들어오면 다시
