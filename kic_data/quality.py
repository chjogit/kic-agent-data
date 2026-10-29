"""데이터 품질 기록·조회 (sql/017 quality 스키마).

쓰기: 수집 작업(jobs.run_job)이 끝날 때마다 record_job 이 작업 → 원천 API 상태를 갱신하고,
      실패·일부 실패는 quality.checks 에 이상 이벤트로 남깁니다.
      같은 작업이 다시 정상으로 끝나면 그 작업의 이벤트는 '해결됨'(resolved)으로 바뀌어 대시보드에서 빠집니다.
      작업마다 해결 안 된 이벤트는 최신 1건만 남깁니다 (이전 것은 '대체됨'으로 해결 처리).
읽기: 품질 대시보드(main.py 의 /quality, /api/quality/*). 읽기 함수는 SELECT 만 합니다.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

# 작업 이름 → 원천 API. 내부 계산(스키마 적용·감성·TTM·지표 재계산 등)은 외부 API 가 아니라 빠집니다.
API_OF_JOB = {
    "sync-dart-codes": "dart", "company-info": "dart", "backfill-disclosures": "dart", "disclosures": "dart",
    "backfill-dart-annual": "dart", "backfill-dart-quarterly": "dart", "backfill-dart-quarterly-5y": "dart", "dart-annual": "dart", "dart-quarterly": "dart",
    "report-exposure": "dart", "debt-terms": "dart",
    "sec-annual": "sec", "sec-quarterly": "sec",
    "backfill-kr-prices": "fsc", "kr-prices": "fsc", "backfill-etf": "fsc", "etf-prices": "fsc",
    "lending": "data.go.kr", "bond-issue": "data.go.kr", "backfill-customs": "data.go.kr", "customs": "data.go.kr",
    "backfill-us-prices": "yfinance", "us-prices": "yfinance", "backfill-indices": "yfinance", "indices": "yfinance", "us-etf-prices": "yfinance",
    "backfill-macro": "ecos·fred", "macro": "ecos·fred",
    "backfill-news": "naver", "backfill-news-gaps": "naver", "backfill-person-news": "naver", "backfill-community-gaps": "naver", "news": "naver", "backfill-community": "naver", "backfill-blog-deep": "naver", "community": "naver", "mentions": "naver",
    "backfill-trends": "naver_datalab", "trends": "naver_datalab",
    "finnhub-news": "finnhub", "youtube": "youtube", "youtube-rest": "youtube",
    "sync-kospi200": "naver_finance", "backfill-investor-flows": "naver_finance", "investor-flows": "naver_finance",
}
API_NAMES = sorted(set(API_OF_JOB.values()))
SCHEMAS = ("core", "ops", "quality", "rag", "raw")

QUOTA_RE = re.compile(r"\b429\b|too many requests|quota|rate.?limit|한도|LIMITED_NUMBER", re.I)
STALE_AFTER = timedelta(days=2)  # 이보다 오래 성공이 없으면 경고


def quota_hit(log: str) -> bool:
    return bool(QUOTA_RE.search(log or ""))


def first_error(log: str, detail: str | None = None) -> str:
    for ln in (log or "").splitlines():
        if ln.startswith("[FAIL]"):
            return ln[7:300].strip()
    return (detail or "")[:300]


def api_status(row: dict, now: datetime | None = None) -> str:
    """신호등: ok / warn / error / unknown."""
    now = now or datetime.now(timezone.utc)
    fails = row.get("consecutive_failures") or 0
    success = row.get("last_success")
    if fails >= 2:
        return "error"
    if fails == 1 or row.get("quota_hit"):
        return "warn"
    if success is None:
        return "error" if row.get("last_error") else "unknown"
    if now - success > STALE_AFTER:
        return "warn"
    return "ok"


# ---------------------------------------------------------------- 쓰기 (수집 작업에서)

JOB_RULES = ("job_failed", "job_partial_fail")  # 작업 결과로 생기는 이벤트 (작업 이름이 target)
OK_STATUSES = ("ok", "partial")  # partial: 이번 묶음은 정상, 다음 묶음 남음

# 해결 표시: resolved=true 와 details.resolved_at·resolved_by (recovered / superseded / job_removed)
_RESOLVE_SET = ("resolved = true, details = coalesce(details, '{}'::jsonb) || "
                "jsonb_build_object('resolved_at', %s::timestamptz, 'resolved_by', %s::text)")


def resolve_reason(status: str) -> str:
    """작업이 끝났을 때 그 작업의 이전 이벤트를 해결 처리하는 이유. 정상이면 회복, 아니면 새 이벤트로 대체."""
    return "recovered" if status in OK_STATUSES else "superseded"


def resolve_job_checks(cur, job: str, status: str, finished: datetime) -> None:
    """이 작업의 해결 안 된 이벤트를 해결 처리합니다 (새 실패·일부 실패는 이 뒤에 다시 남김)."""
    cur.execute(f"UPDATE quality.checks SET {_RESOLVE_SET} "
                "WHERE NOT resolved AND target = %s AND rule_id = ANY(%s) AND check_time <= %s",
                [finished, resolve_reason(status), job, list(JOB_RULES), finished])


def resolve_stale(conn, known_jobs, now: datetime | None = None) -> None:
    """이전 기록 정리 (매 작업 뒤, 몇 ms): 그 뒤 정상으로 끝난 작업, 같은 작업의 더 새 이벤트가 있는 것,
    작업 목록에 없는 작업(삭제됐거나 병합 전 브랜치에서만 돈 작업)의 이벤트를 해결 처리합니다."""
    now = now or datetime.now(timezone.utc)
    rules = list(JOB_RULES)
    with conn.cursor() as cur:
        cur.execute(f"UPDATE quality.checks c SET {_RESOLVE_SET} FROM ops.job_state s "
                    "WHERE NOT c.resolved AND c.rule_id = ANY(%s) AND s.job = c.target "
                    "AND s.last_status = ANY(%s) AND s.last_finished > c.check_time",
                    [now, "recovered", rules, list(OK_STATUSES)])
        cur.execute(f"UPDATE quality.checks c SET {_RESOLVE_SET} "
                    "WHERE NOT c.resolved AND c.rule_id = ANY(%s) AND EXISTS (SELECT 1 FROM quality.checks n "
                    "WHERE n.target = c.target AND n.rule_id = ANY(%s) AND n.check_time > c.check_time)",
                    [now, "superseded", rules, rules])
        cur.execute(f"UPDATE quality.checks SET {_RESOLVE_SET} "
                    "WHERE NOT resolved AND rule_id = ANY(%s) AND NOT (target = ANY(%s))",
                    [now, "job_removed", rules, sorted(known_jobs)])
    conn.commit()


def record_job(conn, job: str, *, status: str, seconds: float, detail: str | None, log: str,
               finished: datetime) -> None:
    """작업 결과를 원천 API 상태·이상 이벤트로 남깁니다. 키가 없어 건너뛴 작업은 기록하지 않습니다.
    이전 이벤트는 먼저 해결 처리하고(정상이면 회복, 아니면 대체), 이번이 실패·일부 실패면 새로 남깁니다."""
    api = API_OF_JOB.get(job)
    if api is None or status == "skipped":
        return
    failed = status == "failed"
    quota = quota_hit(log) if status in ("failed", "warn") else False
    msg = first_error(log, detail) if status in ("failed", "warn") else None
    ms = int(seconds * 1000)
    with conn.cursor() as cur:
        if failed:
            cur.execute(
                "INSERT INTO quality.api_status (api_name, last_error, last_error_msg, consecutive_failures, quota_hit, "
                "last_run_duration_ms, updated_at) VALUES (%s, %s, %s, 1, %s, %s, now()) "
                "ON CONFLICT (api_name) DO UPDATE SET last_error = EXCLUDED.last_error, "
                "last_error_msg = EXCLUDED.last_error_msg, "
                "consecutive_failures = quality.api_status.consecutive_failures + 1, quota_hit = EXCLUDED.quota_hit, "
                "last_run_duration_ms = EXCLUDED.last_run_duration_ms, updated_at = now()",
                [api, finished, msg, quota, ms])
        else:  # ok · partial(다음 묶음 남음) · warn(일부 종목 실패) 는 API 자체는 응답한 것으로 봄
            cur.execute(
                "INSERT INTO quality.api_status (api_name, last_success, consecutive_failures, quota_hit, "
                "last_run_duration_ms, updated_at) VALUES (%s, %s, 0, %s, %s, now()) "
                "ON CONFLICT (api_name) DO UPDATE SET last_success = EXCLUDED.last_success, consecutive_failures = 0, "
                "quota_hit = EXCLUDED.quota_hit, last_run_duration_ms = EXCLUDED.last_run_duration_ms, updated_at = now()",
                [api, finished, quota, ms])
            if status == "warn" and msg:
                cur.execute("UPDATE quality.api_status SET last_error = %s, last_error_msg = %s WHERE api_name = %s",
                            [finished, msg, api])
        resolve_job_checks(cur, job, status, finished)
        if status in ("failed", "warn"):
            cur.execute(
                "INSERT INTO quality.checks (check_time, source, rule_id, severity, target, message, details) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [finished, api, "job_failed" if failed else "job_partial_fail", "error" if failed else "warn", job,
                 msg or detail, json.dumps({"detail": detail, "quota_hit": quota, "seconds": round(seconds, 1)},
                                           ensure_ascii=False)])
    conn.commit()


# ---------------------------------------------------------------- 읽기 (대시보드 API)

def _rows(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def error_resolved(r: dict) -> bool:
    """최근 오류 뒤에 성공이 있으면 True (오류는 기록으로만 보여 줌). 일부 실패(warn)는 같은 시각이라 False."""
    err, ok = r.get("last_error"), r.get("last_success")
    return bool(err and ok and ok > err and not (r.get("consecutive_failures") or 0))


def _api_item(r: dict, now: datetime) -> dict:
    return {"api": r["api_name"], "status": api_status(r, now), "last_success": r.get("last_success"),
            "last_error": r.get("last_error"), "last_error_msg": r.get("last_error_msg"),
            "error_resolved": error_resolved(r),
            "consecutive_failures": r.get("consecutive_failures") or 0, "quota_hit": bool(r.get("quota_hit")),
            "last_run_duration_ms": r.get("last_run_duration_ms"), "updated_at": r.get("updated_at")}


def overview(conn) -> dict:
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM quality.api_status")
        rows = {r["api_name"]: r for r in _rows(cur)}
        cur.execute("SELECT severity, count(*) FROM quality.checks "
                    "WHERE check_time > now() - interval '24 hours' AND NOT resolved GROUP BY severity")
        counts = {s: n for s, n in cur.fetchall()}
    apis = [_api_item(rows.get(name) or {"api_name": name}, now) for name in API_NAMES]
    apis += [_api_item(r, now) for name, r in sorted(rows.items()) if name not in API_NAMES]
    return {"generated_at": now, "apis": apis,
            "anomalies_24h": {"info": counts.get("info", 0), "warn": counts.get("warn", 0),
                              "error": counts.get("error", 0)}}


def api_detail(conn, name: str, limit: int = 20) -> dict | None:
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM quality.api_status WHERE api_name = %s", [name])
        rows = _rows(cur)
        if not rows and name not in API_NAMES:
            return None
        cur.execute("SELECT check_time AS time, severity, rule_id AS rule, target, message, resolved, "
                    "details->>'resolved_at' AS resolved_at, details->>'resolved_by' AS resolved_by "
                    "FROM quality.checks WHERE source = %s ORDER BY check_time DESC LIMIT %s", [name, limit])
        checks = _rows(cur)
    item = _api_item(rows[0] if rows else {"api_name": name}, now)
    item["jobs"] = sorted(j for j, a in API_OF_JOB.items() if a == name)
    item["recent_checks"] = checks
    return item


def anomalies(conn, limit: int = 50, severity: str | None = None, include_resolved: bool = False) -> dict:
    """이상 이벤트. 기본은 해결 안 된 것만 (정상으로 돌아온 작업의 경고·오류는 빠짐)."""
    sql = ("SELECT id, check_time AS time, source, rule_id AS rule, severity, target, message, resolved, "
           "details->>'resolved_at' AS resolved_at, details->>'resolved_by' AS resolved_by FROM quality.checks")
    where, params = [], []
    if not include_resolved:
        where.append("NOT resolved")
    if severity:
        where.append("severity = %s")
        params.append(severity)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY check_time DESC LIMIT %s"
    params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return {"anomalies": _rows(cur)}


def schema_tables(conn, schema: str) -> dict | None:
    """스키마의 테이블별 대략 건수 (pg_class.reltuples 추정치: 큰 테이블도 바로 나옴, ANALYZE 전이면 -1 → None)."""
    if schema not in SCHEMAS:
        return None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.relname AS table, c.relkind AS kind, "
            "CASE WHEN c.reltuples < 0 THEN NULL ELSE c.reltuples::bigint END AS count "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relkind IN ('r', 'p', 'v', 'm') ORDER BY c.relname", [schema])
        tables = _rows(cur)
        for t in tables:
            t["kind"] = {"r": "table", "p": "table", "v": "view", "m": "matview"}.get(t["kind"], t["kind"])
            if t["kind"] == "view":
                t["count"] = None
            elif t["count"] is None:  # 아직 통계가 없는 새 테이블은 대개 작으니 직접 셉니다 (2초 제한)
                try:
                    cur.execute("SAVEPOINT cnt; SET LOCAL statement_timeout = 2000")
                    cur.execute(f'SELECT count(*) FROM "{schema}"."{t["table"]}"')
                    t["count"] = cur.fetchone()[0]
                    cur.execute("RELEASE SAVEPOINT cnt")
                except Exception:  # noqa: BLE001
                    cur.execute("ROLLBACK TO SAVEPOINT cnt")
    return {"schema": schema, "tables": tables, "count_note": "추정치(pg_class.reltuples)"}


# ---------------------------------------------------------------- 데이터 현황 (status.collect 요약)

# (이름, 최신 값 키, 건수 키, 최신성 기준 키)
DATASETS = [
    ("국내·해외 시세", "price_to", "price_rows", "price"),
    ("재무 원본(연간 연도 수)", "fin_to", "annual_years", None),
    ("분기 단독 값", "last_quarter", "quarters", None),
    ("TTM", "ttm_end", None, None),
    ("공시", "disclosure_last", "disclosures", "disclosure"),
    ("네이버 뉴스", "news_last", "news_docs", "news"),
    ("블로그", "blog_last", "blog_docs", "blog"),
    ("카페", "cafe_last", "cafe_docs", "cafe"),
    ("감성 점수", None, "scored_docs", None),
    ("검색 관심도", "trend_last", "trend_days", "trend"),
    ("언급량 스냅샷", "mention_last", "mention_days", "mention"),
    ("5개 관점 지표", "feature_as_of", "features_filled", "feature"),
]


def data_status(conn, today=None) -> dict:
    """DB 의 현재 데이터 상태: 종목 마스터, 데이터 종류별 건수·최신 값·종목 커버리지·최신성, 지수·거시·문서·공시."""
    from zoneinfo import ZoneInfo

    from . import status

    today = today or datetime.now(ZoneInfo("Asia/Seoul")).date()
    st = status.collect(conn, today)
    cos = st["companies"]
    kr = [c for c in cos if c["market"] == "KR"]
    us = [c for c in cos if c["market"] == "US"]
    master = {"total": len(cos), "kr": len(kr), "us": len(us),
              "kr_dart_code": sum(c["corp_code"] for c in kr), "us_cik": sum(c["cik"] for c in us),
              "sector": sum(c["sector"] for c in cos), "shares": sum(c["shares"] for c in cos)}

    datasets = []
    for name, last_key, count_key, fresh_key in DATASETS:
        covered = sum(1 for c in cos if c.get(count_key or last_key))
        lasts = [c[last_key] for c in cos if last_key and c.get(last_key) is not None]  # 키마다 date 또는 datetime 한 종류
        last = max(lasts) if lasts else None
        stale = (sum(1 for v in lasts if status.freshness(v, today, status.FRESH_DAYS[fresh_key]) != "OK")
                 if fresh_key else None)  # 종목별 최신 값이 기준보다 오래된 종목 수
        datasets.append({
            "name": name, "covered": covered, "total": len(cos),
            "rows": sum(int(c.get(count_key) or 0) for c in cos) if count_key else None,
            "latest": last, "stale": stale,
            "freshness": status.freshness(last, today, status.FRESH_DAYS[fresh_key]) if fresh_key
            else ("없음" if covered == 0 else "-"),
        })
    indices = [{**i, "freshness": status.freshness(i["to"], today, status.FRESH_DAYS["index"])} for i in st["indices"]]
    macro = [{**m, "freshness": status.freshness(
        m["to"], today, status.FRESH_DAYS["macro_D" if m["frequency"] == "D" else "macro_M"])} for m in st["macro"]]
    return {"checked_at": today, "master": master, "datasets": datasets, "indices": indices,
            "macro": macro, "macro_stale": sum(m["freshness"] != "OK" for m in macro),
            "documents": st["documents"], "disclosures": st["disclosures"],
            "peer_groups": len(st["peer_groups"])}
