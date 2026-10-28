"""10-28 ETF 확장: 대표 지수·고배당 ETF, 해외 ETF, 처음 보는 ETF 의 과거분 자동 수집."""
from datetime import date

from kic_data import cli, jobs, quality
from kic_data.universe import (
    CATEGORY_LABEL,
    ETF_CATEGORY,
    ETF_UNIVERSE,
    US_ETF_UNIVERSE,
    etf_name_mismatch,
)


def test_requested_categories_are_covered():
    cats = {ETF_CATEGORY[t][0] for t, _ in ETF_UNIVERSE + US_ETF_UNIVERSE}
    for need in ("sp500", "kospi200", "nasdaq100", "kr_high_dividend"):
        assert need in cats
        assert need in CATEGORY_LABEL
    # 국내 상장으로 4개 분류가 모두 있어야 NAV·괴리율까지 같은 기준으로 비교 가능
    kr_cats = {ETF_CATEGORY[t][0] for t, _ in ETF_UNIVERSE}
    assert {"sp500", "kospi200", "nasdaq100", "kr_high_dividend"} <= kr_cats


def test_every_etf_has_category_and_unique_ticker():
    tickers = [t for t, _ in ETF_UNIVERSE + US_ETF_UNIVERSE]
    assert len(tickers) == len(set(tickers))
    assert all(t in ETF_CATEGORY for t in tickers)
    assert all(len(t) == 6 and t.isdigit() for t, _ in ETF_UNIVERSE)


def test_name_mismatch_detects_wrong_code():
    assert not etf_name_mismatch("360750", "TIGER 미국S&P500")
    assert not etf_name_mismatch("161510", "PLUS 고배당주")
    assert etf_name_mismatch("161510", "KODEX 200")
    assert not etf_name_mismatch("SPY", "anything")  # 해외는 이름 검사 안 함
    assert not etf_name_mismatch("161510", None)


def test_new_etf_gets_five_years():
    today = date(2026, 10, 28)
    begin = date(2026, 10, 18)
    assert cli.etf_begin(date(2021, 10, 28), begin, today) == begin
    assert cli.etf_begin(None, begin, today) == date(2021, 10, 29)
    # 요청 기간이 더 길면 그대로
    assert cli.etf_begin(None, date(2020, 1, 1), today) == date(2020, 1, 1)


def test_us_etf_job_runs_in_global_lane():
    job = jobs.JOB_BY_NAME["us-etf-prices"]
    assert job.command == "cmd_us_etf" and hasattr(cli, "cmd_us_etf")
    assert quality.API_OF_JOB["us-etf-prices"] == "yfinance"
    assert jobs.lane_of(job) == "global"
    assert jobs.lane_of(jobs.JOB_BY_NAME["etf-prices"]) == "public"


def test_upsert_etf_prices_passes_market(monkeypatch):
    from kic_data import db

    seen = {}

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def executemany(self, sql, params):
            seen["sql"], seen["params"] = sql, params

    class Conn:
        def cursor(self):
            return Cur()

        def commit(self):
            pass

    rows = [{"trade_date": date(2026, 10, 27), "close": 1, "open": 1, "high": 1, "low": 1, "volume": 5}]
    assert db.upsert_etf_prices(Conn(), "SPY", "SPDR", rows, "yfinance", market="US", currency="USD") == 1
    p = seen["params"][0]
    assert p["market"] == "US" and p["currency"] == "USD" and p["nav"] is None and "open" not in p
    assert "market" in seen["sql"]
