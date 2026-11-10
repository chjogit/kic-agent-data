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


def test_wrapper_context_manager_closes():
    raw = FakeRaw()
    with cf_db.HyperdriveConnection(raw) as conn:
        assert isinstance(conn, cf_db.HyperdriveConnection)
    assert ("close",) in raw.log


def test_wrapper_read_only_sets_session_once():
    raw = FakeRaw()
    conn = cf_db.HyperdriveConnection(raw)
    conn.read_only = True
    conn.read_only = True
    execs = [e for e in raw.log if e[0] == "execute"]
    assert len(execs) == 1
    assert "READ ONLY" in execs[0][1].upper() and "SESSION CHARACTERISTICS" in execs[0][1].upper()
    assert raw.log[-1][0] in ("commit", "rollback")   # 세션 설정은 트랜잭션을 끝내 둠
    assert conn.read_only is True


def test_wrapper_read_only_false_is_noop_when_never_set():
    raw = FakeRaw()
    conn = cf_db.HyperdriveConnection(raw)
    conn.read_only = False
    assert raw.log == []
    assert conn.read_only is False


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
    assert calls == [((), dict(user="u", host="h.example", port=5432, database="d", password="p", ssl_context=None))]
