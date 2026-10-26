"""KOSPI 200 구성 종목 (네이버 증권 모바일 공개 API, 로그인 불필요).

KRX 정보데이터시스템은 로그인이 필요해 쓰지 않습니다 (팀 공유 프로젝트 원칙).
네이버 증권 '코스피 200 편입 종목' 목록은 한 쪽에 20종목씩, 약 11쪽입니다 (2026-10 확인).
비공식 API 라 구조가 바뀌면 개수 검사에서 실패로 남고, 이전에 받은 구성 종목은 그대로 둡니다.
"""
from __future__ import annotations

from ..http_util import RateLimiter, get_json

URL = "https://m.stock.naver.com/api/index/KPI200/enrollStocks"
HEADERS = {"User-Agent": "Mozilla/5.0 (kic-agent-data)", "Referer": "https://m.stock.naver.com/"}
_limiter = RateLimiter(0.3)
EXPECTED = (190, 210)  # 정기 변경·특례 편입이 있어도 이 범위 안


def parse_enroll(payload) -> list[tuple[str, str]]:
    if not isinstance(payload, list):
        return []
    return [(str(it["itemCode"]).strip(), (it.get("stockName") or "").strip())
            for it in payload if isinstance(it, dict) and it.get("itemCode")]


def fetch_constituents(session, max_pages: int = 30) -> list[tuple[str, str]]:
    """[(티커, 이름)] — 개수가 예상 범위를 벗어나면 구조가 바뀐 것으로 보고 실패."""
    out, seen = [], set()
    for page in range(1, max_pages + 1):
        payload = get_json(session, "kospi200", URL, params={"page": page}, headers=HEADERS, limiter=_limiter)
        new = [(c, n) for c, n in parse_enroll(payload) if c not in seen]
        if not new:
            break
        seen.update(c for c, _ in new)
        out += new
    if not EXPECTED[0] <= len(out) <= EXPECTED[1]:
        raise RuntimeError(f"[kospi200] 구성 종목 {len(out)}개 (예상 {EXPECTED[0]}~{EXPECTED[1]}): 응답 구조 확인 필요")
    return out
