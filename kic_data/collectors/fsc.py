"""공공데이터포털 금융위원회 주식시세정보 (국내 일별 시세).

주의: V2 응답 필드명(basDt, clpr 등)과 파라미터명은 Swagger 의 Models 로 한 번 더 확인하세요. 엔드포인트 경로와 파라미터명은 포털의 '활용신청 > 상세기능'에서 한 번 확인하세요.
인증키는 '일반 인증키(Decoding)' 를 .env 에 넣습니다 (requests 가 인코딩).
"""
from __future__ import annotations

from ..config import env
from ..http_util import RateLimiter, get_json
from ..util import parse_ymd, to_decimal, to_int

# 2026-09 변경: V2 (포털 상세기능 Swagger 의 Base URL 기준: apis.data.go.kr/1160100/GetStockSecuritiesInfoService_V2)
URL = "https://apis.data.go.kr/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2"
_limiter = RateLimiter(0.2)


def parse_prices(payload: dict) -> list[dict]:
    """응답 JSON → core.price_daily 용 행 목록."""
    header = payload.get("response", {}).get("header", {})
    if header.get("resultCode") not in ("00", None):
        raise RuntimeError(f"[fsc] 오류 {header.get('resultCode')}: {header.get('resultMsg')}")
    items = payload.get("response", {}).get("body", {}).get("items", {})
    if not items or isinstance(items, str):
        return []
    item = items.get("item", [])
    if isinstance(item, dict):
        item = [item]
    rows = []
    for it in item:
        rows.append(
            {
                "trade_date": parse_ymd(it["basDt"]),
                "open": to_decimal(it.get("mkp")),
                "high": to_decimal(it.get("hipr")),
                "low": to_decimal(it.get("lopr")),
                "close": to_decimal(it.get("clpr")),
                "volume": to_int(it.get("trqu")),
                "trade_value": to_decimal(it.get("trPrc")),
                "market_cap": to_decimal(it.get("mrktTotAmt")),
                "shares": to_int(it.get("lstgStCnt")),  # 상장주식수 (DB 시세 테이블엔 저장하지 않음)
            }
        )
    return rows


def fetch_prices(session, ticker: str, begin: str, end: str, rows: int = 1000) -> list[dict]:
    params = {
        "serviceKey": env("FSC_API_KEY", required=True),
        "resultType": "json",
        "numOfRows": rows,
        "pageNo": 1,
        "likeSrtnCd": ticker,
        "beginBasDt": begin,
        "endBasDt": end,
    }
    payload = get_json(session, "fsc", URL, params=params, limiter=_limiter)
    return parse_prices(payload)


def latest_shares(rows: list[dict]) -> tuple[int, object] | None:
    """시세 행 중 가장 최근 날짜의 상장주식수 → (주식수, 기준일)."""
    rows = [r for r in rows if r.get("shares")]
    if not rows:
        return None
    last = max(rows, key=lambda r: r["trade_date"])
    return last["shares"], last["trade_date"]
