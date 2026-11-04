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


def test_new_collection_jobs_map_to_dart():
    for name in ("disclosures-ownership", "backfill-disclosures-ownership", "ownership", "audit-opinion", "financial-items"):
        assert quality.API_OF_JOB[name] == "dart"


def test_credit_balance_maps_to_data_go_kr_and_skip_is_not_red():
    assert quality.API_OF_JOB["credit-balance"] == "data.go.kr"
    assert not quality.quota_hit("[WARN] 신용공여 잔고 키가 아직 이 서비스에 승인·적용되지 않음 → 이번 실행은 건너뜀 (SERVICE KEY IS NOT REGISTERED)")


class _FakeCur:
    def __init__(self, fail): self.fail, self.sql = fail, ""
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None):
        self.sql = sql
        if self.fail(sql):
            raise RuntimeError("permission denied for table x\nDETAIL secret")
    def fetchall(self): return [(5, 3, None, datetime(2026, 11, 3, 3, 0))] if "max(collected_at)" in self.sql else []


class _FakeConn:
    def __init__(self, fail): self.fail, self.rolled = fail, 0
    def cursor(self): return _FakeCur(self.fail)
    def rollback(self): self.rolled += 1


def test_data_status_passes_new_sections_and_shows_query_errors(monkeypatch):
    from datetime import date

    from kic_data import status
    monkeypatch.setattr(status, "NEW_TABLES", (("core.a", "표A", None, "weekly"), ("core.b", "표B", None, "weekly")))
    conn = _FakeConn(lambda sql: "core.b" in sql or "credit_balance" in sql)      # 표B·신용공여만 조회 실패
    st = status.collect(conn, date(2026, 11, 4))
    a, b = st["new_collections"]
    assert a["rows"] == 5 and a["error"] is None and b["rows"] is None and b["error"].startswith("RuntimeError: permission denied")
    assert "DETAIL" not in b["error"] and conn.rolled >= 2                         # 첫 줄만, 롤백함
    assert st["market_indicators"][0]["error"]
    out = quality.data_status(conn, date(2026, 11, 4))
    assert out["new_collections"][0]["freshness"] == "OK" and out["new_collections"][1]["error"]
    assert out["market_indicators"][0]["freshness"] is None
    assert "조회 실패" in status.render(st)
