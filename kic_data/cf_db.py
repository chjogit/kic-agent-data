"""Cloudflare Python Worker 용 DB 연결 (Hyperdrive 바인딩 + pg8000).

pg8000 은 함수 안에서 불러오므로, 이 모듈은 pg8000 없이도 import 됩니다 (테스트·기본 psycopg 경로 영향 없음).
Worker 쪽에서 db.use_factories(...) 로 이 연결을 주입합니다.
"""
from __future__ import annotations

from typing import Any

READ_ONLY_SQL = "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"


class _Cursor:
    """pg8000 커서를 psycopg 커서처럼 `with` 로 쓰게 하는 얇은 래퍼 (나머지는 그대로 전달)."""

    def __init__(self, raw):
        self._raw = raw

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __iter__(self):
        return iter(self._raw)


class HyperdriveConnection:
    """pg8000.dbapi 연결 래퍼. 기존 코드가 쓰는 cursor/commit/rollback/close/with/read_only 만 제공합니다."""

    def __init__(self, raw):
        self._raw = raw
        self._read_only = False

    def cursor(self) -> _Cursor:
        return _Cursor(self._raw.cursor())

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

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
        """True 면 이 세션의 이후 모든 트랜잭션을 읽기 전용으로 (한 번만 실행).
        pg8000 에는 psycopg 의 conn.read_only 가 없어서, 세션 기본값을 바꾸고 트랜잭션을 끝내 설정을 확정합니다."""
        value = bool(value)
        if value == self._read_only:
            return
        cur = self._raw.cursor()
        try:
            cur.execute(READ_ONLY_SQL if value else "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE")
        finally:
            cur.close()
        self._raw.commit()
        self._read_only = value


def connect_hyperdrive(binding) -> HyperdriveConnection:
    """Hyperdrive 바인딩(host/port/user/password/database 속성)으로 접속. 바인딩 구간은 TLS 를 쓰지 않습니다."""
    import pg8000.dbapi

    raw = pg8000.dbapi.connect(user=binding.user, host=binding.host, port=int(binding.port),
                               database=binding.database, password=binding.password, ssl_context=None)
    return HyperdriveConnection(raw)
