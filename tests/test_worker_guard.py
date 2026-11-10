"""Worker 가드·진입점·빌드 스크립트 — 실제 네트워크/DB/Cloudflare 런타임 없이 가짜로 검증."""
import asyncio
import importlib.util
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kic_data import cf_db, db, jobs
from kic_data.worker_guard import guard

ROOT = Path(__file__).resolve().parents[1]


def _app():
    app = FastAPI()
    seen = []

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/jobs")
    def list_jobs():
        seen.append("jobs")
        return {"jobs": []}

    @app.get("/api/cron/tick")
    def tick():
        seen.append("tick")
        return {}

    @app.post("/api/jobs/{name}/run")
    def run(name: str):
        seen.append("run")
        return {}

    @app.get("/api/quality/overview")
    def quality():
        return {"q": 1}

    @app.get("/static/kic.css")
    def css():
        return {"css": 1}

    app.seen = seen
    return app


@pytest.fixture
def password(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pw")


@pytest.fixture
def no_password(monkeypatch):
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)


def test_write_and_cron_paths_are_405_even_with_password(password):
    app = _app()
    c = TestClient(guard(app))
    assert c.get("/api/cron/tick").status_code == 405
    assert c.post("/api/jobs/x/run").status_code == 405
    assert c.post("/api/jobs").status_code == 405
    assert c.delete("/api/jobs").status_code == 405
    assert c.get("/api/jobs/x/reset").status_code == 405
    assert app.seen == []   # 앱까지 도달하지 않음


def test_unlisted_and_traversal_paths_are_405(password):
    c = TestClient(guard(_app()))
    assert c.get("/unknown").status_code == 405
    assert c.get("/api/quality/../cron/tick").status_code == 405
    assert c.get("/api/quality/").status_code == 405
    assert c.get("/api/jobs/").status_code == 405


def test_missing_password_is_503_except_health(no_password):
    app = _app()
    c = TestClient(guard(app))
    r = c.get("/api/jobs")
    assert r.status_code == 503 and "detail" in r.json()
    assert c.get("/api/quality/overview").status_code == 503
    assert c.get("/api/health").status_code == 200
    assert app.seen == []


def test_empty_password_counts_as_missing(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "")
    assert TestClient(guard(_app())).get("/api/jobs").status_code == 503


def test_allowed_get_passes_through_when_password_set(password):
    c = TestClient(guard(_app()))
    for path in ("/api/jobs", "/api/health", "/api/quality/overview", "/static/kic.css"):
        assert c.get(path).status_code == 200, path


def test_real_app_without_credentials_is_401_and_unlisted_is_405(password):
    import main
    c = TestClient(guard(main.app))
    assert c.get("/api/jobs").status_code == 401
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/cron/tick").status_code == 405
    assert c.post("/api/jobs/x/run").status_code == 405


def test_guard_does_not_leak_password(password):
    r = TestClient(guard(_app())).get("/api/cron/tick")
    assert "pw" not in r.text


def test_main_import_graph_has_no_heavy_modules():
    code = ("import sys, main; "
            "bad=[m for m in ('requests','yfinance','pandas','numpy','boto3','psycopg') if m in sys.modules]; "
            "print(','.join(bad))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""


# ---------------------------------------------------------------- entry.py (가짜 workers/asgi 로 로드)

@pytest.fixture
def entry(monkeypatch):
    calls = []
    fake_workers = types.ModuleType("workers")

    class WorkerEntrypoint:
        pass

    fake_workers.WorkerEntrypoint = WorkerEntrypoint
    fake_asgi = types.ModuleType("asgi")
    import anyio.to_thread
    monkeypatch.setattr(anyio.to_thread, "run_sync", anyio.to_thread.run_sync)   # entry 가 바꾼 것을 원복

    async def fetch(app, req, env):
        calls.append({"app": app, "req": req, "env": env,
                      "locked": module._LOCK.locked(),
                      "env_url": __import__("os").environ.get("DATABASE_URL"),
                      "opinion_url": __import__("os").environ.get("OPINION_DATABASE_URL"),
                      "password": __import__("os").environ.get("DASHBOARD_PASSWORD")})
        return "response"

    fake_asgi.fetch = fetch
    monkeypatch.setitem(sys.modules, "workers", fake_workers)
    monkeypatch.setitem(sys.modules, "asgi", fake_asgi)
    monkeypatch.setattr(jobs, "ensure_schema", jobs.ensure_schema)   # 원복용
    for k in ("DATABASE_URL", "OPINION_DATABASE_URL", "DASHBOARD_PASSWORD", "CRON_SECRET"):
        monkeypatch.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location("kic_worker_entry", ROOT / "cloudflare" / "src" / "entry.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.calls = calls
    yield module
    db.use_factories(None, None)


def _env(**extra):
    b = lambda n: SimpleNamespace(host=f"{n}.h", port="5432", user="u", password="p", database=n)  # noqa: E731
    return SimpleNamespace(HYPERDRIVE_MAIN=b("main"), HYPERDRIVE_OPINION=b("opinion"), **extra)


def test_entry_fetch_configures_request(entry, monkeypatch):
    opened = []

    def fake_connect(binding):
        conn = SimpleNamespace(read_only=False, db=binding.database)
        opened.append(conn)
        return conn

    monkeypatch.setattr(cf_db, "connect_hyperdrive", fake_connect)
    env = _env(DASHBOARD_PASSWORD="secret")
    req = SimpleNamespace(js_object="JS")
    result = asyncio.run(entry.Default.fetch(SimpleNamespace(env=env), req))
    assert result == "response"
    call = entry.calls[0]
    assert call["req"] == "JS" and call["env"] is env
    assert call["locked"] is True   # DB 사용 구간은 락 안
    assert call["env_url"] == "hyperdrive" and call["opinion_url"] == "hyperdrive"
    assert call["password"] == "secret"
    assert jobs.ensure_schema(object()) is False   # DDL 건너뜀 (conn 을 건드리지 않음)
    # 팩토리가 바인딩별 연결을 만들고, opinion 은 read_only 를 전달
    assert db.connect().db == "main"
    oc = db.connect_opinion(read_only=True)
    assert oc.db == "opinion" and oc.read_only is True
    assert not entry._LOCK.locked()


def test_entry_without_password_secret_closes(entry, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "stale")
    asyncio.run(entry.Default.fetch(SimpleNamespace(env=_env()), SimpleNamespace(js_object=None)))
    assert entry.calls[0]["password"] is None   # 남은 값도 지워 fail-closed


def test_entry_serializes_concurrent_requests(entry):
    active, peak = [0], [0]

    async def slow_fetch(app, req, env):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        await asyncio.sleep(0.01)
        active[0] -= 1
        return "ok"

    sys.modules["asgi"].fetch = slow_fetch
    entry.asgi.fetch = slow_fetch

    async def run():
        d = entry.Default
        await asyncio.gather(*(d.fetch(SimpleNamespace(env=_env()), SimpleNamespace(js_object=None)) for _ in range(4)))

    asyncio.run(run())
    assert peak[0] == 1


def test_entry_lock_released_after_fetch_error(entry):
    async def boom(app, req, env):
        raise RuntimeError("fetch failed")

    entry.asgi.fetch = boom

    async def run():
        with pytest.raises(RuntimeError):
            await entry.Default.fetch(SimpleNamespace(env=_env()), SimpleNamespace(js_object=None))
        assert not entry._LOCK.locked()

    asyncio.run(run())


def _get_raw(app, path):
    """TestClient 는 자체 스레드를 쓰므로, 스레드 금지 시험은 ASGI 를 직접 호출."""
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "method": "GET", "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": [], "scheme": "http", "server": ("t", 80), "client": ("c", 1), "http_version": "1.1",
             "root_path": ""}
    asyncio.run(app(scope, receive, send))
    return sent[0]["status"]


def test_sync_endpoints_work_without_threads(entry, monkeypatch):
    """Pyodide 에는 스레드가 없다: 스레드 시작이 실패해도 동기 FastAPI 엔드포인트가 응답해야 함."""
    import threading

    def no_threads(self, *a, **kw):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", no_threads)
    assert _get_raw(entry._APP, "/api/health") == 200


def test_threadpool_really_needs_the_patch(monkeypatch):
    """대조군: 패치가 없으면 같은 조건에서 실패한다 (위 테스트가 헛돌지 않음을 보증)."""
    import threading

    import anyio.to_thread
    import main

    def no_threads(self, *a, **kw):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", no_threads)
    with pytest.raises(RuntimeError):
        _get_raw(guard(main.app), "/api/health")


def test_entry_falls_back_to_toplevel_asgi_and_prefers_workers_asgi(monkeypatch):
    import anyio.to_thread
    monkeypatch.setattr(anyio.to_thread, "run_sync", anyio.to_thread.run_sync)
    monkeypatch.setattr(jobs, "ensure_schema", jobs.ensure_schema)
    path = ROOT / "cloudflare" / "src" / "entry.py"

    def load(workers_asgi):
        w = types.ModuleType("workers")
        w.WorkerEntrypoint = type("WorkerEntrypoint", (), {})
        if workers_asgi is not None:
            w.asgi = workers_asgi
        monkeypatch.setitem(sys.modules, "workers", w)
        top = types.ModuleType("asgi")
        monkeypatch.setitem(sys.modules, "asgi", top)
        spec = importlib.util.spec_from_file_location("kic_worker_entry2", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, top

    preferred = types.ModuleType("workers_asgi")
    mod, top = load(preferred)
    assert mod.asgi is preferred
    mod, top = load(None)
    assert mod.asgi is top


# ---------------------------------------------------------------- 빌드 스크립트

def _load_build():
    spec = importlib.util.spec_from_file_location("build_worker", ROOT / "scripts" / "build_worker.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_build_worker_copies_only_needed_files(tmp_path):
    out = _load_build().build(tmp_path / "src")
    assert (out / "main.py").is_file()
    assert (out / "kic_data" / "db.py").is_file()
    assert (out / "kic_data" / "cf_db.py").is_file()
    assert (out / "kic_data" / "worker_guard.py").is_file()
    assert any((out / "sql").glob("*.sql"))
    assert (out / "web" / "quality_dashboard.html").is_file()
    assert (out / "web" / "kic.css").is_file()
    assert not (out / "kic_data" / "collectors").exists()
    assert not (out / "tests").exists()
    assert not list(out.rglob("__pycache__"))
    assert not list(out.rglob("*.pyc"))
    # 재실행해도 동작 (이전 복사본 교체)
    (out / "kic_data" / "stale.py").write_text("x")
    _load_build().build(out)
    assert not (out / "kic_data" / "stale.py").exists()


def test_build_worker_refuses_dest_overlapping_sources(tmp_path):
    build = _load_build().build
    for bad in (ROOT, ROOT / "kic_data", ROOT / "kic_data" / "sub", ROOT / "web", ROOT / "sql", ROOT / "main.py"):
        with pytest.raises(ValueError):
            build(bad)
    assert (ROOT / "kic_data" / "db.py").is_file()   # 원본 무사
    fake_root = tmp_path / "r"
    (fake_root / "kic_data").mkdir(parents=True)
    with pytest.raises(ValueError):   # 원본을 품는 상위 폴더
        build(tmp_path, root=fake_root)
