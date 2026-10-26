"""웹 자동 실행기: cli.py 의 수집 명령을 시간표대로 나눠 실행합니다 (Vercel 서버리스용).

서버리스 함수는 한 번 호출에 최대 300초라 '전체 수집을 한 번에' 돌릴 수 없습니다. 그래서
  - 작업(Job)마다 주기를 두고, ops.job_state 에 마지막 완료 시각을 남깁니다.
  - /api/cron/tick 을 한 번 부를 때마다 '지금 할 차례인' 작업을 시간 예산(기본 240초) 안에서 차례로 실행합니다.
  - 종목이 많은 작업은 chunk 개씩 나눠 처리하고(cursor), 다음 tick 에서 이어서 합니다.
tick 을 10~15분마다 불러 주면(GitHub Actions 또는 외부 크론) 모든 작업이 주기대로 돌아갑니다.

키(환경변수)가 없는 작업은 실패가 아니라 '건너뜀' 으로 기록하고, 키를 넣으면 다음 tick 부터 실행됩니다.
"""
from __future__ import annotations

import io
import os
import time
import traceback
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from . import db

HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
WEEK = timedelta(days=7)
ONCE = None  # 한 번 끝나면 다시 하지 않음 (대시보드에서 수동 재실행 가능)

LOG_LIMIT = 20000  # job_run.log 에 남길 최대 글자 수
STALE_RUNNING = timedelta(minutes=6)  # 함수가 시간 초과로 끊겨 running 이 남은 경우
RETRY_AFTER_FAIL = timedelta(minutes=30)
RETRY_AFTER_SKIP = timedelta(hours=1)


@dataclass
class Job:
    name: str
    label: str
    command: str  # cli.py 의 함수 이름 (cmd_*)
    kwargs: dict = field(default_factory=dict)  # argparse Namespace 로 넘길 값 (date 는 호출 때 계산하도록 callable 허용)
    every: timedelta | None = HOUR
    needs: tuple[str, ...] = ()  # 모두 있어야 하는 환경변수
    needs_any: tuple[str, ...] = ()  # 하나 이상 있어야 하는 환경변수
    split: tuple[str, ...] = ()  # 종목을 나눠 처리할 시장 ("KR", "US")
    chunk: int = 6
    group: str = "매시간"

    def namespace(self) -> Namespace:
        return Namespace(**{k: (v() if callable(v) else v) for k, v in self.kwargs.items()})

    def missing_env(self, getenv: Callable[[str], str | None]) -> list[str]:
        miss = [k for k in self.needs if not getenv(k)]
        if self.needs_any and not any(getenv(k) for k in self.needs_any):
            miss.append(" 또는 ".join(self.needs_any))
        return miss


def _years_ago(n: int):
    return lambda: date.today().year - n


DB = ("DATABASE_URL",)
NAVER = ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET")

# 실행 순서 = 목록 순서. 준비 → 과거분(1회) → 매시간 수집 → 감성·언급량 → 재무 → 지표 재계산
# 재무는 DART 호출이 많아(국내 종목 1개 5년치 ≈ 35초) 자주 도는 수집이 밀리지 않게 뒤에 둡니다.
JOBS: list[Job] = [
    # 준비 (매일 1회: 종목을 바꾸면 다음 날 자동 반영, 급하면 대시보드에서 수동 실행)
    Job("apply-schema", "DB 스키마 적용", "cmd_apply_schema", every=DAY, needs=DB, group="준비"),
    Job("seed-companies", "종목 등록 (universe.py)", "cmd_seed", every=DAY, needs=DB, group="준비"),
    Job("sync-dart-codes", "DART 고유번호 연결", "cmd_sync_dart", every=DAY, needs=DB + ("DART_API_KEY",), group="준비"),
    Job("company-info", "업종코드·발행주식수", "cmd_company_info", every=DAY,
        needs=DB + ("DART_API_KEY", "FSC_API_KEY", "SEC_USER_AGENT"), split=("KR", "US"), chunk=8, group="준비"),
    # 과거분 (한 번만): 시계열·학습용 5년치
    Job("backfill-kr-prices", "국내 시세 5년", "cmd_kr_prices", {"days": 1825}, every=ONCE,
        needs=DB + ("FSC_API_KEY",), split=("KR",), chunk=5, group="과거분(1회)"),
    Job("backfill-us-prices", "해외 시세 5년", "cmd_us_prices", {"days": 1825}, every=ONCE,
        needs=DB, split=("US",), chunk=6, group="과거분(1회)"),
    Job("backfill-indices", "시장 지수 5년", "cmd_indices", {"days": 1825}, every=ONCE, needs=DB, group="과거분(1회)"),
    Job("backfill-etf", "ETF 시세 5년", "cmd_etf", {"days": 1825}, every=ONCE, needs=DB + ("FSC_API_KEY",), group="과거분(1회)"),
    Job("backfill-macro", "거시지표 5년", "cmd_macro", {"years": 5}, every=ONCE, needs=DB,
        needs_any=("ECOS_API_KEY", "FRED_API_KEY"), group="과거분(1회)"),
    Job("backfill-disclosures", "공시 목록 1년", "cmd_disclosures", {"days": 365, "types": "A,B,I"}, every=ONCE,
        needs=DB + ("DART_API_KEY", "SEC_USER_AGENT"), split=("KR", "US"), chunk=6, group="과거분(1회)"),
    Job("backfill-news", "뉴스 30일", "cmd_news", {"days": 30, "max_items": 300}, every=ONCE,
        needs=DB + NAVER, split=("KR", "US"), chunk=6, group="과거분(1회)"),
    Job("backfill-community", "블로그·카페 30일", "cmd_community",
        {"days": 30, "sources": "naver_blog,naver_cafe", "pages": 2}, every=ONCE,
        needs=DB + NAVER, split=("KR", "US"), chunk=6, group="과거분(1회)"),
    Job("backfill-trends", "검색 관심도 1년", "cmd_trends", {"days": 365}, every=ONCE, needs=DB,
        needs_any=("NCP_APIHUB_KEY_ID", "NAVER_CLIENT_ID"), group="과거분(1회)"),
    Job("backfill-dart-quarterly", "국내 분기 재무 2년", "cmd_dart_q", {"from_year": _years_ago(2)}, every=ONCE,
        needs=DB + ("DART_API_KEY",), split=("KR",), chunk=3, group="과거분(1회)"),
    # 매시간
    Job("kr-prices", "국내 시세", "cmd_kr_prices", {"days": 10}, needs=DB + ("FSC_API_KEY",), split=("KR",), chunk=10),
    Job("us-prices", "해외 시세", "cmd_us_prices", {"days": 10}, needs=DB, split=("US",), chunk=10),
    Job("indices", "시장 지수", "cmd_indices", {"days": 10}, needs=DB),
    Job("etf-prices", "ETF 시세", "cmd_etf", {"days": 10}, needs=DB + ("FSC_API_KEY",)),
    Job("macro", "거시·업황 지표", "cmd_macro", {"years": 1}, needs=DB, needs_any=("ECOS_API_KEY", "FRED_API_KEY")),
    Job("disclosures", "공시 목록", "cmd_disclosures", {"days": 3, "types": "A,B,I"},
        needs=DB + ("DART_API_KEY", "SEC_USER_AGENT"), split=("KR", "US"), chunk=12),
    Job("news", "네이버 뉴스", "cmd_news", {"days": 2, "max_items": 300}, needs=DB + NAVER, split=("KR", "US"), chunk=12),
    Job("finnhub-news", "해외 영문 뉴스", "cmd_finnhub", {"days": 3}, needs=DB + ("FINNHUB_API_KEY",), split=("US",), chunk=20),
    Job("community", "블로그·카페 반응", "cmd_community", {"days": 2, "sources": "naver_blog,naver_cafe", "pages": 1},
        needs=DB + NAVER, split=("KR", "US"), chunk=12),
    Job("trends", "검색 관심도", "cmd_trends", {"days": 14}, needs=DB, needs_any=("NCP_APIHUB_KEY_ID", "NAVER_CLIENT_ID")),
    Job("sentiment", "감성 점수", "cmd_sentiment", {"method": None, "limit": None, "rescore": False}, needs=DB),
    Job("mentions", "언급량 스냅샷", "cmd_pulse", {"tickers": None, "max_age": 0, "market_hours": False},
        needs=DB + NAVER, split=("KR", "US"), chunk=12),
    # 재무: 연간은 매주(5년치), 분기는 매일 올해분만 확인(과거 2년치는 과거분에서 1회)
    Job("dart-annual", "국내 연간 재무", "cmd_dart", {"from_year": _years_ago(5)}, every=WEEK,
        needs=DB + ("DART_API_KEY",), split=("KR",), chunk=4, group="재무"),
    Job("dart-quarterly", "국내 분기 재무(올해)", "cmd_dart_q", {"from_year": _years_ago(0)}, every=DAY,
        needs=DB + ("DART_API_KEY",), split=("KR",), chunk=6, group="재무"),
    Job("sec-annual", "해외 연간 재무", "cmd_sec", {"from_year": _years_ago(5)}, every=DAY,
        needs=DB + ("SEC_USER_AGENT",), split=("US",), chunk=5, group="재무"),
    Job("sec-quarterly", "해외 분기 재무", "cmd_sec_q", {"from_year": _years_ago(2)}, every=DAY,
        needs=DB + ("SEC_USER_AGENT",), split=("US",), chunk=5, group="재무"),
    Job("build-quarterly", "분기 단독 값·TTM", "cmd_build_quarterly", every=DAY, needs=DB, group="재무"),
    Job("build-peer-groups", "Peer Group", "cmd_build_peers", every=DAY, needs=DB, group="재무"),
    Job("build-features", "5개 관점 지표 재계산", "cmd_build_features", needs=DB),
]
JOB_BY_NAME = {j.name: j for j in JOBS}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- 상태 저장소

class DbState:
    """ops.job_state / ops.job_run (sql/013). 테스트에서는 같은 메서드를 가진 메모리 저장소를 씁니다.

    긴 작업(과거분 5년치 등)이 도는 동안 이 연결은 쉬고 있어서 Neon 이 끊을 수 있습니다.
    connect 를 넘기면 끊긴 연결을 다시 열어 한 번 더 시도합니다.
    """

    def __init__(self, conn, connect: Callable | None = None):
        self.conn = conn
        self._connect = connect

    def _run(self, fn):
        try:
            return fn(self.conn)
        except Exception as exc:  # noqa: BLE001
            if self._connect is None or not _is_disconnect(exc, self.conn):
                raise
            try:
                self.conn.close()
            except Exception:  # noqa: BLE001
                pass
            self.conn = self._connect()
            return fn(self.conn)

    def all(self) -> dict[str, dict]:
        def q(conn):
            with conn.cursor() as cur:
                cur.execute("SELECT job, cursor, last_status, last_started, last_finished, last_complete, last_seconds, "
                            "running_since, detail FROM ops.job_state")
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
            conn.commit()
            return {r[0]: dict(zip(cols, r)) for r in rows}
        return self._run(q)

    def mark_running(self, job: str, started: datetime) -> None:
        def q(conn):
            with conn.cursor() as cur:
                cur.execute("INSERT INTO ops.job_state (job, running_since, last_started) VALUES (%s, %s, %s) "
                            "ON CONFLICT (job) DO UPDATE SET running_since = EXCLUDED.running_since, "
                            "last_started = EXCLUDED.last_started", [job, started, started])
            conn.commit()
        self._run(q)

    def finish(self, job: str, *, status: str, cursor: int, complete: bool, seconds: float, detail: str,
               finished: datetime, trigger: str, tickers: str | None, log: str, started: datetime) -> None:
        def q(conn):
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ops.job_state SET cursor = %s, last_status = %s, last_finished = %s, "
                    "last_complete = CASE WHEN %s THEN %s ELSE last_complete END, last_seconds = %s, "
                    "running_since = NULL, detail = %s WHERE job = %s",
                    [cursor, status, finished, complete, finished, round(seconds, 1), detail, job])
                cur.execute(
                    "INSERT INTO ops.job_run (job, trigger, tickers, started_at, finished_at, status, seconds, log) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    [job, trigger, tickers, started, finished, status, round(seconds, 1), log[-LOG_LIMIT:]])
            conn.commit()
        self._run(q)

    def reset(self, job: str) -> None:
        def q(conn):
            with conn.cursor() as cur:
                cur.execute("UPDATE ops.job_state SET cursor = 0, last_complete = NULL, running_since = NULL "
                            "WHERE job = %s", [job])
            conn.commit()
        self._run(q)

    def prune(self, days: int = 14) -> None:
        def q(conn):
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ops.job_run WHERE started_at < now() - make_interval(days => %s)", [days])
            conn.commit()
        self._run(q)

    def recent_runs(self, limit: int = 60) -> list[dict]:
        def q(conn):
            with conn.cursor() as cur:
                cur.execute("SELECT run_id, job, trigger, tickers, started_at, finished_at, status, seconds, log "
                            "FROM ops.job_run ORDER BY started_at DESC LIMIT %s", [limit])
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
            conn.commit()
            return [dict(zip(cols, r)) for r in rows]
        return self._run(q)

    def try_lock(self) -> bool:
        # Neon 풀러(-pooler) 주소는 트랜잭션 단위로 연결을 바꿔 써서 세션 잠금이 보장되지 않습니다.
        # 이때는 잠금을 생략하고 GitHub Actions 의 concurrency 설정(동시 실행 1개)에 맡깁니다.
        if "-pooler" in (os.environ.get("DATABASE_URL") or ""):
            return True
        with self.conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext('kic_jobs_tick'))")
            ok = cur.fetchone()[0]
        self.conn.commit()
        return bool(ok)

    def unlock(self) -> None:
        if "-pooler" in (os.environ.get("DATABASE_URL") or ""):
            return
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext('kic_jobs_tick'))")
            self.conn.commit()
        except Exception:  # noqa: BLE001  연결이 끊겼으면 잠금도 이미 풀림
            pass


def _is_disconnect(exc: Exception, conn) -> bool:
    """연결이 끊겨서 난 오류인지 (SQL 오류는 다시 시도하지 않음)."""
    try:
        import psycopg
    except ImportError:
        return False
    if isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)):
        return True
    return bool(getattr(conn, "closed", False) or getattr(conn, "broken", False))


def ensure_schema(conn) -> bool:
    """ops 테이블이 없으면 sql/*.sql 전체를 적용합니다 (처음 배포 직후 첫 tick). 적용했으면 True."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('ops.job_state') IS NOT NULL")
        exists = cur.fetchone()[0]
    conn.commit()
    if exists:
        return False
    db.apply_schema(conn)
    return True


# ---------------------------------------------------------------- 판단

def due_reason(job: Job, st: dict | None, now: datetime, missing: list[str]) -> str | None:
    """실행할 차례면 이유 문자열, 아니면 None."""
    st = st or {}
    running = st.get("running_since")
    if running and now - running < STALE_RUNNING:
        return None  # 다른 tick 이 실행 중
    last_status, finished = st.get("last_status"), st.get("last_finished")
    if missing:
        if last_status == "skipped" and finished and now - finished < RETRY_AFTER_SKIP:
            return None
        return "키 확인"
    if running:  # 시간 초과로 끊긴 것으로 보고 다시
        return "이전 실행이 끊김"
    if last_status == "failed" and finished and now - finished < RETRY_AFTER_FAIL:
        return None
    if st.get("cursor"):
        return "이어서"
    complete = st.get("last_complete")
    if complete is None:
        return "처음"
    if job.every is ONCE:
        return None
    return "주기" if now - complete >= job.every - timedelta(minutes=5) else None


def next_due(job: Job, st: dict | None) -> datetime | None:
    st = st or {}
    if st.get("cursor") or not st.get("last_complete"):
        return None  # 바로
    if job.every is ONCE:
        return None
    return st["last_complete"] + job.every


def ticker_universe(conn, markets: tuple[str, ...]) -> list[tuple[str, str]]:
    token = db.ONLY_TICKERS.set(None)
    try:
        return [(m, r[1]) for m in markets for r in db.list_companies(conn, m)]
    finally:
        db.ONLY_TICKERS.reset(token)


# ---------------------------------------------------------------- 실행

def _prepare_runtime() -> None:
    """서버리스 환경은 /tmp 만 쓸 수 있어 yfinance 캐시 위치를 옮깁니다."""
    os.environ.setdefault("XDG_CACHE_HOME", "/tmp/.cache")
    try:
        import yfinance as yf

        yf.set_tz_cache_location("/tmp/.cache/yfinance")
    except Exception:  # yfinance 미설치 또는 구버전
        pass


def run_job(job: Job, state, *, conn=None, trigger: str = "tick", commands=None, getenv=None,
            universe: list[tuple[str, str]] | None = None) -> dict:
    """작업 하나(나눠 처리하는 작업이면 한 묶음)를 실행하고 결과를 기록합니다."""
    from . import cli

    commands = commands or cli
    getenv = getenv or (lambda k: os.environ.get(k))
    started = _now()
    t0 = time.monotonic()
    st = state.all().get(job.name) or {}
    missing = job.missing_env(getenv)
    if missing:
        detail = "키 없음: " + ", ".join(missing)
        state.mark_running(job.name, started)
        state.finish(job.name, status="skipped", cursor=st.get("cursor") or 0, complete=False, seconds=0.0,
                     detail=detail, finished=_now(), trigger=trigger, tickers=None, log=detail, started=started)
        return {"job": job.name, "status": "skipped", "detail": detail}

    cursor = st.get("cursor") or 0
    chunk_keys, next_cursor, complete = None, 0, True
    if job.split:
        if universe is not None:
            keys = universe
        elif hasattr(state, "_run"):  # 끊긴 연결이면 다시 열어서 읽기
            def q(c):
                out = ticker_universe(c, job.split)
                c.commit()  # 읽기 트랜잭션을 열어 둔 채로 긴 작업에 들어가지 않게
                return out
            keys = state._run(q)
        else:
            keys = ticker_universe(conn, job.split)
        if cursor >= len(keys):
            cursor = 0
        chunk_keys = keys[cursor:cursor + job.chunk]
        next_cursor = cursor + job.chunk
        complete = next_cursor >= len(keys)
        if complete:
            next_cursor = 0
    tickers = None if chunk_keys is None else ",".join(f"{m}:{t}" for m, t in chunk_keys)

    state.mark_running(job.name, started)
    buf = io.StringIO()
    token = db.ONLY_TICKERS.set(None if chunk_keys is None else frozenset(chunk_keys))
    status = "ok"
    try:
        _prepare_runtime()
        fn = getattr(commands, job.command)
        with redirect_stdout(buf), redirect_stderr(buf):
            rc = fn(job.namespace())
        if rc not in (0, None):
            status = "warn"  # 일부 종목·시리즈만 실패: 로그 확인. 반복 실행으로 막히지 않게 진행은 계속
    except Exception as exc:  # noqa: BLE001  작업 하나가 실패해도 다음 작업은 실행
        status = "failed"
        buf.write(f"\n[FAIL] {type(exc).__name__}: {exc}\n")
        buf.write("".join(traceback.format_exception(exc)[-6:]))
        next_cursor, complete = cursor, False  # 같은 묶음을 나중에 다시
    finally:
        db.ONLY_TICKERS.reset(token)
    seconds = time.monotonic() - t0
    if status != "failed" and not complete:
        status = "partial" if status == "ok" else status
    log = buf.getvalue()
    detail = _summary(log, status, tickers, complete)
    state.finish(job.name, status=status, cursor=next_cursor, complete=complete and status != "failed",
                 seconds=seconds, detail=detail, finished=_now(), trigger=trigger, tickers=tickers, log=log,
                 started=started)
    return {"job": job.name, "status": status, "seconds": round(seconds, 1), "tickers": tickers, "detail": detail,
            "more": status != "failed" and not complete}


def _summary(log: str, status: str, tickers: str | None, complete: bool) -> str:
    lines = [ln for ln in log.splitlines() if ln.strip()]
    fails = [ln for ln in lines if ln.startswith("[FAIL]")]
    oks = sum(ln.startswith("[OK]") for ln in lines)
    parts = [f"OK {oks}"]
    if fails:
        parts.append(f"FAIL {len(fails)}: {fails[0][7:120]}")
    if tickers and not complete:
        parts.append("다음 묶음 남음")
    return " · ".join(parts)


def tick(state, *, conn=None, budget: float | None = None, hard_limit: float = 280.0, only: str | None = None,
         commands=None, getenv=None, clock=time.monotonic, universe_for=None) -> dict:
    """차례가 된 작업을 시간 예산 안에서 순서대로 실행합니다."""
    getenv = getenv or (lambda k: os.environ.get(k))
    budget = budget if budget is not None else float(getenv("TICK_BUDGET_SECONDS") or 240)
    t0 = clock()
    ran, waiting = [], []
    states = state.all()
    for job in JOBS:
        if only and job.group != only and job.name != only:
            continue
        st = states.get(job.name)
        reason = due_reason(job, st, _now(), job.missing_env(getenv))
        if reason is None:
            continue
        elapsed = clock() - t0
        expected = float((st or {}).get("last_seconds") or 20) * 1.2
        if elapsed > budget or elapsed + expected > hard_limit:
            waiting.append(job.name)
            continue
        universe = universe_for(job) if universe_for else None
        while True:  # 나눠 처리하는 작업은 시간이 남는 동안 다음 묶음도 이어서
            res = run_job(job, state, conn=conn, commands=commands, getenv=getenv, universe=universe)
            res["reason"] = reason
            ran.append(res)
            if not res.get("more"):
                break
            elapsed = clock() - t0
            if elapsed + max(res.get("seconds") or 0, 5) * 1.2 > min(budget, hard_limit):
                waiting.append(job.name)
                break
            reason = "이어서"
    return {"ran": ran, "waiting": waiting, "seconds": round(clock() - t0, 1)}


def overview(state, getenv=None) -> list[dict]:
    """대시보드용: 작업별 상태 + 다음 예정 시각."""
    getenv = getenv or (lambda k: os.environ.get(k))
    states = state.all()
    now = _now()
    out = []
    for job in JOBS:
        st = states.get(job.name) or {}
        missing = job.missing_env(getenv)
        out.append({
            "name": job.name, "label": job.label, "group": job.group,
            "every": "1회" if job.every is ONCE else {HOUR: "매 실행", DAY: "매일", WEEK: "매주"}.get(job.every, str(job.every)),
            "status": st.get("last_status"), "detail": st.get("detail"),
            "last_finished": st.get("last_finished"), "last_complete": st.get("last_complete"),
            "seconds": None if st.get("last_seconds") is None else float(st["last_seconds"]),
            "running": bool(st.get("running_since") and now - st["running_since"] < STALE_RUNNING),
            "cursor": st.get("cursor") or 0, "missing": missing,
            "due": due_reason(job, st, now, missing) is not None, "next": next_due(job, st),
        })
    return out


STATUS_KO = {"ok": "정상", "warn": "일부 실패", "failed": "실패", "skipped": "건너뜀", "partial": "진행 중"}


def markdown_summary(result: dict, ov: list[dict]) -> str:
    """GitHub Actions 실행 요약($GITHUB_STEP_SUMMARY)용 표."""
    from zoneinfo import ZoneInfo

    kst = ZoneInfo("Asia/Seoul")
    fmt = lambda ts: ts.astimezone(kst).strftime("%m-%d %H:%M") if ts else "-"  # noqa: E731
    lines = [f"### 이번 실행: {len(result['ran'])}건, {result['seconds']}초", "",
             "| 작업 | 결과 | 초 | 내용 |", "|---|---|---|---|"]
    for r in result["ran"]:
        lines.append(f"| {r['job']} | {STATUS_KO.get(r['status'], r['status'])} | {r.get('seconds') or 0} | "
                     f"{(r.get('detail') or '').replace('|', '/')[:120]} |")
    if result["waiting"]:
        lines += ["", "다음 실행으로 넘긴 작업: " + ", ".join(result["waiting"])]
    lines += ["", "### 전체 작업 상태", "", "| 묶음 | 작업 | 주기 | 상태 | 마지막 완료(KST) | 비고 |", "|---|---|---|---|---|---|"]
    for j in ov:
        note = ("필요: " + ", ".join(j["missing"])) if j["missing"] else (j["detail"] or "")
        lines.append(f"| {j['group']} | {j['label']} | {j['every']} | {STATUS_KO.get(j['status'], j['status'] or '대기')} | "
                     f"{fmt(j['last_complete'])} | {note.replace('|', '/')[:100]} |")
    return "\n".join(lines) + "\n"
