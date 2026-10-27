"""KIC 데이터 수집 웹 (Vercel 배포용 FastAPI 앱).

  GET  /                       대시보드 (작업별 상태·최근 실행 기록·수집 현황)
  GET  /api/cron/tick          차례가 된 작업 실행 (CRON_SECRET 필요; Vercel Cron·GitHub Actions·외부 크론이 호출)
  POST /api/jobs/{name}/run    작업 하나 바로 실행 (CRON_SECRET 필요)
  POST /api/jobs/{name}/reset  1회 작업을 처음부터 다시 하도록 초기화 (CRON_SECRET 필요)
  GET  /api/jobs · /api/runs · /api/status · /api/features   JSON 조회
  GET  /quality                데이터 품질 대시보드 (web/quality_dashboard.html)
  GET  /api/quality/overview · /api/quality/data · /api/quality/api/{name} · /api/quality/schema/{name} · /api/quality/anomalies
                               품질 조회 JSON (읽기 전용 트랜잭션)

배포 방법은 docs/DEPLOY_VERCEL.md 를 보세요. 로컬 실행: uvicorn main:app --reload
"""
from __future__ import annotations

import base64
import hmac
import html
import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from kic_data import db, jobs, quality, status

KST = ZoneInfo("Asia/Seoul")
app = FastAPI(title="KIC 데이터 수집", docs_url=None, redoc_url=None)

# ---------------------------------------------------------------- 인증

def _require_cron(request: Request) -> None:
    secret = os.environ.get("CRON_SECRET")
    if not secret:
        raise HTTPException(503, "CRON_SECRET 환경변수를 먼저 설정하세요.")
    auth = request.headers.get("authorization", "")
    given = auth[7:] if auth.lower().startswith("bearer ") else request.query_params.get("key", "")
    if not hmac.compare_digest(given.encode(), secret.encode()):
        raise HTTPException(401, "인증 실패")


def _require_viewer(request: Request) -> None:
    """DASHBOARD_PASSWORD 가 있으면 HTTP 기본 인증(아이디는 아무거나)으로 보호합니다."""
    password = os.environ.get("DASHBOARD_PASSWORD")
    if not password:
        return
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            _, _, given = base64.b64decode(auth[6:]).decode().partition(":")
        except Exception:  # noqa: BLE001
            given = ""
        if hmac.compare_digest(given.encode(), password.encode()):
            return
    raise HTTPException(401, "비밀번호가 필요합니다.", headers={"WWW-Authenticate": 'Basic realm="kic"'})


def _jsonable(obj):
    if isinstance(obj, datetime):
        return obj.astimezone(KST).isoformat(timespec="seconds")
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def _ok(data, status_code: int = 200) -> JSONResponse:
    return JSONResponse(_jsonable(data), status_code=status_code)


def _connect():
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(503, "DATABASE_URL 환경변수를 먼저 설정하세요.")
    return db.connect()


# ---------------------------------------------------------------- 실행 API

@app.api_route("/api/cron/tick", methods=["GET", "POST"])
def cron_tick(request: Request, only: str | None = None):
    """Vercel Cron 은 GET 으로 부르고 Authorization: Bearer $CRON_SECRET 를 붙입니다."""
    _require_cron(request)
    with _connect() as conn:
        created = jobs.ensure_schema(conn)
        state = jobs.DbState(conn)
        if not state.try_lock():
            return _ok({"skipped": "다른 실행이 진행 중입니다."}, 409)
        try:
            result = jobs.tick(state, conn=conn, only=only)
            state.prune()
        finally:
            state.unlock()
    result["schema_created"] = created
    return _ok(result)


@app.post("/api/jobs/{name}/run")
def run_one(name: str, request: Request):
    _require_cron(request)
    job = jobs.JOB_BY_NAME.get(name)
    if not job:
        raise HTTPException(404, "없는 작업입니다.")
    with _connect() as conn:
        jobs.ensure_schema(conn)
        state = jobs.DbState(conn)
        if not state.try_lock():
            return _ok({"skipped": "다른 실행이 진행 중입니다. 잠시 뒤 다시 누르세요."}, 409)
        try:
            return _ok(jobs.run_job(job, state, conn=conn, trigger="manual"))
        finally:
            state.unlock()


@app.post("/api/jobs/{name}/reset")
def reset_one(name: str, request: Request):
    _require_cron(request)
    if name not in jobs.JOB_BY_NAME:
        raise HTTPException(404, "없는 작업입니다.")
    with _connect() as conn:
        jobs.DbState(conn).reset(name)
    return _ok({"reset": name})


# ---------------------------------------------------------------- 조회 API

@app.get("/api/health")
def health():
    return {"ok": True, "time": datetime.now(KST).isoformat(timespec="seconds")}


@app.get("/api/jobs")
def list_jobs(request: Request):
    _require_viewer(request)
    with _connect() as conn:
        jobs.ensure_schema(conn)
        return _ok(jobs.overview(jobs.DbState(conn)))


@app.get("/api/runs")
def list_runs(request: Request, job: str | None = None, limit: int = 50):
    _require_viewer(request)
    with _connect() as conn:
        jobs.ensure_schema(conn)
        runs = jobs.DbState(conn).recent_runs(min(limit, 500) if not job else 500)
    if job:
        runs = [r for r in runs if r["job"] == job][:limit]
    return _ok(runs)


@app.get("/api/status")
def data_status(request: Request):
    _require_viewer(request)
    with _connect() as conn:
        return _ok(status.collect(conn))


@app.get("/api/features")
def features_json(request: Request):
    """에이전트 입력용 features.json 과 같은 내용 (DB 의 최신 스냅샷)."""
    _require_viewer(request)
    with _connect() as conn:
        data = db.read_features(conn)
    return _ok({f"{m}:{t}": v for (m, t), v in data.items()})


# ---------------------------------------------------------------- 데이터 품질 (sql/017, kic_data/quality.py)

WEB_DIR = Path(__file__).resolve().parent / "web"
QUALITY_PAGE = WEB_DIR / "quality_dashboard.html"


@app.get("/static/kic.css", include_in_schema=False)
def shared_css():
    return Response((WEB_DIR / "kic.css").read_text(encoding="utf-8"), media_type="text/css",
                    headers={"Cache-Control": "public, max-age=300"})


def _connect_readonly():
    """조회만 하므로 읽기 전용 트랜잭션으로 엽니다 (DATABASE_URL 이 읽기 전용 계정이 아니어도 쓰기 불가)."""
    conn = _connect()
    conn.read_only = True
    return conn


def _quality_db(fn):
    try:
        with _connect_readonly() as conn:
            return fn(conn)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001  quality 스키마가 아직 없을 때 등
        raise HTTPException(503, f"{type(exc).__name__}: {str(exc)[:200]}") from exc


@app.get("/quality", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def quality_page(request: Request):
    _require_viewer(request)
    page = QUALITY_PAGE.read_text(encoding="utf-8")
    return HTMLResponse(page.replace("<!--NAV-->", NAV.replace("{{JOBS_ACTIVE}}", "").replace("{{QUALITY_ACTIVE}}", "active")))


@app.get("/api/quality/overview")
def quality_overview(request: Request):
    _require_viewer(request)
    return _ok(_quality_db(quality.overview))


@app.get("/api/quality/data")
def quality_data(request: Request):
    """DB 의 현재 데이터 상태 (종목 마스터, 데이터 종류별 건수·최신 값·커버리지·최신성, 지수·거시·문서·공시)."""
    _require_viewer(request)
    return _ok(_quality_db(quality.data_status))


@app.get("/api/quality/api/{name}")
def quality_api(name: str, request: Request):
    _require_viewer(request)
    data = _quality_db(lambda conn: quality.api_detail(conn, name))
    if data is None:
        raise HTTPException(404, "없는 API 입니다.")
    return _ok(data)


@app.get("/api/quality/schema/{name}")
def quality_schema(name: str, request: Request):
    _require_viewer(request)
    data = _quality_db(lambda conn: quality.schema_tables(conn, name))
    if data is None:
        raise HTTPException(404, "조회할 수 없는 스키마입니다.")
    return _ok(data)


@app.get("/api/quality/anomalies")
def quality_anomalies(request: Request, limit: int = 50, severity: str | None = None):
    _require_viewer(request)
    if severity not in (None, "info", "warn", "error"):
        raise HTTPException(400, "severity 는 info / warn / error 중 하나입니다.")
    return _ok(_quality_db(lambda conn: quality.anomalies(conn, max(1, min(limit, 500)), severity)))


# ---------------------------------------------------------------- 대시보드

STATUS_TEXT = {"ok": "정상", "warn": "일부 실패", "failed": "실패", "skipped": "건너뜀", "partial": "진행 중"}


def _fmt(ts) -> str:
    if not ts:
        return "-"
    return ts.astimezone(KST).strftime("%m-%d %H:%M")


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    """수집 현황 (조회 전용). 수집은 GitHub Actions 가 하므로 실행 버튼은 두지 않습니다."""
    _require_viewer(request)
    job_html = runs_html = summary_html = ""
    error = None
    if os.environ.get("DATABASE_URL"):
        try:
            with db.connect() as conn:
                conn.read_only = True
                state = jobs.DbState(conn)
                # API 키는 Vercel 이 아니라 GitHub Actions Secrets 에 있으므로 '키 없음' 판단은 실제 실행 기록(건너뜀)에 맡김
                ov = jobs.overview(state, getenv=lambda k: "set")
                runs = state.recent_runs(40)
                st = status.collect(conn)
            job_html = _jobs_table(ov)
            runs_html = _runs_table(runs)
            summary_html = _summary_block(st)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
    else:
        error = "DATABASE_URL 이 설정되지 않았습니다. Vercel 프로젝트의 Environment Variables 에 넣고 다시 배포하세요."
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    body = PAGE.replace("{{NOW}}", now).replace("{{NAV}}", NAV.replace("{{JOBS_ACTIVE}}", "active")
                                                  .replace("{{QUALITY_ACTIVE}}", "")) \
        .replace("{{JOBS}}", job_html).replace("{{RUNS}}", runs_html).replace("{{SUMMARY}}", summary_html) \
        .replace("{{ERROR}}", f"<div class='errbox'>{_e(error)}</div>" if error else "")
    return HTMLResponse(body)


def _jobs_table(ov: list[dict]) -> str:
    rows, group = [], None
    for j in ov:
        if j["group"] != group:
            group = j["group"]
            rows.append(f"<tr class='group'><td colspan='6'>{_e(group)}</td></tr>")
        if j["running"]:
            pill = "<span class='pill partial'>실행 중</span>"
        elif j["missing"]:
            pill = "<span class='pill muted'>키 없음</span>"
        elif j["status"]:
            pill = f"<span class='pill {j['status']}'>{STATUS_TEXT.get(j['status'], j['status'])}</span>"
        else:
            pill = "<span class='pill muted'>대기</span>"
        if j["missing"]:
            nxt = "키를 넣으면 실행"
        elif j["status"] == "failed" and not j["due"]:
            nxt = "30분 뒤 재시도"
        elif j["due"]:
            nxt = "다음 수집"
        elif j["next"]:
            nxt = _fmt(j["next"])
        else:
            nxt = "완료" if j["last_complete"] else "-"
        detail = ("필요: " + ", ".join(j["missing"])) if j["missing"] else (j["detail"] or "")
        secs = "" if j["seconds"] is None or j["status"] == "skipped" else f"{j['seconds']:.0f}초"
        rows.append(
            f"<tr><td><b>{_e(j['label'])}</b><div class='sub'><code>{_e(j['name'])}</code> · {_e(j['every'])}</div></td>"
            f"<td>{pill}</td><td>{_fmt(j['last_finished'])}</td><td>{nxt}</td>"
            f"<td class='num'>{secs}</td>"
            f"<td class='detail'>{_e(detail)}</td></tr>")
    return "".join(rows)


def _runs_table(runs: list[dict]) -> str:
    out = []
    for r in runs:
        log = _e((r["log"] or "")[-4000:])
        secs = "" if r["seconds"] is None else f"{float(r['seconds']):.0f}초"
        out.append(
            f"<details><summary><span class='pill {_e(r['status'])}'>{STATUS_TEXT.get(r['status'], _e(r['status']))}</span> "
            f"{_fmt(r['started_at'])} <b>{_e(r['job'])}</b> <span class='sub'>{_e(r['trigger'])} · "
            f"{secs}"
            f"{(' · ' + _e(r['tickers'])) if r['tickers'] else ''}</span></summary><pre>{log}</pre></details>")
    return "".join(out) or "<p class='sub'>아직 실행 기록이 없습니다.</p>"


def _summary_block(st: dict) -> str:
    return "<pre class='json'>" + _e(json.dumps(_jsonable(st), ensure_ascii=False, indent=1)[:30000]) + "</pre>"


# 두 화면(수집 현황 / 데이터 품질) 공통 상단 전환 버튼
NAV = """<nav class="switch" aria-label="화면 전환">
<a class="{{JOBS_ACTIVE}}" href="/"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 19V9m6 10V5m6 14v-7m4 7H2"/></svg>수집 현황</a>
<a class="{{QUALITY_ACTIVE}}" href="/quality"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z"/><path d="M9 12l2 2 4-4"/></svg>데이터 품질</a>
</nav>"""

PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>KIC 수집 현황</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css">
<link rel="stylesheet" href="/static/kic.css">
</head><body>
<header><div class="bar">
<div class="brand"><div class="logo">KIC</div><h1>수집 현황<small>기준 {{NOW}} KST · 1분마다 새로고침</small></h1></div>
{{NAV}}
</div></header>
<main>
<div class="info"><span>수집은 GitHub Actions 가 매시 7분에 자동 실행합니다 (이 화면은 조회 전용)</span></div>
{{ERROR}}
<h3 style="margin-top:0">작업</h3>
<div class="table-wrap"><table class="wide"><thead><tr><th>작업</th><th>상태</th><th>마지막 실행</th><th>다음 예정</th><th class="num">소요</th><th>내용</th></tr></thead><tbody>{{JOBS}}</tbody></table></div>
<h3>최근 실행 기록</h3>{{RUNS}}
<h3>수집 현황 (status 원본)</h3><details><summary>펼치기</summary>{{SUMMARY}}</details>
</main>
<script>
setTimeout(()=>{if(!document.querySelector('details[open]'))location.reload()},60000);
</script></body></html>"""
