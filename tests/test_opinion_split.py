"""블로그·카페 분리(kic-opinion) 오프라인 테스트: 모의 연결, 네트워크·DB 없음."""
import pytest

from kic_data import db


class Cur:
    def __init__(self, conn):
        self.c = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.c.log.append((sql, params))
        self.rows = self.c.results.pop(0) if self.c.results else []

    def executemany(self, sql, rows):
        self.c.log.append((sql, list(rows)))

    def fetchall(self):
        return self.rows


class Conn:
    def __init__(self, *results):
        self.results, self.log, self.commits, self.closed = list(results), [], 0, False

    def cursor(self):
        return Cur(self)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


def test_split_sources_all_main_when_not_split():
    m = Conn()
    assert db.split_sources(m, m, db.SENTIMENT_SOURCES) == (list(db.SENTIMENT_SOURCES), [])


def test_split_sources_sends_blog_cafe_to_opinion():
    main, op = db.split_sources(Conn(), Conn(), db.SENTIMENT_SOURCES)
    assert main == ["naver_news", "youtube_comment"] and op == ["naver_blog", "naver_cafe"]


def test_opinion_falls_back_to_main_without_setting(monkeypatch):
    monkeypatch.delenv(db.OPINION_URL_ENV, raising=False)
    m = Conn()
    with db.opinion(m) as o:
        assert o is m


def test_opinion_syncs_companies_and_closes(monkeypatch):
    op = Conn([(1, "KR", "005930")])
    m = Conn([(1, "KR", "005930", "삼성전자"), (2, "KR", "000660", "SK하이닉스")])
    monkeypatch.setattr(db, "connect_opinion", lambda: op)
    with db.opinion(m) as o:
        assert o is op
    assert op.closed and op.commits == 1
    inserted = [p for s, p in op.log if s.startswith("INSERT")][0]
    assert inserted == [(2, "KR", "000660", "SK하이닉스")]


def test_sync_rejects_mismatched_company_ids():
    m = Conn([(1, "KR", "005930", "삼성전자")])
    op = Conn([(1, "KR", "000660")])
    with pytest.raises(RuntimeError, match="종목 기준 불일치"):
        db.sync_opinion_companies(m, op)


def test_sync_noop_when_equal():
    m = Conn([(1, "KR", "005930", "삼성전자")])
    op = Conn([(1, "KR", "005930")])
    assert db.sync_opinion_companies(m, op) == 0 and op.commits == 0


def test_reactions_split_reads_each_source_from_its_project(monkeypatch):
    seen = []

    def fake(conn, cid, since, sources=None):
        seen.append((conn.name, tuple(sources)))
        return [{"source": s, "published_at": None, "score": None} for s in sources]
    monkeypatch.setattr(db, "fetch_reactions", fake)
    m, o = Conn(), Conn()
    m.name, o.name = "main", "opinion"
    out = db.fetch_reactions_split(m, o, 1, None)
    assert seen == [("main", ("naver_news", "youtube_comment")), ("opinion", ("naver_blog", "naver_cafe"))]
    assert {r["source"] for r in out} == set(db.SENTIMENT_SOURCES)
    seen.clear()
    db.fetch_reactions_split(m, m, 1, None)       # 분리 안 됨: 메인에서 한 번에
    assert seen == [("main", tuple(db.SENTIMENT_SOURCES))]
