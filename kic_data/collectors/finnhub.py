"""Finnhub 종목 뉴스 (해외 종목 영문 기사: 제목·요약·링크·게시시각만, 본문 미수집).

네이버 검색은 한글 기사 위주라 해외 종목은 기사량이 적을 수 있어 보조 소스로 둡니다.
무료 키(finnhub.io 가입) 기준 분당 60회. FINNHUB_API_KEY 가 없으면 이 수집은 건너뜁니다.
영문이라 기존 한국어 감성 사전으로는 채점하지 않습니다 (source='finnhub_news', 감성 대상 아님).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from ..config import env
from ..http_util import RateLimiter, get_json

URL = "https://finnhub.io/api/v1/company-news"
_limiter = RateLimiter(1.1)


def parse_company_news(payload) -> list[dict]:
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(f"[finnhub] {payload['error']}")
    rows, seen = [], set()
    for it in payload or []:
        url = (it.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        ts = it.get("datetime")
        published = datetime.fromtimestamp(ts, tz=timezone.utc).date() if ts else None
        rows.append({"title": (it.get("headline") or "").strip(), "url": url, "published_at": published,
                     "summary": (it.get("summary") or "").strip()})
    return rows


def fetch_company_news(session, symbol: str, since: date, until: date) -> list[dict]:
    params = {"symbol": symbol, "from": since.isoformat(), "to": until.isoformat(),
              "token": env("FINNHUB_API_KEY", required=True)}
    return parse_company_news(get_json(session, "finnhub", URL, params=params, limiter=_limiter))
