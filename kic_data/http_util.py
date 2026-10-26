"""HTTP 공통 유틸: 재시도, 호출 간격 제한, 인증키가 오류 메시지에 남지 않도록 처리."""
from __future__ import annotations

import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SECRET_PARAM_NAMES = {"serviceKey", "crtfc_key", "api_key", "key", "client_secret", "token"}


def make_session(user_agent: str | None = None) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    if user_agent:
        session.headers["User-Agent"] = user_agent
    return session


class RateLimiter:
    """호출 사이에 최소 간격을 둡니다."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last = time.monotonic()


def redact(params: dict | None) -> dict:
    """저장·출력용으로 인증키 파라미터를 제거."""
    return {k: v for k, v in (params or {}).items() if k not in SECRET_PARAM_NAMES}


def get(session, source: str, url: str, params=None, headers=None, timeout: int = 30, limiter=None):
    """GET 호출. 실패해도 URL(인증키 포함 가능)은 메시지에 넣지 않습니다."""
    if limiter is not None:
        limiter.wait()
    try:
        response = session.get(url, params=params, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise RuntimeError(f"[{source}] 요청 실패: {type(exc).__name__}") from None
    if response.status_code != 200:
        raise RuntimeError(f"[{source}] HTTP {response.status_code} (키 승인·한도·사용자 에이전트를 확인하세요)")
    return response


def get_json(session, source: str, url: str, params=None, headers=None, timeout: int = 30, limiter=None):
    response = get(session, source, url, params=params, headers=headers, timeout=timeout, limiter=limiter)
    try:
        return response.json()
    except ValueError:
        raise RuntimeError(f"[{source}] JSON 이 아닌 응답입니다 (인증키 승인 여부를 확인하세요)") from None
