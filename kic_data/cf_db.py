"""Cloudflare Python Worker 용 DB 연결 (Hyperdrive 바인딩 + pg8000).

pg8000 은 함수 안에서 불러오므로, 이 모듈은 pg8000 없이도 import 됩니다 (테스트·기본 psycopg 경로 영향 없음).
Worker 쪽에서 db.use_factories(...) 로 이 연결을 주입합니다.
"""
from __future__ import annotations

from typing import Any

READ_ONLY_SQL = "SET TRANSACTION READ ONLY"


class _Cursor:
    """pg8000 커서를 psycopg 커서처럼 `with` 로 쓰게 하는 얇은 래퍼.
    execute 전에 연결이 새 트랜잭션을 읽기 전용으로 시작하게 하고, params=None 은 ()로 바꿉니다
    (pg8000 은 None 을 못 받음. 빈 인자면 `%` 변환 없이 그대로 보냄)."""

    def __init__(self, raw, conn: "HyperdriveConnection"):
        self._raw = raw
        self._conn = conn

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)

    def execute(self, sql, params=None, *a, **kw):
        self._conn._begin_read_only()
        return self._raw.execute(sql, () if params is None else params, *a, **kw)

    def executemany(self, sql, seq, *a, **kw):
        self._conn._begin_read_only()
        return self._raw.executemany(sql, seq, *a, **kw)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __iter__(self):
        return iter(self._raw)


class HyperdriveConnection:
    """pg8000.dbapi 연결 래퍼. 기존 코드가 쓰는 cursor/commit/rollback/close/with/read_only 만 제공합니다.

    Hyperdrive 는 트랜잭션 단위 풀링이라 세션 설정(SET SESSION ...)이 트랜잭션이 끝나면 사라집니다.
    그래서 read_only 는 세션 상태에 기대지 않고, 매 트랜잭션의 첫 문장으로 SET TRANSACTION READ ONLY 를 보냅니다.
    `with` 는 닫기만 합니다 (psycopg 처럼 commit/rollback 하지 않음 — 읽기 전용 용도라 쓸 것이 없음)."""

    def __init__(self, raw):
        self._raw = raw
        self._read_only = False
        self._in_txn = False   # 이 래퍼가 보낸 문장으로 트랜잭션이 열려 있는지

    def _begin_read_only(self) -> None:
        if self._in_txn:
            return
        if self._read_only:
            cur = self._raw.cursor()   # pg8000 이 암묵적으로 BEGIN 한 뒤, 그 안의 첫 문장으로 실행됨
            try:
                cur.execute(READ_ONLY_SQL)
            finally:
                cur.close()
        self._in_txn = True

    def cursor(self) -> _Cursor:
        return _Cursor(self._raw.cursor(), self)

    def commit(self) -> None:
        self._raw.commit()
        self._in_txn = False

    def rollback(self) -> None:
        self._raw.rollback()
        self._in_txn = False

    def close(self) -> None:
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def read_only(self) -> bool:
        return self._read_only

    @read_only.setter
    def read_only(self, value: bool) -> None:
        """이후 모든 새 트랜잭션을 읽기 전용으로 시작하게 합니다 (여기서는 SQL 을 보내지 않음).
        트랜잭션이 열려 있는 동안 바꾸려 하면 psycopg 처럼 오류 — 이미 시작한 트랜잭션은 못 바꾸므로."""
        value = bool(value)
        if value != self._read_only and self._in_txn:
            raise RuntimeError("트랜잭션 도중에는 read_only 를 바꿀 수 없습니다 (commit/rollback 후에 설정)")
        self._read_only = value


def connect_hyperdrive(binding) -> HyperdriveConnection:
    """Hyperdrive 바인딩(host/port/user/password/database 속성)으로 접속.
    Worker→Hyperdrive 구간은 평문(Hyperdrive→원본 DB 의 TLS 는 Hyperdrive 가 처리)이라 ssl_context=False."""
    import pg8000.dbapi

    raw = pg8000.dbapi.connect(user=binding.user, host=binding.host, port=int(binding.port),
                               database=binding.database, password=binding.password, ssl_context=False, timeout=30)
    return HyperdriveConnection(raw)
