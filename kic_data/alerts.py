"""품질 알림 (C8): 프로젝트별 DB 용량, 가격보다 늦은 데이터, 수집 실행 멈춤, 원천 API 오류를 GitHub Issue 로 알립니다.

- 읽기만 합니다 (SELECT). 알림 토큰(GITHUB_TOKEN)·저장소(GITHUB_REPOSITORY)·DB 접속 정보는 환경변수로만 받고 출력하지 않습니다.
- 같은 원인(key)은 열린 이슈 1개에 댓글로 갱신하고, 원인이 사라지면 이슈를 닫습니다 (이슈 본문의 <!-- kic-alert:KEY --> 표시로 찾음).
- 토큰이 없거나 GitHub API 오류여도 예외를 내지 않고 결과에 기록만 합니다 (대시보드·수집은 계속).
- 수집 실행(tick)이 멈추면 수집 쪽에서는 알릴 수 없으므로, 이 점검은 별도 워크플로(.github/workflows/alerts.yml)로 돌립니다.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from . import status
from .config import env

MARK = "kic-alert"
TICK_STALL_HOURS = 3
LATE_MIN_DAYS = 1
TITLE_PREFIX = "[KIC 알림] "
OPINION_URL_ENV = "OPINION_DATABASE_URL"   # 블로그·카페 전용 프로젝트(kic-opinion) 접속 정보 (#10 과 같은 이름)

# 같은 종목의 가격 최신일, 그리고 가격보다 늦게 갱신될 수 있는 데이터의 최신일 (국내 종목)
LATE_SQL = """
SELECT co.ticker, p.pmax,
       (SELECT max(f.trade_date) FROM core.investor_flow_daily f WHERE f.company_id = co.company_id) AS fmax,
       (SELECT max(l.trade_date) FROM core.stock_lending_daily l WHERE l.company_id = co.company_id) AS lmax
FROM core.company co
JOIN (SELECT company_id, max(trade_date) AS pmax FROM core.price_daily GROUP BY company_id) p USING (company_id)
WHERE co.market = 'KR'
"""
LATE_DATASETS = (("investor_flow", "투자자별 수급", 2), ("stock_lending", "주식 대차잔고", 3))   # (이름, 표시, LATE_SQL 열 위치)
LAST_TICK_SQL = "SELECT max(started_at) FROM ops.job_run"


# ---------------------------------------------------------------- 용량

def capacity_item(project: str, size_bytes: int | None, configured: bool = True, warn_mb: int | None = None) -> dict:
    """프로젝트 하나의 용량 표시. 접속 정보가 없으면 configured=False ('설정 안 됨')."""
    if not configured:
        return {"project": project, "configured": False}
    u = status.db_usage(size_bytes, warn_mb)
    if u is None:
        return {"project": project, "configured": True, "error": "크기를 읽지 못함"}
    return {"project": project, "configured": True, **u}


def _db_size(conn) -> int | None:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_database_size(current_database())")
            return int(cur.fetchone()[0])
    except Exception:  # noqa: BLE001
        conn.rollback()
        return None


def connect_opinion():
    """kic-opinion 읽기 전용 연결. 접속 정보(환경변수)가 없으면 None."""
    url = env(OPINION_URL_ENV)
    if not url:
        return None
    import psycopg

    conn = psycopg.connect(url, connect_timeout=30)
    conn.read_only = True
    return conn


def fetch_capacity(main_conn, opinion_conn=None, opinion_configured: bool | None = None) -> list[dict]:
    configured = opinion_configured if opinion_configured is not None else opinion_conn is not None
    out = [capacity_item("main", _db_size(main_conn))]
    out.append(capacity_item("kic-opinion", _db_size(opinion_conn) if opinion_conn is not None else None, configured))
    return out


# ---------------------------------------------------------------- 늦은 데이터 · 실행 멈춤

def late_summary(rows: list[tuple], min_days: int = LATE_MIN_DAYS) -> list[dict]:
    """[(ticker, 가격 최신일, 수급 최신일, 대차 최신일)] → 데이터별 {늦은 종목 수, 최대 늦은 일수, 대상 종목 수}.
    가격보다 min_days 일 이상 늦은 종목만 셉니다. 데이터가 아예 없는 종목은 '늦음'이 아니라 제외(없음은 최신성 표에서 다룸)."""
    out = []
    for name, label, idx in LATE_DATASETS:
        lags = []
        for r in rows:
            pmax, last = r[1], r[idx]
            if pmax is None or last is None:
                continue
            lags.append((pmax - last).days)
        late = [d for d in lags if d >= min_days]
        out.append({"dataset": name, "label": label, "late_companies": len(late), "total": len(lags),
                    "max_days_late": max(late) if late else 0})
    return out


def fetch_late(conn, min_days: int = LATE_MIN_DAYS) -> list[dict]:
    try:
        with conn.cursor() as cur:
            cur.execute(LATE_SQL)
            return late_summary(cur.fetchall(), min_days)
    except Exception:  # noqa: BLE001  표가 아직 없을 때 등
        conn.rollback()
        return []


def tick_status(last_started: datetime | None, now: datetime | None = None, hours: float | None = None) -> dict:
    """마지막 수집 실행 시각이 hours(기본 3시간)보다 오래됐으면 stalled."""
    now = now or datetime.now(timezone.utc)
    try:
        hours = float(hours if hours is not None else env("TICK_STALL_HOURS") or TICK_STALL_HOURS)
    except ValueError:
        hours = float(TICK_STALL_HOURS)
    if last_started is None:
        return {"last": None, "hours_ago": None, "limit_hours": hours, "stalled": True}
    if last_started.tzinfo is None:
        last_started = last_started.replace(tzinfo=timezone.utc)
    ago = (now - last_started).total_seconds() / 3600
    return {"last": last_started, "hours_ago": round(ago, 1), "limit_hours": hours, "stalled": ago > hours}


def fetch_tick(conn, now: datetime | None = None) -> dict:
    try:
        with conn.cursor() as cur:
            cur.execute(LAST_TICK_SQL)
            last = cur.fetchone()[0]
    except Exception:  # noqa: BLE001
        conn.rollback()
        return {"last": None, "hours_ago": None, "limit_hours": None, "stalled": False, "unknown": True}
    return tick_status(last, now)


def health(main_conn, opinion_conn=None, opinion_configured: bool | None = None, now: datetime | None = None) -> dict:
    """대시보드 /api/quality/health: 프로젝트별 용량 + 늦은 데이터 + 수집 실행."""
    return {"capacity": fetch_capacity(main_conn, opinion_conn, opinion_configured), "late": fetch_late(main_conn),
            "tick": fetch_tick(main_conn, now)}


# ---------------------------------------------------------------- 알림 항목

def build_alerts(overview: dict | None, hl: dict) -> list[dict]:
    """[{key, severity, title, body}] — 지금 켜져 있어야 하는 알림 전체. 키가 같으면 같은 원인."""
    items: list[dict] = []
    for a in (overview or {}).get("apis") or []:
        if a.get("status") == "error":
            items.append({"key": f"api:{a['api']}", "severity": "error", "title": f"원천 API 오류: {a['api']}",
                          "body": f"연속 실패 {a.get('consecutive_failures', 0)}회"
                                  f"{' · 한도 초과 의심' if a.get('quota_hit') else ''}\n최근 오류: {a.get('last_error_msg') or '-'}"})
    for c in hl.get("capacity") or []:
        if c.get("configured") and c.get("over"):
            items.append({"key": f"capacity:{c['project']}", "severity": "warn",
                          "title": f"DB 용량 경고: {c['project']} {c['mb']}MB",
                          "body": f"{c['project']} 프로젝트 {c['mb']}MB / 한도 {c['limit_mb']}MB ({c['pct']}%), 경고 기준 {c['warn_mb']}MB 초과.\n"
                                  "한도에 닿으면 수집 쓰기가 실패합니다."})
    for d in hl.get("late") or []:
        if d.get("late_companies"):
            items.append({"key": f"late:{d['dataset']}", "severity": "warn",
                          "title": f"가격보다 늦은 데이터: {d['label']}",
                          "body": f"{d['total']}개 종목 중 {d['late_companies']}개가 가격 최신일보다 늦음 (최대 {d['max_days_late']}일)."})
    t = hl.get("tick") or {}
    if t.get("stalled"):
        ago = "기록 없음" if t.get("hours_ago") is None else f"{t['hours_ago']}시간 전"
        items.append({"key": "stalled:tick", "severity": "warn", "title": "수집 실행 멈춤",
                      "body": f"마지막 수집 실행: {ago} (기준 {t.get('limit_hours')}시간 초과)."})
    return items


def digest(item: dict) -> str:
    return hashlib.sha1(json.dumps([item["severity"], item["title"], item["body"]], ensure_ascii=False).encode()).hexdigest()[:10]


# ---------------------------------------------------------------- GitHub Issue

class GitHubIssues:
    """GitHub Issues REST 호출 (requests). 테스트는 같은 메서드를 가진 모의 객체를 씁니다."""

    def __init__(self, token: str, repo: str, session=None):
        self.repo, self.session = repo, session
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    def _s(self):
        if self.session is None:
            import requests

            self.session = requests
        return self.session

    def _call(self, method: str, path: str, **kw):
        r = getattr(self._s(), method)(f"https://api.github.com/repos/{self.repo}{path}", headers=self.headers, timeout=20, **kw)
        if r.status_code >= 300:
            raise RuntimeError(f"GitHub {method.upper()} {path.split('?')[0]} HTTP {r.status_code}")
        return r.json() if r.content else {}

    def list_open(self) -> list[dict]:
        return self._call("get", f"/issues?labels={MARK}&state=open&per_page=100")

    def create(self, title: str, body: str) -> dict:
        return self._call("post", "/issues", json={"title": title, "body": body, "labels": [MARK]})

    def comment(self, number: int, text: str) -> None:
        self._call("post", f"/issues/{number}/comments", json={"body": text})

    def update_body(self, number: int, body: str) -> None:
        self._call("patch", f"/issues/{number}", json={"body": body})

    def close(self, number: int) -> None:
        self._call("patch", f"/issues/{number}", json={"state": "closed"})


def get_client() -> GitHubIssues | None:
    token, repo = env("GITHUB_TOKEN"), env("GITHUB_REPOSITORY")
    return GitHubIssues(token, repo) if token and repo else None


def issue_body(item: dict) -> str:
    return f"<!-- {MARK}:{item['key']} -->\n<!-- digest:{digest(item)} -->\n**{item['severity']}** — {item['body']}"


def _key_of(issue: dict) -> str | None:
    import re

    m = re.search(rf"<!-- {MARK}:(\S+) -->", issue.get("body") or "")
    return m.group(1) if m else None


def sync_issues(client, items: list[dict], now: datetime | None = None) -> dict:
    """켜져 있어야 하는 알림(items)에 맞춰 이슈를 열고·갱신하고·닫습니다. 예외를 내지 않고 errors 에 기록."""
    res = {"opened": 0, "updated": 0, "closed": 0, "unchanged": 0, "errors": [], "skipped": None}
    if client is None:
        res["skipped"] = "알림 토큰(GITHUB_TOKEN)·저장소(GITHUB_REPOSITORY) 없음"
        return res
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    try:
        open_issues = client.list_open()
    except Exception as exc:  # noqa: BLE001
        res["errors"].append(f"목록: {exc}")
        return res
    by_key: dict[str, dict] = {}
    for iss in open_issues:
        k = _key_of(iss)
        if k and k not in by_key:   # 같은 원인 이슈가 둘이면 첫 번째만 갱신 대상으로 삼음
            by_key[k] = iss
    wanted = {i["key"] for i in items}
    for item in items:
        iss = by_key.get(item["key"])
        try:
            if iss is None:
                client.create(TITLE_PREFIX + item["title"], issue_body(item))
                res["opened"] += 1
            elif f"digest:{digest(item)}" in (iss.get("body") or ""):
                res["unchanged"] += 1
            else:
                client.comment(iss["number"], f"{stamp} 갱신 — {item['severity']}: {item['body']}")
                client.update_body(iss["number"], issue_body(item))
                res["updated"] += 1
        except Exception as exc:  # noqa: BLE001
            res["errors"].append(f"{item['key']}: {exc}")
    for key, iss in by_key.items():
        if key in wanted:
            continue
        try:
            client.comment(iss["number"], f"{stamp} 해결됨 — 원인이 사라져 이슈를 닫습니다.")
            client.close(iss["number"])
            res["closed"] += 1
        except Exception as exc:  # noqa: BLE001
            res["errors"].append(f"{key}: {exc}")
    return res
