"""QA (#13): 국내 원천 재시도의 적용 범위·4xx 비재시도·최악 대기시간 상한."""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import requests
from requests.adapters import HTTPAdapter
from urllib3.exceptions import ConnectTimeoutError

from kic_data import http_util

OVERSEAS_URLS = [
    "https://api.stlouisfed.org/fred/series/observations",
    "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
    "https://finnhub.io/api/v1/company-news",
    "https://openapi.naver.com/v1/search/news.json",
    "https://www.googleapis.com/youtube/v3/search",
    "https://query1.finance.yahoo.com/v8/finance/chart/AAPL",
    "https://dart.fss.or.kr/dsaf001/main.do",  # 공시 뷰어(OpenDART API 아님)
    "https://apis.data.go.kr.example.com/x",  # 접두가 같아 보여도 다른 호스트
]


@pytest.mark.parametrize("url", OVERSEAS_URLS)
def test_overseas_hosts_keep_main_retry(url):
    retry = http_util.make_session().get_adapter(url).max_retries
    assert (retry.total, retry.connect, retry.backoff_factor, retry.backoff_jitter) == (4, 1, 1.0, 0)
    assert set(retry.status_forcelist) == {429, 500, 502, 503, 504}


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_domestic_retry_skips_4xx(status):
    assert not http_util.domestic_retry().is_retry("GET", status)


def test_domestic_retry_get_only():
    assert not http_util.domestic_retry().is_retry("POST", 503)


def _worst_waits(errors: int, monkeypatch) -> float:
    monkeypatch.setattr("urllib3.util.retry.random.random", lambda: 1.0)  # 지터 최대
    retry, total = http_util.domestic_retry(), 0.0
    for _ in range(errors):
        retry = retry.increment(method="GET", url="/", error=ConnectTimeoutError("x"))
        total += retry.get_backoff_time()
    return total


def test_connect_retry_worst_wait(monkeypatch):
    # 접속 재시도 3회 소진까지 대기 합 ≤ 20초 (0+4+8 + 지터 최대 2*3)
    assert _worst_waits(3, monkeypatch) <= 20


class _Handler(BaseHTTPRequestHandler):
    status = 200
    hits = 0

    def do_GET(self):  # noqa: N802
        type(self).hits += 1
        self.send_response(type(self).status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture()
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_port}/"
    httpd.shutdown()


def _session(base):
    s = requests.Session()
    s.mount(base, HTTPAdapter(max_retries=http_util.domestic_retry()))
    return s


@pytest.mark.parametrize("status", [401, 403])
def test_auth_error_called_once_and_not_cached(server, status, monkeypatch):
    _Handler.status, _Handler.hits = status, 0
    http_util._down.clear()
    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        http_util.get(_session(server), "ecos", server)
    assert _Handler.hits == 1
    assert "ecos" not in http_util._down  # 인증 오류는 실패 캐싱 대상 아님


def test_5xx_worst_case_attempts_and_wait(server, monkeypatch):
    sleeps = []
    monkeypatch.setattr("urllib3.util.retry.time.sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr("urllib3.util.retry.random.random", lambda: 1.0)
    _Handler.status, _Handler.hits = 503, 0
    with pytest.raises(RuntimeError, match="요청 실패"):
        http_util.get(_session(server), "lending", server)
    assert _Handler.hits == 6  # 처음 1번 + 재시도 5번
    assert sum(sleeps) <= 70  # 0+4+8+16+30 + 지터 최대 10
