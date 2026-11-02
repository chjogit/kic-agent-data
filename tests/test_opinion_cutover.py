"""kic-opinion 컷오버 준비(#14) 오프라인 테스트: 시퀀스 맞춤, status·/quality·알림의 블로그·카페 읽기 위치. 모의 연결만 씀."""
from contextlib import contextmanager
from datetime import date

from kic_data import alerts, cli, db, quality, status

TODAY = date(2026, 11, 2)


class Cur:
    def __init__(self, conn):
        self.c = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.c.log.append((sql, params))
        self.rows = self.c.answer(sql, params)

    def fetchall(self):
        return self.rows


class SqlConn:
    """SQL 에 들어 있는 문자열로 결과를 고르는 모의 연결. 맞는 규칙이 없으면 빈 결과."""

    def __init__(self, rules=(), name=""):
        self.rules, self.log, self.commits, self.closed, self.name = list(rules), [], 0, False, name

    def answer(self, sql, params):
        for key, rows in self.rules:
            if all(k in sql for k in (key if isinstance(key, tuple) else (key,))):
                return rows(sql, params) if callable(rows) else rows
        return []

    def cursor(self):
        return Cur(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def sqls(self):
        return [s for s, _ in self.log]


class FakeSeq(SqlConn):
    """kic-opinion 시퀀스 흉내: nextval 대신 setval 로 바뀐 값을 기억."""

    def __init__(self, last, called, op_max):
        super().__init__()
        self.last, self.called, self.op_max = last, called, op_max

    def answer(self, sql, params):
        if "pg_get_serial_sequence" in sql:
            return [("rag.document_doc_id_seq",)]
        if "last_value" in sql:
            return [(self.last, self.called, self.op_max)]
        if "setval" in sql:
            self.last, self.called = params[1], params[2] if len(params) > 2 else True
            return [(params[1],)]
        return []

    def next_id(self):
        return self.last + 1 if self.called else self.last


def main_with_max(n):
    return SqlConn([("max(doc_id)", [(n,)])])


# ---------------------------------------------------------------- 시퀀스

def test_sequence_moves_above_main_max_after_copy():
    """복사 직후: kic-opinion 시퀀스는 1, 행은 메인 doc_id 그대로 → 메인 최대값 + 간격으로."""
    op = FakeSeq(last=1, called=False, op_max=480)
    changed = db.sync_opinion_sequences(main_with_max(500), op)
    assert changed == {"rag.document.doc_id": 500 + db.OPINION_ID_GAP}
    assert op.next_id() > 500 and op.next_id() > 480 and op.commits == 1


def test_sequence_sync_is_idempotent():
    op = FakeSeq(last=1, called=False, op_max=480)
    db.sync_opinion_sequences(main_with_max(500), op)
    first = op.last
    assert db.sync_opinion_sequences(main_with_max(500), op) == {}
    # 메인이 뉴스로 계속 늘어도(간격 안) 다시 옮기지 않고, 새 키는 여전히 메인 최대값보다 큼
    assert db.sync_opinion_sequences(main_with_max(90_000), op) == {}
    assert op.last == first and op.next_id() > 90_000 and op.commits == 1


def test_sequence_never_collides_with_existing_opinion_rows():
    """kic-opinion 행이 이미 메인보다 크고 시퀀스만 뒤처진 경우 → 최대값까지만 올림 (PK 충돌 방지)."""
    op = FakeSeq(last=10, called=True, op_max=2_000)
    assert db.sync_opinion_sequences(main_with_max(500), op) == {"rag.document.doc_id": 2_000}
    assert op.next_id() == 2_001


def test_sequence_reads_main_only():
    m = main_with_max(500)
    db.sync_opinion_sequences(m, FakeSeq(1, False, 0))
    assert m.commits == 0 and all(s.lstrip().upper().startswith("SELECT") for s in m.sqls())


def test_opinion_connection_syncs_sequences(monkeypatch):
    op = FakeSeq(last=1, called=False, op_max=480)
    m = SqlConn([("max(doc_id)", [(500,)]), ("FROM core.company", [])])
    monkeypatch.setattr(db, "connect_opinion", lambda: op)
    with db.opinion(m) as o:
        assert o is op and op.next_id() > 500
    assert op.closed


def test_apply_schema_syncs_opinion_sequences(monkeypatch):
    main, op, calls = SqlConn(name="main"), SqlConn(name="op"), []
    monkeypatch.setattr(db, "connect", lambda: main)
    monkeypatch.setattr(db, "apply_schema", lambda conn, **kw: ["001.sql"])
    monkeypatch.setattr(db, "connect_opinion", lambda: op)
    monkeypatch.setattr(db, "apply_opinion_schema", lambda conn: ["opinion/001_opinion.sql"])
    monkeypatch.setattr(db, "sync_opinion_sequences", lambda m, o: calls.append((m.name, o.name)) or {"rag.document.doc_id": 7})
    assert cli.cmd_apply_schema(None) == 0
    assert calls == [("main", "op")]


def test_apply_schema_without_opinion_skips_sequences(monkeypatch):
    monkeypatch.setattr(db, "connect", lambda: SqlConn())
    monkeypatch.setattr(db, "apply_schema", lambda conn, **kw: [])
    monkeypatch.setattr(db, "connect_opinion", lambda: None)
    monkeypatch.setattr(db, "sync_opinion_sequences", lambda m, o: (_ for _ in ()).throw(AssertionError("호출되면 안 됨")))
    assert cli.cmd_apply_schema(None) == 0


# ---------------------------------------------------------------- status · /quality

COMPANY = ("FROM core.company ORDER BY", [(1, "KR", "005930", "삼성전자", "x", None, "IT", 1)])
OLD, NEW = date(2026, 10, 1), date(2026, 11, 2)


def main_db():
    """메인: 블로그·카페는 10-01 이후 멈춤(컷오버 뒤 상태), 뉴스는 최신."""
    return SqlConn([
        COMPANY,
        ("source = 'naver_news'", [(1, 10, NEW)]),
        ("source = 'naver_blog'", [(1, 50, OLD)]),
        ("source = 'naver_cafe'", [(1, 40, OLD)]),
        (("doc_sentiment", "NOT IN"), [(1, 10)]),
        ("doc_sentiment", [(1, 100)]),
        (("FROM rag.document d", "NOT IN"), [("naver_news", 10, 1, OLD, NEW, 5)]),
        ("FROM rag.document d", [("naver_blog", 50, 1, OLD, OLD, 0), ("naver_news", 10, 1, OLD, NEW, 5)]),
    ], name="main")


def opinion_db():
    return SqlConn([
        ("source = 'naver_blog'", [(1, 70, NEW)]),
        ("source = 'naver_cafe'", [(1, 60, NEW)]),
        ("doc_sentiment", [(1, 120)]),
        ("FROM rag.document GROUP BY source", [("naver_blog", 70, 1, OLD, NEW), ("naver_cafe", 60, 1, OLD, NEW)]),
    ], name="op")


def test_status_reads_blog_cafe_from_opinion():
    m, op = main_db(), opinion_db()
    st = status.collect(m, TODAY, op, True)
    c = st["companies"][0]
    assert (c["blog_docs"], c["blog_last"], c["cafe_docs"], c["cafe_last"]) == (70, NEW, 60, NEW)
    assert c["news_docs"] == 10 and c["scored_docs"] == 10 + 120   # 메인은 블로그·카페 빼고 + kic-opinion
    assert not any("source = 'naver_blog'" in s or "source = 'naver_cafe'" in s for s in m.sqls())
    docs = {d["source"]: d for d in st["documents"]}
    assert docs["naver_blog"]["docs"] == 70 and docs["naver_blog"]["chunks"] is None and docs["naver_news"]["chunks"] == 5
    assert st["opinion"] == {"configured": True, "connected": True}
    text = status.render(st)
    fresh = text.split("[최신성]")[1].split("[시장 지수]")[0]
    assert "kic-opinion 에서 읽음" in text
    assert all("OK" in line for line in fresh.splitlines() if line.strip().startswith(("블로그", "카페")))


def test_status_configured_but_unreachable_leaves_blog_cafe_empty():
    m = main_db()
    st = status.collect(m, TODAY, None, True)
    c = st["companies"][0]
    assert "blog_last" not in c and "cafe_last" not in c
    assert "접속 실패" in status.render(st)
    assert all("naver_blog" not in s or "NOT IN" in s for s in m.sqls())


def test_status_without_opinion_is_unchanged():
    st = status.collect(main_db(), TODAY)
    c = st["companies"][0]
    assert (c["blog_docs"], c["blog_last"], c["scored_docs"]) == (50, OLD, 100)
    assert st["opinion"] == {"configured": False, "connected": False}


def test_quality_data_freshness_uses_opinion():
    ds = {d["name"]: d for d in quality.data_status(main_db(), TODAY, opinion_db(), True)["datasets"]}
    assert ds["블로그"]["freshness"] == "OK" and ds["블로그"]["rows"] == 70
    assert ds["카페"]["freshness"] == "OK" and ds["카페"]["rows"] == 60
    old = {d["name"]: d for d in quality.data_status(main_db(), TODAY)["datasets"]}
    assert old["블로그"]["freshness"].startswith("오래됨")   # 분리 없이 메인만 보면 '오래됨' (바로 이 오경보를 막음)


def test_opinion_reader(monkeypatch):
    monkeypatch.delenv(db.OPINION_URL_ENV, raising=False)
    with status.opinion_reader() as got:
        assert got == (None, False)
    monkeypatch.setenv(db.OPINION_URL_ENV, "postgresql://example.invalid/x")

    def boom(read_only=False):
        raise OSError("연결 실패")
    monkeypatch.setattr(db, "connect_opinion", boom)
    with status.opinion_reader() as got:
        assert got == (None, True)
    op = SqlConn()
    monkeypatch.setattr(db, "connect_opinion", lambda read_only=False: op if read_only else None)
    with status.opinion_reader() as got:
        assert got == (op, True)
    assert op.closed


def test_collect_all_uses_opinion_reader(monkeypatch):
    op = opinion_db()

    @contextmanager
    def reader():
        yield op, True
    monkeypatch.setattr(status, "opinion_reader", reader)
    assert status.collect_all(main_db(), TODAY)["companies"][0]["blog_last"] == NEW


# ---------------------------------------------------------------- 알림 (health)

def op_docs_db():
    return SqlConn([("source = ANY", [("naver_blog", 70, NEW), ("naver_cafe", 60, NEW)])])


def main_docs_db():
    return SqlConn([("source = ANY", [("naver_blog", 50, OLD), ("naver_cafe", 40, OLD)])])


def test_alert_health_reads_blog_cafe_from_opinion():
    m, op = main_docs_db(), op_docs_db()
    got = {d["source"]: d for d in alerts.fetch_opinion_docs(m, op, True, TODAY)}
    assert got["naver_blog"]["docs"] == 70 and got["naver_blog"]["freshness"] == "OK" and got["naver_cafe"]["project"] == "kic-opinion"
    assert not any("source = ANY" in s for s in m.sqls())


def test_alert_health_unreachable_opinion_does_not_fall_back_to_main():
    m = main_docs_db()
    got = alerts.fetch_opinion_docs(m, None, True, TODAY)
    assert all(d.get("error") == "접속 실패" for d in got) and m.log == []


def test_alert_health_without_opinion_reads_main():
    got = {d["source"]: d for d in alerts.fetch_opinion_docs(main_docs_db(), None, False, TODAY)}
    assert got["naver_blog"]["docs"] == 50 and got["naver_blog"]["project"] == "main"


def test_health_includes_opinion_docs_and_no_new_alerts():
    m, op = main_docs_db(), op_docs_db()
    hl = alerts.health(m, op, True)
    assert {d["source"] for d in hl["opinion_docs"]} == {"naver_blog", "naver_cafe"}
    assert not any(i["key"].startswith(("late:naver", "stale:naver")) for i in alerts.build_alerts(None, hl))
