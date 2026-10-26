"""KOSPI 200 구성 종목 (네이버 금융 공개 페이지, 로그인 불필요).

KRX 정보데이터시스템은 로그인이 필요해 쓰지 않습니다 (팀 공유 프로젝트 원칙).
네이버 금융 '코스피200 편입종목' 표는 한 쪽에 10종목씩, 약 20쪽입니다.
"""
from __future__ import annotations

import re

from ..http_util import RateLimiter, get

URL = "https://finance.naver.com/sise/entryJongmok.naver"
_limiter = RateLimiter(0.3)
_ROW = re.compile(r'href="/item/main\.naver\?code=(\d{6})"[^>]*>([^<]+)</a>')
EXPECTED = (190, 210)  # 정기 변경·특례 편입이 있어도 이 범위 안


def parse_entry_page(html: str) -> list[tuple[str, str]]:
    seen, out = set(), []
    for code, name in _ROW.findall(html):
        if code not in seen:
            seen.add(code)
            out.append((code, name.strip()))
    return out


def fetch_constituents(session, max_pages: int = 30) -> list[tuple[str, str]]:
    """[(티커, 이름)] — 개수가 예상 범위를 벗어나면 페이지 구조가 바뀐 것으로 보고 실패."""
    out, seen = [], set()
    headers = {"User-Agent": "Mozilla/5.0 (kic-agent-data)"}
    for page in range(1, max_pages + 1):
        res = get(session, "kospi200", URL, params={"type": "KPI200", "page": page}, headers=headers,
                  limiter=_limiter)
        html = res.content.decode("euc-kr", errors="replace")
        new = [(c, n) for c, n in parse_entry_page(html) if c not in seen]
        if not new:
            break
        seen.update(c for c, _ in new)
        out += new
    if not EXPECTED[0] <= len(out) <= EXPECTED[1]:
        raise RuntimeError(f"[kospi200] 구성 종목 {len(out)}개 (예상 {EXPECTED[0]}~{EXPECTED[1]}): 페이지 구조 확인 필요")
    return out
