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


def test_new_collections_in_status_render():
    from datetime import date, datetime
    rows = [{"table": "core.audit_opinion", "label": "감사의견", "rows": 2502, "companies": 200,
             "last_date": date(2026, 6, 30), "last_collected": datetime(2026, 11, 1, 3, 0), "freq": "weekly"},
            {"table": "core.financial_item", "label": "재무 항목(감가상각·이자비용)", "rows": 1183, "companies": 90,
             "last_date": None, "last_collected": datetime(2026, 10, 1, 3, 0), "freq": "weekly"}]
    out = status.render({**BASE, "checked_at": "2026-11-04", "new_collections": rows})
    assert "[새 수집]" in out and "2502행" in out
    audit, fin = [ln for ln in out.splitlines() if "행  종목" in ln and ("감사의견" in ln or "재무 항목" in ln)]
    assert "OK" in audit and "오래됨" in fin                      # 주 1회 작업: 11/1 수집은 OK, 10/1 은 오래됨
    assert "아직 수집 전" in status.render(BASE)


def test_market_indicator_freshness_uses_business_days():
    from datetime import date
    assert status.market_freshness(date(2026, 11, 2), date(2026, 11, 4)) == "OK"            # 월 → 수: 2영업일
    assert status.market_freshness(date(2026, 10, 30), date(2026, 11, 2)) == "OK"            # 금 → 월: 주말은 제외
    assert status.market_freshness(date(2026, 10, 30), date(2026, 11, 4)).startswith("오래됨")
    assert status.market_freshness(None, date(2026, 11, 4)) == "없음"


def test_market_indicator_in_status_render():
    from datetime import date, datetime
    m = [{"label": "신용공여 잔고(시장 전체)", "rows": 1216, "last_date": date(2026, 11, 2), "last_collected": datetime(2026, 11, 4, 3, 0)}]
    out = status.render({**BASE, "checked_at": "2026-11-04", "market_indicators": m})
    assert "[시장 지표]" in out and "1216행" in out and "OK" in out and "종목별 아님" in out
    assert "collect-credit-balance" in status.render(BASE)
