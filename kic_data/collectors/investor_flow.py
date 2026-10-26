"""투자자별 일별 순매수 (네이버 증권 모바일 공개 API, 로그인 불필요).

KRX 정보데이터시스템의 투자자별 매매·공매도 통계는 2025-12-27 부터 로그인이 필요해 쓰지 않습니다 (팀 공유 프로젝트 원칙).
네이버 종목 '투자자별 매매동향' 화면이 쓰는 JSON 으로, 외국인·기관·개인 순매수 수량과 외국인 보유율을 줍니다 (2026-10 확인).
비공식 API 라 예고 없이 바뀔 수 있고, 약관상 대량·고빈도 수집은 피해야 해서 종목당 하루 1회, 호출 간격 0.5초로 받습니다.
bizdate 를 주면 그 날짜 '이전' 거래일부터 pageSize 개를 돌려줍니다 → 가장 오래된 날짜를 다음 bizdate 로 넘겨 과거로 갑니다.
"""
from __future__ import annotations

from datetime import date

from ..http_util import RateLimiter, get_json
from ..util import parse_ymd, to_decimal, to_int, ymd

URL = "https://m.stock.naver.com/api/stock/{code}/trend"
HEADERS = {"User-Agent": "Mozilla/5.0 (kic-agent-data)", "Referer": "https://m.stock.naver.com/"}
PAGE = 60
_limiter = RateLimiter(0.5)


def parse_trend(payload) -> list[dict]:
    """응답 JSON → core.investor_flow_daily 용 행 목록."""
    if not isinstance(payload, list):
        raise RuntimeError("[investor_flow] 응답 구조가 바뀜 (목록 아님)")
    rows = []
    for it in payload:
        if not isinstance(it, dict) or not it.get("bizdate"):
            continue
        hold = to_decimal(str(it.get("foreignerHoldRatio") or "").replace("%", ""))
        rows.append({
            "trade_date": parse_ymd(it["bizdate"]),
            "foreign_net_qty": to_int(it.get("foreignerPureBuyQuant")),
            "institution_net_qty": to_int(it.get("organPureBuyQuant")),
            "individual_net_qty": to_int(it.get("individualPureBuyQuant")),
            "foreign_hold_pct": hold,
            "volume": to_int(it.get("accumulatedTradingVolume")),
        })
    return rows


def fetch_flows(session, ticker: str, since: date, max_pages: int = 10) -> list[dict]:
    """since 이후 일별 행 (오름차순). 오래된 쪽으로 한 쪽씩 넘깁니다."""
    by_date: dict[date, dict] = {}
    params = {"pageSize": PAGE}
    for _ in range(max_pages):
        payload = get_json(session, "investor_flow", URL.format(code=ticker), params=params, headers=HEADERS,
                           limiter=_limiter)
        rows = parse_trend(payload)
        new = [r for r in rows if r["trade_date"] not in by_date]
        for r in new:
            by_date[r["trade_date"]] = r
        oldest = min((r["trade_date"] for r in rows), default=None)
        if not new or oldest is None or oldest <= since or len(rows) < PAGE:
            break
        params = {"pageSize": PAGE, "bizdate": ymd(oldest)}
    return [by_date[d] for d in sorted(by_date) if d >= since]
