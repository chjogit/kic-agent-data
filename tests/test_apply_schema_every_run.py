"""apply-schema 는 매 실행 돌아야 한다.

10-28: 정시 실행(08:15) 33분 뒤 브랜치 수동 실행에서 apply-schema 가 '1시간 주기'로 건너뛰어져
새 sql(022) 이 적용되지 않은 채 etf-prices 가 market 열 없음으로 실패했다.
"""
from datetime import datetime, timedelta, timezone

from kic_data import jobs


def _job(name):
    return next(j for j in jobs.JOBS if j.name == name)


def test_apply_schema_runs_again_minutes_later():
    now = datetime(2026, 10, 28, 8, 48, tzinfo=timezone.utc)
    st = {"last_status": "ok", "last_finished": now - timedelta(minutes=33), "last_complete": now - timedelta(minutes=33)}
    assert jobs.due_reason(_job("apply-schema"), st, now, []) is not None


def test_other_hourly_jobs_still_wait():
    now = datetime(2026, 10, 28, 8, 48, tzinfo=timezone.utc)
    st = {"last_status": "ok", "last_finished": now - timedelta(minutes=33), "last_complete": now - timedelta(minutes=33)}
    assert jobs.due_reason(_job("etf-prices"), st, now, []) is None
