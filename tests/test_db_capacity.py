"""DB 용량 경고(C5) 오프라인 테스트."""
from kic_data import status

MB = 1024 * 1024
BASE = {"checked_at": "2026-10-30", "companies": [], "indices": [], "macro": [], "documents": [],
        "disclosures": [], "trend_topics": [], "peer_groups": []}


def test_usage_below_and_above_default_threshold(monkeypatch):
    monkeypatch.delenv("DB_WARN_MB", raising=False)
    assert status.db_usage(738 * MB)["over"] is False
    u = status.db_usage(901 * MB)
    assert u["over"] is True and u["warn_mb"] == 900 and u["limit_mb"] == 1024


def test_threshold_is_configurable_and_bad_value_falls_back(monkeypatch):
    monkeypatch.setenv("DB_WARN_MB", "700")
    assert status.db_usage(738 * MB)["over"] is True
    monkeypatch.setenv("DB_WARN_MB", "abc")
    assert status.warn_threshold_mb() == 900


def test_unknown_size_gives_none():
    assert status.db_usage(None) is None


def test_render_shows_warning_only_over_threshold(monkeypatch):
    monkeypatch.delenv("DB_WARN_MB", raising=False)
    ok = status.render({**BASE, "db": status.db_usage(738 * MB)})
    assert "[DB 용량] 738.0MB / 한도 1024MB" in ok and "[경고]" not in ok
    bad = status.render({**BASE, "db": status.db_usage(950 * MB)})
    assert "[경고]" in bad and "900MB" in bad and "수집 쓰기가 실패" in bad


def test_render_without_db_key_still_works():
    assert "[DB 용량]" not in status.render(BASE)


class _Cur:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        if "pg_database_size" not in sql:
            raise RuntimeError("no table")  # 다른 테이블은 없는 것처럼 (status._q 가 None 처리)

    def fetchall(self):
        return [(920 * MB,)]


class _Conn:
    def cursor(self):
        return _Cur()

    def rollback(self):
        pass


def test_collect_records_db_usage(monkeypatch):
    monkeypatch.delenv("DB_WARN_MB", raising=False)
    st = status.collect(_Conn())
    assert st["db"]["over"] is True and st["db"]["mb"] == 920.0
