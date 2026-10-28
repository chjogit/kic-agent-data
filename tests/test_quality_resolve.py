"""정상으로 돌아온 작업의 경고·오류 자동 해결 (quality.record_job / resolve_stale / anomalies). DB 없이 SQL 순서만 확인."""
from datetime import datetime, timedelta, timezone

from kic_data import jobs, quality

T = datetime(2026, 10, 28, 9, 15, tzinfo=timezone.utc)


class FakeCursor:
    def __init__(self, log, rows=()):
        self.log, self.rows, self.description = log, list(rows), [("x",)]

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self):
        self.log, self.commits = [], 0

    def cursor(self):
        return FakeCursor(self.log)

    def commit(self):
        self.commits += 1


def _record(status):
    conn = FakeConn()
    quality.record_job(conn, "kr-prices", status=status, seconds=3.0, detail="OK 1",
                       log="[FAIL] 국내 시세: ConnectTimeout" if status in ("failed", "warn") else "[OK] 1", finished=T)
    return conn.log


def test_ok_resolves_previous_events_as_recovered_and_adds_none():
    log = _record("ok")
    upd = [p for s, p in log if s.startswith("UPDATE quality.checks")]
    assert len(upd) == 1 and upd[0][1] == "recovered" and upd[0][2] == "kr-prices"
    assert not any(s.startswith("INSERT INTO quality.checks") for s, _ in log)


def test_partial_counts_as_recovered():
    assert quality.resolve_reason("partial") == "recovered"


def test_failure_supersedes_previous_then_inserts_new():
    log = _record("failed")
    i_upd = next(i for i, (s, _) in enumerate(log) if s.startswith("UPDATE quality.checks"))
    i_ins = next(i for i, (s, _) in enumerate(log) if s.startswith("INSERT INTO quality.checks"))
    assert i_upd < i_ins  # 이전 것을 먼저 해결 처리해야 새 이벤트까지 지우지 않음
    assert log[i_upd][1][1] == "superseded"
    assert log[i_upd][1][-1] == T  # check_time <= 이번 종료 시각만


def test_warn_also_keeps_only_latest():
    log = _record("warn")
    assert [p[1] for s, p in log if s.startswith("UPDATE quality.checks")] == ["superseded"]
    assert any(s.startswith("INSERT INTO quality.checks") for s, _ in log)


def test_skipped_and_internal_jobs_touch_nothing():
    conn = FakeConn()
    quality.record_job(conn, "kr-prices", status="skipped", seconds=0, detail=None, log="", finished=T)
    quality.record_job(conn, "build-features", status="failed", seconds=0, detail=None, log="", finished=T)
    assert conn.log == []


def test_resolve_stale_three_rules_and_known_jobs():
    conn = FakeConn()
    quality.resolve_stale(conn, jobs.JOB_BY_NAME, now=T)
    reasons = [p[1] for _, p in conn.log]
    assert reasons == ["recovered", "superseded", "job_removed"]
    known = conn.log[2][1][-1]
    assert "kr-prices" in known and "no-such-job" not in known  # 목록에 없는 작업(삭제·병합 전 브랜치)은 정리
    assert "ops.job_state" in conn.log[0][0] and "last_finished > c.check_time" in conn.log[0][0]
    assert conn.commits == 1


def test_anomalies_hide_resolved_by_default():
    conn = FakeConn()
    quality.anomalies(conn, 50)
    assert "WHERE NOT resolved" in conn.log[-1][0]
    quality.anomalies(conn, 50, "error", include_resolved=True)
    sql, params = conn.log[-1]
    assert "NOT resolved" not in sql and "severity = %s" in sql and params == ["error", 50]


def test_error_resolved_flag():
    base = {"last_error": T, "consecutive_failures": 0}
    assert quality.error_resolved({**base, "last_success": T + timedelta(minutes=30)})
    assert not quality.error_resolved({**base, "last_success": T})  # 일부 실패(warn)는 같은 시각
    assert not quality.error_resolved({**base, "last_success": T + timedelta(hours=1), "consecutive_failures": 1})
    assert not quality.error_resolved({"last_success": T})


def test_apply_schema_runs_every_tick():
    job = jobs.JOB_BY_NAME["apply-schema"]
    st = {"last_complete": T - timedelta(minutes=1), "last_status": "ok", "last_finished": T - timedelta(minutes=1)}
    assert jobs.due_reason(job, st, T, []) == "주기"  # 정시 실행 직후의 수동 실행에서도 새 sql 을 적용
