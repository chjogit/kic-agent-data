"""개인 투자자 반응: 네이버 검색 API(블로그·카페글)로 종목 관련 글의 제목·요약·링크를 수집합니다.

- 공식 검색 API 라서 네이버 개발자센터 약관 범위 안에서 쓸 수 있습니다 (뉴스와 같은 키 사용).
  종목토론실처럼 공개 API 가 없는 내부 엔드포인트는 쓰지 않습니다.
- 검색어는 '○○ 주가' 처럼 투자 의도가 있는 표현을 씁니다 (제품·서비스 글 제외).
- 블로그는 작성일(postdate)을 주지만 카페글은 날짜가 없어 '처음 수집한 날'을 날짜로 씁니다
  (매일 수집하면 하루 이내 오차). 그래서 카페글은 기존 행의 날짜를 덮어쓰지 않습니다.
"""
from __future__ import annotations

from datetime import date, datetime

from ..config import env
from ..http_util import RateLimiter, get_json
from ..util import parse_ymd
from .news import clean_text

URLS = {
    "naver_blog": "https://openapi.naver.com/v1/search/blog.json",
    "naver_cafe": "https://openapi.naver.com/v1/search/cafearticle.json",
}
_limiter = RateLimiter(0.12)

# 리딩방·광고 글 (반응 지표를 왜곡하므로 제외)
SPAM = (
    # 리딩방·유료 추천
    "리딩방", "리딩", "무료추천", "무료 추천", "종목추천방", "오픈채팅", "오픈톡", "카톡", "텔레그램", "선착순",
    "수익인증", "급등주 추천", "VIP", "목표가 공개", "히든종목", "원금회복", "무료체험", "문의주세요", "상담",
    # 종목명만 걸어 둔 금융상품·부동산 광고
    "소액결제", "대출", "보험", "분양", "지원금", "수수료",
)
QUERY_SUFFIXES = ("주가", "주식")


def is_spam(text: str) -> bool:
    return any(s.lower() in text.lower() for s in SPAM)


def parse_items(payload: dict, source: str, today: date | None = None) -> list[dict]:
    if "errorMessage" in payload:
        raise RuntimeError(f"[{source}] 오류 {payload.get('errorCode')}: {payload.get('errorMessage')}")
    rows = []
    for it in payload.get("items", []):
        url = (it.get("link") or "").strip()
        if not url:
            continue
        if source == "naver_blog":
            try:
                posted = parse_ymd(it.get("postdate", ""))
            except (TypeError, ValueError):
                posted = None
        else:
            posted = today or date.today()
        rows.append({"title": clean_text(it.get("title")), "url": url, "published_at": posted,
                     "summary": clean_text(it.get("description"))})
    return rows


def fetch_page(session, source: str, query: str, start: int = 1, display: int = 100) -> tuple[list[dict], int | None]:
    """(글 목록, 전체 검색 결과 수 total)."""
    headers = {
        "X-Naver-Client-Id": env("NAVER_CLIENT_ID", required=True),
        "X-Naver-Client-Secret": env("NAVER_CLIENT_SECRET", required=True),
    }
    params = {"query": query, "display": display, "start": start, "sort": "date"}
    payload = get_json(session, source, URLS[source], params=params, headers=headers, limiter=_limiter)
    return parse_items(payload, source), payload.get("total")


def fetch(session, source: str, query: str, start: int = 1, display: int = 100) -> list[dict]:
    return fetch_page(session, source, query, start, display)[0]


def collect(session, source: str, term: str, must: tuple[str, ...], since: date, pages: int = 2) -> list[dict]:
    """'{term} 주가', '{term} 주식' 최신순. 제목·요약에 종목명이 있고 광고가 아닌 글만, since 이후."""
    by_url: dict[str, dict] = {}
    for suf in QUERY_SUFFIXES:
        for page in range(pages):
            items = fetch(session, source, f"{term} {suf}", start=1 + 100 * page)
            if not items:
                break
            old = 0
            for r in items:
                text = f"{r['title']} {r['summary']}"
                if r["published_at"] and r["published_at"] < since:
                    old += 1
                    continue
                if not any(m.lower() in text.lower() for m in must) or is_spam(text):
                    continue
                by_url.setdefault(r["url"], r)
            if old == len(items):  # 최신순이라 전부 기간 밖이면 다음 페이지도 볼 필요 없음
                break
    return sorted(by_url.values(), key=lambda r: r["published_at"] or date.min, reverse=True)
