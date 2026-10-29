"""네이버 검색 API(뉴스): 제목·요약(스니펫)·링크·게시일만 수집합니다. 기사 본문은 수집하지 않습니다.

- 하루 호출 한도·저장/재사용 조건은 네이버 개발자센터 약관을 따르세요 (기획서 부록에 명시).
- 검색은 키워드 기반이라 관련 없는 기사가 섞입니다 → 제목에 종목명이 있는 기사만 남기고 시황 기사는 제외합니다.
"""
from __future__ import annotations

import html
import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime

from ..config import env
from ..http_util import RateLimiter, get_json

URL = "https://openapi.naver.com/v1/search/news.json"
_limiter = RateLimiter(0.12)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")

# 검색어와 '제목/요약에 반드시 들어가야 하는 단어(하나 이상)'. 없는 종목은 종목명으로 검색.
# 해외 종목은 국내 언론 표기(한글)로 검색합니다. 팀에서 자유롭게 보완하세요.
NEWS_TERMS: dict[str, tuple[str, tuple[str, ...]]] = {
    "KR:035420": ("네이버", ("네이버", "NAVER")),
    "US:NVDA": ("엔비디아", ("엔비디아", "NVIDIA", "Nvidia")),
    "US:MU": ("마이크론", ("마이크론", "Micron")),
    "US:AVGO": ("브로드컴", ("브로드컴", "Broadcom")),
    "US:AMD": ("AMD", ("AMD",)),
    "US:INTC": ("인텔", ("인텔", "Intel")),
    "US:AMAT": ("어플라이드머티어리얼즈", ("어플라이드", "Applied Materials", "AMAT")),
    "US:ASML": ("ASML", ("ASML",)),
    "US:TSM": ("TSMC", ("TSMC", "대만 반도체")),
    "US:AAPL": ("애플", ("애플", "Apple", "아이폰")),
    "US:MSFT": ("마이크로소프트", ("마이크로소프트", "Microsoft")),
    "KR:095340": ("ISC", ("ISC", "아이에스시")),
    "US:QCOM": ("퀄컴", ("퀄컴", "Qualcomm")),
    "US:TXN": ("텍사스인스트루먼트", ("텍사스인스트루먼트", "Texas Instruments")),
    "US:LRCX": ("램리서치", ("램리서치", "Lam Research")),
    "US:KLAC": ("KLA", ("KLA",)),
    "US:ADI": ("아날로그디바이스", ("아날로그디바이스", "아나로그디바이스", "Analog Devices", "ADI")),
    "KR:361610": ("SK아이이테크놀로지", ("SK아이이테크놀로지", "SKIET")),
    "US:MRVL": ("마벨", ("마벨", "Marvell")),
    "US:ON": ("온세미", ("온세미", "onsemi", "ON Semiconductor")),
    "US:NXPI": ("NXP", ("NXP",)),
    "US:MCHP": ("마이크로칩", ("마이크로칩", "Microchip")),
    "US:TER": ("테라다인", ("테라다인", "Teradyne")),
}


def terms_for(market: str, ticker: str, name: str | None) -> tuple[str, tuple[str, ...]]:
    key = f"{market}:{ticker}"
    if key in NEWS_TERMS:
        return NEWS_TERMS[key]
    base = (name or ticker).strip()
    return base, (base,)


def clean_text(text: str | None) -> str:
    """<b>태그 제거 + HTML 엔티티(&quot; 등) 복원 + 공백 정리."""
    return _SPACE.sub(" ", html.unescape(_TAG.sub("", text or ""))).strip()


def parse_news(payload: dict) -> list[dict]:
    """응답 JSON → rag.document 용 행 목록 (url 이 없는 항목은 버림)."""
    if "errorMessage" in payload:
        raise RuntimeError(f"[naver] 오류 {payload.get('errorCode')}: {payload.get('errorMessage')}")
    rows = []
    for it in payload.get("items", []):
        url = (it.get("originallink") or it.get("link") or "").strip()
        if not url:
            continue
        try:
            published = parsedate_to_datetime(it["pubDate"]).date()
        except (KeyError, TypeError, ValueError):
            published = None
        rows.append(
            {
                "title": clean_text(it.get("title")),
                "url": url,
                "published_at": published,
                "summary": clean_text(it.get("description")),
            }
        )
    return rows


# 시황 기사(여러 종목을 한꺼번에 언급)는 종목별 근거로 부적합해 제외합니다. 필요하면 패턴을 조정하세요.
MARKET_WRAP = ("뉴욕증시", "코스피", "코스닥", "증시 마감", "마감 시황", "사이드카", "서킷브레이커",
               "필라델피아 반도체지수", "나스닥", "다우", "S&P")


def is_market_wrap(title: str) -> bool:
    return any(p in title for p in MARKET_WRAP)


def matches(row: dict, must: tuple[str, ...]) -> bool:
    """제목에 종목명(검색 포함어)이 있어야 통과. 요약에서만 언급된 기사는 제외."""
    title = row["title"].lower()
    return any(m.lower() in title for m in must)


def fetch_news_page(session, query: str, display: int = 100, start: int = 1, sort: str = "date") -> tuple[list[dict], int | None]:
    """(기사 목록, 전체 검색 결과 수 total). total 은 누적값이라 시점 간 차이 = 그 사이 새로 올라온 기사 수."""
    headers = {
        "X-Naver-Client-Id": env("NAVER_CLIENT_ID", required=True),
        "X-Naver-Client-Secret": env("NAVER_CLIENT_SECRET", required=True),
    }
    params = {"query": query, "display": display, "start": start, "sort": sort}
    payload = get_json(session, "naver", URL, params=params, headers=headers, limiter=_limiter)
    return parse_news(payload), payload.get("total")


def fetch_news(session, query: str, display: int = 100, start: int = 1, sort: str = "date") -> list[dict]:
    return fetch_news_page(session, query, display, start, sort)[0]


# 검색어에 붙이는 주제어(AND 검색). 주제마다 최신순·관련도순으로 모아 날짜와 내용이 고르게 퍼지게 합니다.
TOPICS = ("실적", "목표주가", "전망", "투자")
FALLBACK_MIN = 10  # 주제어 검색으로 모은 기사가 이보다 적으면 종목명만으로 한 번 더 검색


def _title_key(title: str) -> str:
    """같은 기사를 여러 언론사가 재전송한 경우를 걸러내기 위한 제목 키."""
    return re.sub(r"[\W_]+", "", title.lower())[:25]


def collect_company_news(
    session,
    query: str,
    must: tuple[str, ...],
    since: date,
    max_items: int = 300,
    topics: tuple[str, ...] = TOPICS,
    pages: int = 1,
) -> list[dict]:
    """주제어별(topics) × 정렬(date, sim) 로 최대 pages 페이지(100건/페이지)씩 모읍니다.

    since 이후 기사만, URL 과 제목 키 기준으로 중복 제거. 게시일이 고르게 분포하도록
    최신 기사부터 채우되 max_items 를 넘으면 최신순으로 자릅니다.
    """
    by_url: dict[str, dict] = {}
    seen_titles: set[str] = set()
    for topic in topics or ("",):
        q = f"{query} {topic}".strip()
        for sort in ("date", "sim"):
            for page_no in range(pages):
                page = fetch_news(session, q, display=100, start=1 + 100 * page_no, sort=sort)
                if not page:
                    break
                for row in page:
                    if row["published_at"] and row["published_at"] < since:
                        continue
                    if not matches(row, must) or is_market_wrap(row["title"]) or row["url"] in by_url:
                        continue
                    key = _title_key(row["title"])
                    if key in seen_titles:
                        continue
                    seen_titles.add(key)
                    by_url[row["url"]] = row
    if topics and len(by_url) < FALLBACK_MIN:
        # 중소형주는 '종목명 + 실적/전망' 기사가 드물어 0건이 되기 쉬움 → 종목명만으로 한 번 더 (최신순·관련도순)
        for sort in ("date", "sim"):
            for row in fetch_news(session, query, display=100, start=1, sort=sort):
                if row["published_at"] and row["published_at"] < since:
                    continue
                if not matches(row, must) or is_market_wrap(row["title"]) or row["url"] in by_url:
                    continue
                key = _title_key(row["title"])
                if key in seen_titles:
                    continue
                seen_titles.add(key)
                by_url[row["url"]] = row
    rows = sorted(by_url.values(), key=lambda r: r["published_at"] or date.min, reverse=True)
    return rows[:max_items]


def collect_person_news(session, company: str, person: str, since: date, until: date,
                        topics: tuple[str, ...] = ("",), pages: int = 10) -> tuple[list[dict], dict]:
    """'회사명 인물명 [주제어]' 를 최신순·관련도순으로 최대 pages 페이지(검색 API 한계 1,000건)씩 모읍니다.

    제목에 인물 이름이 그대로 들어간 기사만, since~until 사이만, URL·제목 키로 중복 제거.
    반환: (기사 목록, {"calls": 호출 수, "oldest_seen": 최신순 검색에서 닿은 가장 오래된 날짜})
    네이버 검색 API 는 기간 지정이 없어, 기사가 많은 인물은 최신순으로 since 까지 닿지 못할 수 있습니다
    (관련도순·주제어 검색으로 보완). oldest_seen 으로 어디까지 닿았는지 확인합니다.
    """
    by_url: dict[str, dict] = {}
    seen_titles: set[str] = set()
    calls, oldest = 0, None
    for topic in topics:
        q = f"{company} {person} {topic}".strip()
        for sort in ("date", "sim"):
            for page_no in range(pages):
                page = fetch_news(session, q, display=100, start=1 + 100 * page_no, sort=sort)
                calls += 1
                if not page:
                    break
                dates = [r["published_at"] for r in page if r["published_at"]]
                if sort == "date" and dates:
                    oldest = min(dates) if oldest is None else min(oldest, min(dates))
                for row in page:
                    d = row["published_at"]
                    if d is None or d < since or d > until or person not in row["title"]:
                        continue
                    if row["url"] in by_url:
                        continue
                    key = _title_key(row["title"])
                    if key in seen_titles:
                        continue
                    seen_titles.add(key)
                    by_url[row["url"]] = row
                if sort == "date" and dates and max(dates) < since:
                    break  # 최신순이라 이 페이지가 전부 기간 앞이면 더 볼 필요 없음
                if len(page) < 100:
                    break
    rows = sorted(by_url.values(), key=lambda r: r["published_at"], reverse=True)
    return rows, {"calls": calls, "oldest_seen": oldest}

