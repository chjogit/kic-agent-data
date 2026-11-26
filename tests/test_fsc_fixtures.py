"""국내 시세 경로 fixture 하네스 (DB·네트워크 불필요).

경로: FSC JSON 응답 → fsc.parse_prices → cli._kr_prices_bulk(종목별 묶기) → db.upsert_prices(guards.check_price_rows) → core.price_daily
fixture: tests/fixtures/fsc/*.json (실제 응답 형식을 줄인 것). 흐름표는 docs/PRICE_PIPELINE_FLOW.md.

실패 사례를 늘릴 때는 fixture JSON 을 추가하고 아래에 시험 하나를 붙이면 됩니다.
"""
import io
import json
import unittest
from contextlib import contextmanager, redirect_stdout
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from kic_data import cli, db
from kic_data.collectors import fsc
from kic_data.guards import MAX_PRICE_ROWS, GuardError, check_price_rows

FIXTURES = Path(__file__).parent / "fixtures" / "fsc"

# parse_prices 가 돌려주는 행의 키 (출력 스키마). 바뀌면 upsert_prices·cli 도 같이 봐야 합니다.
PARSED_KEYS = {"trade_date", "open", "high", "low", "close", "volume", "trade_value", "market_cap", "shares", "ticker"}


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def summary(rows: list[dict]) -> dict:
    """핵심 지표: 행 수, 종목 수, 종가 null 비율, 거래량 합계."""
    n = len(rows)
    return {
        "rows": n,
        "tickers": len({r["ticker"] for r in rows}),
        "close_null_ratio": (sum(r["close"] is None for r in rows) / n) if n else 0.0,
        "volume_sum": sum(r["volume"] or 0 for r in rows),
    }


class FakeCursor:
    def __init__(self, sink):
        self.sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def executemany(self, sql, params):
        self.sink.append((sql, list(params)))


class FakeConn:
    def __init__(self):
        self.calls = []

    def cursor(self):
        return FakeCursor(self.calls)

    def commit(self):
        pass


class ParseFixtureTest(unittest.TestCase):
    def test_ok(self):
        rows = fsc.parse_prices(load("ok.json"))
        self.assertEqual(summary(rows), {"rows": 4, "tickers": 2, "close_null_ratio": 0.0, "volume_sum": 3700})
        for r in rows:
            self.assertEqual(set(r), PARSED_KEYS)
            self.assertIsInstance(r["trade_date"], date)
            self.assertIsInstance(r["close"], Decimal)
        self.assertEqual(sorted({r["ticker"] for r in rows}), ["000660", "005930"])

    def test_missing_clpr_rows_dropped_with_warning(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rows = fsc.parse_prices(load("missing_clpr.json"))
        # 3행 중 종가 빈 값('')·숫자 아님('-') 2행은 버리고 1행만 남음 (예전엔 close=NULL 로 저장됨)
        self.assertEqual(summary(rows), {"rows": 1, "tickers": 1, "close_null_ratio": 0.0, "volume_sum": 1000})
        self.assertIn("필수 필드 빈 행 2개 제외", out.getvalue())

    def test_renamed_field_fails_fast(self):
        # 2026-09 V2 전환처럼 필드명이 바뀌면 모든 행에서 clpr 가 비게 됨 → 조용히 0행이 아니라 실패
        with self.assertRaises(RuntimeError) as ctx:
            fsc.parse_prices(load("renamed_field.json"))
        self.assertIn("응답 형식 변경 의심", str(ctx.exception))

    def test_dup_date_parsed_as_is(self):
        rows = fsc.parse_prices(load("dup_date.json"))
        self.assertEqual(len(rows), 3)  # 파서는 중복을 걸러내지 않음 → 가드·묶기 단계 몫


class GuardTest(unittest.TestCase):
    def test_ok_passes(self):
        rows = [r for r in fsc.parse_prices(load("ok.json")) if r["ticker"] == "005930"]
        check_price_rows(rows, who="005930")

    def test_duplicate_trade_date(self):
        rows = fsc.parse_prices(load("dup_date.json"))
        with self.assertRaises(GuardError) as ctx:
            check_price_rows(rows, who="005930")
        self.assertIn("2026-11-17 중복", str(ctx.exception))

    def test_null_close(self):
        rows = fsc.parse_prices(load("ok.json"))[:1]
        rows[0]["close"] = None
        with self.assertRaises(GuardError):
            check_price_rows(rows)

    def test_row_cap(self):
        rows = [{"trade_date": date(2000, 1, 1), "close": Decimal(1)}] * (MAX_PRICE_ROWS + 1)
        with self.assertRaises(GuardError) as ctx:
            check_price_rows(rows)
        self.assertIn("상한", str(ctx.exception))

    def test_upsert_writes_nothing_on_guard_failure(self):
        conn = FakeConn()
        with self.assertRaises(GuardError):
            db.upsert_prices(conn, 1, fsc.parse_prices(load("dup_date.json")), "fsc")
        self.assertEqual(conn.calls, [])

    def test_upsert_ok(self):
        conn = FakeConn()
        rows = [r for r in fsc.parse_prices(load("ok.json")) if r["ticker"] == "000660"]
        self.assertEqual(db.upsert_prices(conn, 7, rows, "fsc"), 2)
        sql, params = conn.calls[0]
        self.assertIn("core.price_daily", sql)
        self.assertEqual({p["company_id"] for p in params}, {7})


class BulkPathTest(unittest.TestCase):
    """cli._kr_prices_bulk 를 fixture 로 끝까지 돌립니다 (DB 는 가짜 연결)."""

    def run_bulk(self, fixture: str, companies):
        conn = FakeConn()

        @contextmanager
        def fake_connect():
            yield conn

        rows = fsc.parse_prices(load(fixture))
        out = io.StringIO()
        with mock.patch.object(db, "connect", fake_connect), \
                mock.patch.object(db, "list_companies", return_value=companies), \
                mock.patch.object(db, "update_company_info"), \
                mock.patch.object(fsc, "fetch_all_prices", return_value=rows), \
                mock.patch.object(cli, "drop_incomplete_today", side_effect=lambda r, m: r), \
                redirect_stdout(out):
            code = cli._kr_prices_bulk(None, date(2026, 11, 17), date(2026, 11, 18))
        return code, conn, out.getvalue()

    def test_ok_fixture_loads_both(self):
        code, conn, _ = self.run_bulk("ok.json", [(1, "005930"), (2, "000660")])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(len(p) for _, p in conn.calls), [2, 2])

    def test_dup_date_is_collapsed_before_load(self):
        # 알려진 위험(연습 4 기록): 묶기 단계 dict 가 같은 날짜를 '나중 값 우선'으로 조용히 합침 → 가드까지 오지 않음
        code, conn, _ = self.run_bulk("dup_date.json", [(1, "005930")])
        self.assertEqual(code, 0)
        (_, params), = conn.calls
        self.assertEqual(len(params), 2)
        self.assertEqual(next(p["close"] for p in params if p["trade_date"] == date(2026, 11, 17)), Decimal("70100"))

    def test_missing_company_counted(self):
        code, conn, out = self.run_bulk("ok.json", [(1, "005930"), (2, "035420")])
        self.assertIn("035420 기간 내 시세 없음", out)
        self.assertEqual(code, 1)  # 2종목 중 1종목 누락 → 10% 초과 → 실패 코드


if __name__ == "__main__":
    unittest.main()
