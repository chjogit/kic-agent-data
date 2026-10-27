"""공공데이터포털 금융위원회 증권상품시세정보 (국내 ETF 일별 시세·NAV).

주식시세정보와 같은 공공데이터포털 계정의 일반 인증키를 쓰지만, '증권상품시세정보' 를 따로 활용신청해야 합니다.
엔드포인트는 활용자가이드(2026) 기준 V2 입니다. 예전 주소(/service/GetSecuritiesProductInfoService/getETFPriceInfo)는
승인된 서비스로 인식되지 않아 403 Forbidden 이 납니다.
endBasDt 는 '검색값보다 작은' 날짜만 돌려주므로(끝 날짜 미포함) 하루를 더해 보냅니다.
"""
from __future__ import annotations

from datetime import date, timedelta

from ..config import data_go_key
from ..http_util import RateLimiter, get_json
from ..util import parse_ymd, to_decimal, to_int

URL = "https://apis.data.go.kr/1160100/GetSecuritiesProductInfoService_V2/getETFPriceInfo_V2"
_limiter = RateLimiter(0.2)


def parse_etf(payload: dict) -> list[dict]:
    header = payload.get("response", {}).get("header", {})
    if header.get("resultCode") not in ("00", None):
        raise RuntimeError(f"[fsc-etf] 오류 {header.get('resultCode')}: {header.get('resultMsg')}")
    items = payload.get("response", {}).get("body", {}).get("items", {})
    if not items or isinstance(items, str):
        return []
    item = items.get("item", [])
    if isinstance(item, dict):
        item = [item]
    return [
        {
            "trade_date": parse_ymd(it["basDt"]),
            "ticker": str(it.get("srtnCd", "")).strip(),
            "name": it.get("itmsNm"),
            "close": to_decimal(it.get("clpr")),
            "nav": to_decimal(it.get("nav")),
            "volume": to_int(it.get("trqu")),
            "trade_value": to_decimal(it.get("trPrc")),
            "market_cap": to_decimal(it.get("mrktTotAmt")),
            "net_assets": to_decimal(it.get("nPptTotAmt")),
            "base_index": it.get("bssIdxIdxNm"),
        }
        for it in item
    ]


def next_ymd(ymd: str) -> str:
    return (date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8])) + timedelta(days=1)).strftime("%Y%m%d")


def fetch_etf(session, ticker: str, begin: str, end: str, rows: int = 1000) -> list[dict]:
    params = {
        "serviceKey": data_go_key(),
        "resultType": "json",
        "numOfRows": rows,
        "pageNo": 1,
        "likeSrtnCd": ticker,
        "beginBasDt": begin,
        "endBasDt": next_ymd(end),  # 끝 날짜 미포함이라 하루 뒤까지
    }
    payload = get_json(session, "fsc-etf", URL, params=params, limiter=_limiter)
    # likeSrtnCd 는 부분 일치라 다른 종목이 섞일 수 있어 정확히 같은 코드만 남깁니다
    return [r for r in parse_etf(payload) if r["ticker"] == ticker]
