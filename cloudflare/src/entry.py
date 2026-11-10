"""Cloudflare Python Worker 진입점: 기존 FastAPI 앱(main.py)을 조회 전용으로 서빙합니다.

scripts/build_worker.py 가 kic_data, sql, web, main.py 를 이 폴더로 복사한 뒤에 배포합니다.
- DB: Hyperdrive 바인딩(HYPERDRIVE_MAIN / HYPERDRIVE_OPINION) + pg8000. 요청마다 새 연결.
- 읽기 전용 계정이므로 jobs.ensure_schema(DDL)는 건너뜀.
- 동기 DB 연결을 여러 요청이 같이 쓰지 않도록 asyncio.Lock 으로 직렬화.
- guard: 허용 목록 밖 요청 405, DASHBOARD_PASSWORD 시크릿이 없으면 503. 비밀값은 출력하지 않음.
"""
import asyncio
import os

import asgi
from workers import WorkerEntrypoint

import main
from kic_data import cf_db, db, jobs
from kic_data.worker_guard import guard  # noqa: F401  (테스트·재사용용으로 여기서도 노출)

_APP = guard(main.app)
_LOCK = None


def _lock() -> asyncio.Lock:
    global _LOCK
    if _LOCK is None:
        _LOCK = asyncio.Lock()
    return _LOCK


def _secret(env, name: str):
    try:
        value = getattr(env, name, None)
    except Exception:  # noqa: BLE001
        return None
    return str(value) if value else None


def configure(env) -> None:
    """요청 하나를 위한 설정. 반드시 락 안에서 부릅니다."""

    def main_factory():
        return cf_db.connect_hyperdrive(env.HYPERDRIVE_MAIN)

    def opinion_factory(read_only: bool = False):
        conn = cf_db.connect_hyperdrive(env.HYPERDRIVE_OPINION)
        if read_only:
            conn.read_only = True
        return conn

    db.use_factories(main_factory, opinion_factory)
    # main.py / status.opinion_reader 가 '설정됨'으로 판단하는 값일 뿐, 실제 접속은 위 팩토리가 합니다.
    os.environ["DATABASE_URL"] = "hyperdrive"
    os.environ["OPINION_DATABASE_URL"] = "hyperdrive"
    os.environ.pop("CRON_SECRET", None)   # 쓰기 경로는 어차피 막혀 있지만 이중으로 비워 둠
    password = _secret(env, "DASHBOARD_PASSWORD")
    if password:
        os.environ["DASHBOARD_PASSWORD"] = password
    else:
        os.environ.pop("DASHBOARD_PASSWORD", None)   # 없으면 guard 가 503 으로 닫음
    jobs.ensure_schema = lambda conn: True   # 읽기 전용 계정은 DDL 불가


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        async with _lock():
            configure(self.env)
            return await asgi.fetch(_APP, request.js_object, self.env)
