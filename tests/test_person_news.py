"""대주주·경영진 기사 과거분 (P05): 인물 이름 필터·기간·페이지 중단·새 작업."""
from datetime import date

from kic_data import jobs, quality, universe
from kic_data.collectors import news

SINCE, UNTIL = date(2026, 1, 1), date(2026, 10, 27)


def _row(i, title, d):
    return {"title": title, "url": f"https://n/{i}", "published_at": d, "summary": ""}


def test_title_must_contain_full_name_and_date_in_range(monkeypatch):
    page = [
        _row(1, "한미사이언스 신동국 회장, 지분 확대", date(2026, 5, 1)),
        _row(2, "한미사이언스 신 회장 지분", date(2026, 5, 2)),          # 호칭만 → 제외
        _row(3, "한미사이언스 신동국 회장 인터뷰", date(2026, 10, 28)),   # 기준일 뒤 → 제외
        _row(4, "한미 신동국 지분 매각", date(2025, 12, 31)),             # 기간 앞 → 제외
    ]
    monkeypatch.setattr(news, "fetch_news", lambda *a, **k: page if k.get("start", 1) == 1 else [])
    rows, info = news.collect_person_news(None, "한미사이언스", "신동국", SINCE, UNTIL)
    assert [r["url"] for r in rows] == ["https://n/1"]
    assert info["oldest_seen"] == date(2025, 12, 31)


def test_date_sort_stops_when_page_older_than_since(monkeypatch):
    calls = []

    def fake(session, q, display=100, start=1, sort="date"):
        calls.append((sort, start))
        d = date(2026, 3, 1) if start == 1 else date(2025, 11, 1)
        return [_row(start * 1000 + i, "삼성전자 이재용 회장", d) for i in range(100)]

    monkeypatch.setattr(news, "fetch_news", fake)
    news.collect_person_news(None, "삼성전자", "이재용", SINCE, UNTIL, pages=10)
    assert [c for c in calls if c[0] == "date"] == [("date", 1), ("date", 101)]
    assert len([c for c in calls if c[0] == "sim"]) == 10  # 관련도순은 날짜가 섞여 끝까지


def test_targets_and_job():
    people = sum(len(p) for _, p in universe.PERSON_NEWS.values())
    assert people == 23 and "호세 무뇨스" in universe.PERSON_NEWS["005380"][1]
    job = jobs.JOB_BY_NAME["backfill-person-news"]
    assert job.every is jobs.ONCE and quality.API_OF_JOB[job.name] == "naver"
    assert job.name not in jobs.PER_TICKER_BACKFILLS  # 새 종목 편입과 무관 (고정 명단)
    from kic_data import cli
    assert cli.PERSON_NEWS_SOURCE == "naver_news_backfill"
