"""Cloudflare Worker 용 DB 연결 주입점과 Hyperdrive(pg8000) 어댑터 — DB·pg8000 없이 가짜로 검증."""
import sys
import types
from types import SimpleNamespace

import pytest

from kic_data import alerts, cf_db, db


@pytest.fixture(autouse=True)
def _reset_factories():
    yield
    db.use_factories(None, None)


def _fake_psycopg(monkeypatch, calls):
    mod = types.ModuleType("psycopg")

    def connect(url, **kw):
        calls.append((url, kw))
        return SimpleNamespace(read_only=False, url=url)

    mod.connect = connect
    monkeypatch.setitem(sys.modules, "psycopg", mod)


def test_main_factory_injected_and_reset(monkeypatch):
    calls = []
    _fake_psycopg(monkeypatch, calls)
    monkeypatch.setenv("DATABASE_URL", "postgresql://main")
    sentinel = object()
    db.use_factories(lambda: sentinel, None)
    assert db.connect() is sentinel
    assert calls == []
    db.use_factories(None, None)
    assert db.connect().url == "postgresql://main"
    assert len(calls) == 1


def test_opinion_factory_receives_read_only(monkeypatch):
    monkeypatch.delenv("OPINION_DATABASE_URL", raising=False)
    seen = []
    db.use_factories(None, lambda ro: seen.append(ro) or "oconn")
    assert db.connect_opinion(read_only=True) == "oconn"
    assert db.connect_opinion() == "oconn"
    assert seen == [True, False]


def test_opinion_default_unchanged_without_factory(monkeypatch):
    monkeypatch.delenv("OPINION_DATABASE_URL", raising=False)
    assert db.connect_opinion(read_only=True) is None
    calls = []
    _fake_psycopg(monkeypatch, calls)
    monkeypatch.setenv("OPINION_DATABASE_URL", "postgresql://op")
    c = db.connect_opinion(read_only=True)
    assert c.read_only is True and calls[0][0] == "postgresql://op"


def test_alerts_connect_opinion_uses_injected_factory(monkeypatch):
    monkeypatch.delenv("OPINION_DATABASE_URL", raising=False)
    seen = []
    db.use_factories(None, lambda ro: seen.append(ro) or "oconn")
    assert alerts.connect_opinion() == "oconn"
    assert seen == [True]   # 읽기 전용


def test_alerts_connect_opinion_none_without_factory_or_env(monkeypatch):
    monkeypatch.delenv("OPINION_DATABASE_URL", raising=False)
    assert alerts.connect_opinion() is None


class FakeCursor:
    description = [("a",)]

    def __init__(self, log):
        self.log = log

    def execute(self, sql, params=None):
        self.log.append(("execute", sql, params))

    def executemany(self, sql, seq):
        self.log.append(("executemany", sql, list(seq)))

    def fetchall(self):
        return [(1,)]

    def fetchone(self):
        return (1,)

    def close(self):
        self.log.append(("cursor_close",))


class FakeRaw:
    def __init__(self):
        self.log = []

    def cursor(self):
        return FakeCursor(self.log)

    def commit(self):
        self.log.append(("commit",))

    def rollback(self):
        self.log.append(("rollback",))

    def close(self):
        self.log.append(("close",))


def _stmts(raw):
    """실행된 SQL 과 commit/rollback 만 순서대로."""
    return [e[1] if e[0] == "execute" else e[0] for e in raw.log if e[0] in ("execute", "commit", "rollback")]


def test_wrapper_context_manager_closes():
    raw = FakeRaw()
    with cf_db.HyperdriveConnection(raw) as conn:
        assert isinstance(conn, cf_db.HyperdriveConnection)
    assert ("close",) in raw.log


def test_read_only_setter_sends_nothing():
    raw = FakeRaw()
    conn = cf_db.HyperdriveConnection(raw)
    conn.read_only = True
    assert conn.read_only is True
    assert raw.log == []   # 세션 상태에 기대지 않음 (Hyperdrive 풀링)


def test_read_only_applied_first_and_once_per_transaction():
    raw = FakeRaw()
    conn = cf_db.HyperdriveConnection(raw)
    conn.read_only = True
    with conn.cursor() as cur:
        cur.execute("SELECT 1")
        cur.execute("SELECT 2")
    conn.cursor().execute("SELECT 3")
    assert _stmts(raw) == ["SET TRANSACTION READ ONLY", "SELECT 1", "SELECT 2", "SELECT 3"]


@pytest.mark.parametrize("end", ["commit", "rollback"])
def test_read_only_reapplied_after_each_transaction_end(end):
    raw = FakeRaw()
    conn = cf_db.HyperdriveConnection(raw)
    conn.read_only = True
    conn.cursor().execute("SELECT 1")
    getattr(conn, end)()
    conn.cursor().execute("SELECT 2")
    conn.cursor().execute("SELECT 3")
    assert _stmts(raw) == ["SET TRANSACTION READ ONLY", "SELECT 1", end,
                           "SET TRANSACTION READ ONLY", "SELECT 2", "SELECT 3"]


def test_not_read_only_sends_no_extra_statement():
    raw = FakeRaw()
    conn = cf_db.HyperdriveConnection(raw)
    conn.cursor().execute("SELECT 1")
    conn.read_only = False
    assert _stmts(raw) == ["SELECT 1"]


def test_read_only_change_mid_transaction_raises():
    raw = FakeRaw()
    conn = cf_db.HyperdriveConnection(raw)
    conn.cursor().execute("SELECT 1")
    with pytest.raises(RuntimeError):
        conn.read_only = True
    conn.rollback()
    conn.read_only = True   # 트랜잭션이 끝난 뒤에는 가능
    assert conn.read_only is True


def test_executemany_also_starts_read_only():
    raw = FakeRaw()
    conn = cf_db.HyperdriveConnection(raw)
    conn.read_only = True
    conn.cursor().executemany("SELECT %s", [(1,)])
    assert _stmts(raw)[0] == "SET TRANSACTION READ ONLY"


def test_execute_none_params_become_empty_tuple():
    raw = FakeRaw()
    conn = cf_db.HyperdriveConnection(raw)
    conn.cursor().execute("SELECT 'a%'", None)
    conn.cursor().execute("SELECT 'a%'")
    conn.cursor().execute("SELECT %s", [1])
    assert [e[2] for e in raw.log if e[0] == "execute"] == [(), (), [1]]


def test_wrapper_cursor_passthrough():
    raw = FakeRaw()
    conn = cf_db.HyperdriveConnection(raw)
    with conn.cursor() as cur:
        cur.execute("SELECT 1", [])
        assert cur.fetchall() == [(1,)]
        assert cur.fetchone() == (1,)
        assert cur.description == [("a",)]
    assert ("cursor_close",) in raw.log
    conn.commit()
    conn.rollback()
    assert ("commit",) in raw.log and ("rollback",) in raw.log


def test_connect_hyperdrive_passes_args(monkeypatch):
    calls = []
    dbapi = types.ModuleType("pg8000.dbapi")
    dbapi.connect = lambda *a, **kw: calls.append((a, kw)) or FakeRaw()
    pkg = types.ModuleType("pg8000")
    pkg.dbapi = dbapi
    monkeypatch.setitem(sys.modules, "pg8000", pkg)
    monkeypatch.setitem(sys.modules, "pg8000.dbapi", dbapi)
    binding = SimpleNamespace(host="h.example", port="5432", user="u", password="p", database="d")
    conn = cf_db.connect_hyperdrive(binding)
    assert isinstance(conn, cf_db.HyperdriveConnection)
    assert calls == [((), dict(user="u", host="h.example", port=5432, database="d", password="p",
                               ssl_context=False, timeout=30))]
