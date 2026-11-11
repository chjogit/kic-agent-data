"""Cloudflare Python Worker 진입점: 기존 FastAPI 앱(main.py)을 조회 전용으로 서빙합니다.

scripts/build_worker.py 가 kic_data, sql, web, main.py 를 이 폴더로 복사한 뒤에 배포합니다.
- DB: Hyperdrive 바인딩(HYPERDRIVE_MAIN / HYPERDRIVE_OPINION) + pg8000. 요청마다 새 연결.
- 읽기 전용 계정이므로 jobs.ensure_schema(DDL)는 건너뜀.
- 락(asyncio.Lock)은 쓰지 않음: 한 요청이 쥔 락을 다른 요청이 기다리면 Workers 가 '요청 간 promise'로 보고 대기 요청을 취소함(Error 1101, code had hung). 연결은 요청마다 새로 만들어 공유하지 않으므로 직렬화가 필요 없음.
- 응답 캐시: 인증을 통과한 조회 화면을 5분간 isolate 메모리에 보관(kic_data/response_cache.py).
- guard: 허용 목록 밖 요청 405, DASHBOARD_PASSWORD 시크릿이 없으면 503. 비밀값은 출력하지 않음.
"""
import os

import anyio.to_thread
from workers import WorkerEntrypoint

try:   # 현재 Workers 런타임은 workers.asgi, 예전 방식은 최상위 asgi
    from workers import asgi
except ImportError:
    import asgi

import main
from kic_data import cf_db, db, jobs
from kic_data.response_cache import response_cache
from kic_data.worker_guard import guard  # noqa: F401  (테스트·재사용용으로 여기서도 노출)



async def _run_inline(func, *args, **_kw):
    """Pyodide(Workers)에는 스레드가 없어 FastAPI 의 동기 엔드포인트(anyio 스레드풀)가 실패합니다.
    그래서 호출한 자리에서 바로 실행합니다. """
    return func(*args)


anyio.to_thread.run_sync = _run_inline   # starlette.concurrency.run_in_threadpool 이 호출 때마다 이 속성을 찾음

_APP = guard(response_cache(main.app))   # guard(허용 목록·503) → 캐시(인증 통과 GET 만) → 앱


def _secret(env, name: str):
    try:
        value = getattr(env, name, None)
    except Exception:  # noqa: BLE001
        return None
    return str(value) if value else None


def configure(env) -> None:
    """요청 하나를 위한 설정. 매번 같은 값을 다시 넣을 뿐이라 동시에 불려도 안전합니다(await 없음)."""

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
    jobs.ensure_schema = lambda conn: False   # 읽기 전용 계정은 DDL 불가 (False = 스키마를 새로 만들지 않았음)


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        configure(self.env)
        return await asgi.fetch(_APP, request.js_object, self.env)
