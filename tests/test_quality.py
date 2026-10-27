"""데이터 품질 (kic_data/quality.py): DB 없이 판단 규칙만 확인합니다."""
from datetime import datetime, timedelta, timezone

from kic_data import jobs, quality

NOW = datetime(2026, 10, 27, 5, 0, tzinfo=timezone.utc)


def test_every_mapped_job_exists():
    assert set(quality.API_OF_JOB) <= set(jobs.JOB_BY_NAME)


def test_internal_jobs_are_not_apis():
    for name in ("apply-schema", "seed-companies", "sentiment", "build-features", "build-quarterly"):
        assert name not in quality.API_OF_JOB


def test_quota_detection():
    assert quality.quota_hit("[FAIL] AAPL: 429 Too Many Requests")
    assert quality.quota_hit("[FAIL] 일 한도 초과")
    assert not quality.quota_hit("[FAIL] 005930: timeout")


def test_first_error_prefers_fail_line():
    assert quality.first_error("[OK] a\n[FAIL] b: boom\n[FAIL] c", "d") == "b: boom"
    assert quality.first_error("", "OK 3") == "OK 3"


def test_api_status_lights():
    ok = {"last_success": NOW - timedelta(hours=1), "consecutive_failures": 0}
    assert quality.api_status(ok, NOW) == "ok"
    assert quality.api_status({**ok, "quota_hit": True}, NOW) == "warn"
    assert quality.api_status({**ok, "consecutive_failures": 1}, NOW) == "warn"
    assert quality.api_status({**ok, "consecutive_failures": 2}, NOW) == "error"
    assert quality.api_status({**ok, "last_success": NOW - timedelta(days=3)}, NOW) == "warn"
    assert quality.api_status({}, NOW) == "unknown"
    assert quality.api_status({"last_error": NOW}, NOW) == "error"


def test_schema_whitelist():
    assert quality.schema_tables(None, "pg_catalog") is None


def test_datasets_use_known_freshness_keys():
    from kic_data import status
    for name, _last, _count, fresh in quality.DATASETS:
        assert fresh is None or fresh in status.FRESH_DAYS, name
