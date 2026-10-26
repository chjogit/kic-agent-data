"""KIC 데이터 수집 웹 (Vercel 배포용 FastAPI 앱).

  GET  /                       대시보드 (작업별 상태·최근 실행 기록·수집 현황)
  GET  /api/cron/tick          차례가 된 작업 실행 (CRON_SECRET 필요; Vercel Cron·GitHub Actions·외부 크론이 호출)
  POST /api/jobs/{name}/run    작업 하나 바로 실행 (CRON_SECRET 필요)
  POST /api/jobs/{name}/reset  1회 작업을 처음부터 다시 하도록 초기화 (CRON_SECRET 필요)
  GET  /api/jobs · /api/runs · /api/status · /api/features   JSON 조회

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
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from kic_data import db, jobs, status

KST = ZoneInfo("Asia/Seoul")
app = FastAPI(title="KIC 데이터 수집", docs_url=None, redoc_url=None)

# 대시보드에 '설정됨/없음' 으로만 보여 줄 환경변수 (값은 절대 출력하지 않음)
ENV_KEYS = [
    ("DATABASE_URL", "Neon DB 연결 문자열", True),
    ("CRON_SECRET", "자동 실행 호출 비밀값", True),
    ("FSC_API_KEY", "공공데이터포털 (국내 시세·ETF)", True),
    ("DART_API_KEY", "OpenDART (재무·공시)", True),
    ("SEC_USER_AGENT", "SEC EDGAR ('이름 이메일')", True),
    ("NAVER_CLIENT_ID", "네이버 검색 API ID", True),
    ("NAVER_CLIENT_SECRET", "네이버 검색 API Secret", True),
    ("NCP_APIHUB_KEY_ID", "NAVER API HUB 검색 트렌드 ID", False),
    ("NCP_APIHUB_KEY", "NAVER API HUB 검색 트렌드 Key", False),
    ("ECOS_API_KEY", "한국은행 ECOS", True),
    ("FRED_API_KEY", "미국 FRED", True),
    ("FINNHUB_API_KEY", "Finnhub 해외 영문 뉴스 (선택)", False),
    ("DASHBOARD_PASSWORD", "대시보드 비밀번호 (선택)", False),
]


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
    _require_viewer(request)
    env_rows = "".join(
        f"<tr><td><code>{k}</code></td><td>{_e(desc)}</td>"
        f"<td><span class='pill {'ok' if os.environ.get(k) else ('failed' if req else 'muted')}'>"
        f"{'설정됨' if os.environ.get(k) else ('없음' if req else '선택')}</span></td></tr>"
        for k, desc, req in ENV_KEYS)
    job_html = runs_html = summary_html = ""
    error = None
    if os.environ.get("DATABASE_URL"):
        try:
            with db.connect() as conn:
                jobs.ensure_schema(conn)
                state = jobs.DbState(conn)
                ov = jobs.overview(state)
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
    body = PAGE.replace("{{NOW}}", now).replace("{{ENV}}", env_rows).replace("{{JOBS}}", job_html) \
        .replace("{{RUNS}}", runs_html).replace("{{SUMMARY}}", summary_html) \
        .replace("{{ERROR}}", f"<div class='error'>{_e(error)}</div>" if error else "")
    return HTMLResponse(body)


def _jobs_table(ov: list[dict]) -> str:
    rows, group = [], None
    for j in ov:
        if j["group"] != group:
            group = j["group"]
            rows.append(f"<tr class='group'><td colspan='7'>{_e(group)}</td></tr>")
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
            nxt = "다음 tick"
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
            f"<td class='detail'>{_e(detail)}</td>"
            f"<td class='act'><button onclick=\"runJob('{_e(j['name'])}')\">실행</button>"
            + (f"<button class='ghost' onclick=\"resetJob('{_e(j['name'])}')\">다시</button>" if j["every"] == "1회" else "")
            + "</td></tr>")
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


PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>KIC 데이터 수집</title>
<style>
:root{--btnink:#fff;--bg:#f7f7f5;--card:#fff;--ink:#1d1d1b;--sub:#6b6b66;--line:#e4e3de;--accent:#2f5bd3;
--ok:#1f7a3f;--okb:#e5f4e4;--warn:#9a5b00;--warnb:#f9eeda;--bad:#b42318;--badb:#fbebeb;--mut:#6b6b66;--mutb:#f0efec;--run:#2f5bd3;--runb:#e7f1fb}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1e1e1c;--ink:#ecebe6;--sub:#a3a29c;--line:#33332f;--accent:#8aa8ff;
--btnink:#141413;--ok:#7fd49a;--okb:#1d3324;--warn:#f0c070;--warnb:#3a2e17;--bad:#ff9a8f;--badb:#3d1f1c;--mut:#a3a29c;--mutb:#2a2a27;--run:#9db8ff;--runb:#1d2a44}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px}
.sub{color:var(--sub);font-size:12px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:4px 0;overflow-x:auto}
table{border-collapse:collapse;width:100%;min-width:820px}td,th{padding:8px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
tr:last-child td{border-bottom:0}tr.group td{background:var(--mutb);font-weight:600;font-size:12px;color:var(--sub)}
.num{text-align:right;white-space:nowrap}.detail{color:var(--sub);font-size:12px;max-width:340px}.act{white-space:nowrap}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;white-space:nowrap}
.pill.ok{color:var(--ok);background:var(--okb)}.pill.warn{color:var(--warn);background:var(--warnb)}.pill.failed{color:var(--bad);background:var(--badb)}
.pill.skipped,.pill.muted{color:var(--mut);background:var(--mutb)}.pill.partial{color:var(--run);background:var(--runb)}
button{font:inherit;font-size:12px;border:1px solid var(--accent);background:var(--accent);color:var(--btnink);border-radius:6px;padding:3px 10px;cursor:pointer;margin-right:4px}
button.ghost{background:transparent;color:var(--accent)}button.big{font-size:14px;padding:6px 14px}
details{background:var(--card);border:1px solid var(--line);border-radius:8px;margin:6px 0;padding:6px 12px}summary{cursor:pointer}
pre{white-space:pre-wrap;word-break:break-all;font-size:12px;max-height:360px;overflow:auto;margin:8px 0 0}
.error{background:var(--badb);color:var(--bad);padding:10px 14px;border-radius:8px;margin:14px 0}
.top{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;flex-wrap:wrap}#msg{margin-top:10px;font-size:13px}
</style></head><body><main>
<div class="top"><div><h1>KIC 데이터 수집</h1><div class="sub">기준 {{NOW}} KST · 1분마다 새로고침 · tick 을 10~15분마다 호출하면 작업이 주기대로 돌아갑니다</div></div>
<div><button class="big" onclick="tick()">지금 tick 실행</button></div></div>
<div id="msg"></div>
{{ERROR}}
<h2>작업</h2><div class="card"><table><tr><th>작업</th><th>상태</th><th>마지막 실행</th><th>다음 예정</th><th class="num">소요</th><th>내용</th><th></th></tr>{{JOBS}}</table></div>
<h2>최근 실행 기록</h2>{{RUNS}}
<h2>환경변수</h2><div class="card"><table>{{ENV}}</table></div>
<h2>수집 현황 (status)</h2><details><summary>펼치기</summary>{{SUMMARY}}</details>
</main>
<script>
function secret(){let s=null;try{s=sessionStorage.getItem('kic_secret')}catch(e){}
 if(!s){s=prompt('CRON_SECRET 값을 입력하세요 (이 탭에서만 기억)');if(s){try{sessionStorage.setItem('kic_secret',s)}catch(e){}}}return s}
async function call(url,label){const s=secret();if(!s)return;const m=document.getElementById('msg');m.textContent=label+' 실행 중... (최대 5분)';
 try{const r=await fetch(url,{method:'POST',headers:{Authorization:'Bearer '+s}});const t=await r.text();
  if(r.status===401){try{sessionStorage.removeItem('kic_secret')}catch(e){}}
  m.textContent=label+' → HTTP '+r.status+' '+t.slice(0,300);setTimeout(()=>location.reload(),1500)}catch(e){m.textContent=label+' 오류: '+e}}
function runJob(n){call('/api/jobs/'+n+'/run',n)}
function resetJob(n){call('/api/jobs/'+n+'/reset',n+' 초기화')}
function tick(){call('/api/cron/tick','tick')}
setTimeout(()=>{if(!document.querySelector('details[open]'))location.reload()},60000);
</script></body></html>"""
