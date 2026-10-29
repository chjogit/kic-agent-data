"""10-29 회사별 공백 메우기: 별도재무 대체, 업종 비교군, 광고어 필터, 뉴스 대체 검색, 유튜브 순환, 새 작업."""
from datetime import date

from kic_data import db, jobs, krx_sector, quality
from kic_data.collectors import community, news


def _f(year, code, fs, acc="ifrs-full_Revenue"):
    return {"fiscal_year": year, "report_code": code, "fs_div": fs, "account_id": acc}


def test_ofs_used_when_no_consolidated():
    facts = [_f(2025, c, "OFS") for c in ("11013", "11012", "11014", "11011")]
    assert db.prefer_consolidated(facts) == facts  # 리노공업·카카오뱅크 (종속회사 없음)


def test_cfs_preferred_when_every_report_has_it():
    facts = [_f(2026, "11013", "CFS"), _f(2026, "11013", "OFS"), _f(2026, "11012", "CFS"), _f(2026, "11012", "OFS")]
    assert {f["fs_div"] for f in db.prefer_consolidated(facts)} == {"CFS"}


def test_same_basis_within_a_year_when_consolidation_starts_midyear():
    # 한화엔진 2025: 1~3분기는 별도만, 사업보고서는 연결+별도 → 그 해는 별도로 맞춤 (4분기 = 연간 − 3분기 누적)
    facts = [_f(2025, "11013", "OFS"), _f(2025, "11012", "OFS"), _f(2025, "11014", "OFS"),
             _f(2025, "11011", "CFS"), _f(2025, "11011", "OFS")]
    assert {f["fs_div"] for f in db.prefer_consolidated(facts)} == {"OFS"}
    assert len(db.prefer_consolidated(facts)) == 4


def test_sec_facts_untouched():
    facts = [{"fiscal_year": 2025, "report_code": "FY", "account_id": "Revenues"}]
    assert db.prefer_consolidated(facts) == facts


def test_isc_sector_override():
    assert krx_sector.ksic_to_krx("46510", "095340") == "전기·전자"


def test_spam_ignores_company_name():
    text = "DB손해보험 주가 전망, 3분기 실적 좋아"
    assert community.is_spam(text)  # 이름 속 '보험' 때문에 걸리던 문제
    assert not community.is_spam(text, ("DB손해보험",))
    assert community.is_spam("DB손해보험 주가 리딩방 무료추천", ("DB손해보험",))


def test_news_falls_back_to_bare_name(monkeypatch):
    calls = []
    today = date.today()

    def fake_fetch(session, q, display=100, start=1, sort="date"):
        calls.append(q)
        if q == "코스모화학":
            return [{"title": "코스모화학, 2차전지 소재 증설", "url": f"u{sort}", "published_at": today, "summary": ""}]
        return []

    monkeypatch.setattr(news, "fetch_news", fake_fetch)
    rows = news.collect_company_news(None, "코스모화학", ("코스모화학",), today)
    assert calls[-2:] == ["코스모화학", "코스모화학"] and len(rows) == 1


def test_news_aliases():
    assert "SKIET" in news.terms_for("KR", "361610", "SK아이이테크놀로지")[1]
    assert "ADI" in news.terms_for("US", "ADI", "Analog Devices")[1]


def test_youtube_rotation_covers_all():
    from kic_data import cli
    items = list(range(170))
    seen = set()
    for d in range(date(2026, 11, 1).toordinal(), date(2026, 11, 1).toordinal() + 7):
        part = cli.rotation_slice(items, 25, date.fromordinal(d))
        assert len(part) <= 25
        seen |= set(part)
    assert seen == set(items)  # 7일이면 한 바퀴
    assert cli.rotation_slice([], 25, date.today()) == []


def test_new_jobs_registered():
    for name in ("backfill-news-gaps", "backfill-community-gaps", "youtube-rest"):
        job = jobs.JOB_BY_NAME[name]
        assert name in quality.API_OF_JOB
    assert jobs.JOB_BY_NAME["backfill-news-gaps"].kwargs["only_missing"] is True
    assert jobs.lane_of(jobs.JOB_BY_NAME["youtube-rest"]) == jobs.lane_of(jobs.JOB_BY_NAME["youtube"])
    assert job.every == jobs.DAY


def test_near_sector_targets_exist():
    from kic_data import cli
    for src, dst in cli.NEAR_SECTOR.items():
        assert src in krx_sector.KRX_SECTORS and dst in krx_sector.KRX_SECTORS
