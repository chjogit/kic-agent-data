"""국내 공공·금융 API 호출의 원천별 타임아웃·지수 백오프(+지터) 재시도·실패 캐싱 기간 (#13)."""
from __future__ import annotations

import pytest
import requests
from urllib3.exceptions import ConnectTimeoutError

from kic_data import http_util

DOMESTIC_URLS = {
    "ecos": "https://ecos.bok.or.kr/api/StatisticSearch/x/json/kr/1/100",
    "dart": "https://opendart.fss.or.kr/api/list.json",
    "fsc": "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo",
    "lending": "https://apis.data.go.kr/1160100/service/GetCMStckLnbInfoService/getStckLnbDetail",
}


@pytest.mark.parametrize("name", sorted(DOMESTIC_URLS))
def test_domestic_hosts_use_connect_retry_with_jitter(name):
    session = http_util.make_session()
    retry = session.get_adapter(DOMESTIC_URLS[name]).max_retries
    assert retry.connect >= 2
    assert retry.backoff_factor > 0 and retry.backoff_jitter > 0
    assert retry.backoff_max <= 60  # 한 번 기다림이 1분을 넘지 않게


def test_other_hosts_keep_default_retry():
    session = http_util.make_session()
    retry = session.get_adapter("https://api.stlouisfed.org/fred/series/observations").max_retries
    assert retry.connect == 1 and retry.backoff_jitter == 0


def test_domestic_backoff_is_exponential(monkeypatch):
    monkeypatch.setattr("urllib3.util.retry.random.random", lambda: 0.0)
    retry = http_util.domestic_retry()
    waits = []
    for _ in range(3):
        retry = retry.increment(method="GET", url="/", error=ConnectTimeoutError("x"))
        waits.append(retry.get_backoff_time())
    assert waits[1] > 0 and waits[2] == waits[1] * 2
    monkeypatch.setattr("urllib3.util.retry.random.random", lambda: 1.0)
    assert retry.get_backoff_time() == waits[2] + retry.backoff_jitter  # 지터가 더해짐


def test_connect_retries_exhaust_then_fail():
    retry = http_util.domestic_retry()
    with pytest.raises(Exception):
        for _ in range(retry.connect + 1):
            retry = retry.increment(method="GET", url="/", error=ConnectTimeoutError("x"))


@pytest.mark.parametrize("source", ["ecos", "dart", "fsc", "fsc-etf", "fsc_bond", "lending", "customs"])
def test_source_timeouts(source):
    connect, read = http_util.timeouts(source)
    assert 0 < connect <= 30 and 0 < read <= 120
    assert http_util.timeouts(source, 90) == (connect, 90)  # 호출에서 준 읽기 타임아웃이 우선


def test_unlisted_source_keeps_old_defaults():
    assert http_util.timeouts("fred") == (http_util.CONNECT_TIMEOUT, 30)


def test_get_passes_source_timeouts():
    seen = []

    class S:
        def get(self, url, params=None, headers=None, timeout=None):
            seen.append(timeout)
            r = requests.Response()
            r.status_code = 200
            return r

    http_util.get(S(), "ecos", "https://ecos.bok.or.kr/api")
    http_util.get(S(), "lending", "https://apis.data.go.kr/x", timeout=45)
    assert seen == [http_util.timeouts("ecos"), (http_util.timeouts("lending")[0], 45)]


def test_failure_cache_expires_within_a_day(monkeypatch):
    assert 0 < http_util.DOWN_FOR <= 24 * 3600
    now = [1000.0]
    monkeypatch.setattr(http_util.time, "monotonic", lambda: now[0])
    calls = []

    class S:
        def get(self, *a, **k):
            calls.append(1)
            raise requests.ConnectTimeout("x")

    http_util._down.clear()
    try:
        with pytest.raises(RuntimeError, match="요청 실패"):
            http_util.get(S(), "ecos", "https://ecos.bok.or.kr/api")
        with pytest.raises(RuntimeError, match="건너뜀"):
            http_util.get(S(), "ecos", "https://ecos.bok.or.kr/api")
        now[0] += http_util.DOWN_FOR + 1
        with pytest.raises(RuntimeError, match="요청 실패"):  # 기간이 지나면 다시 시도
            http_util.get(S(), "ecos", "https://ecos.bok.or.kr/api")
        assert len(calls) == 2
    finally:
        http_util._down.clear()
