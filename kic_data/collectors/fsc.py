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


# 이 두 필드가 비면 행을 쓸 수 없습니다 (기준일·종가). 2026-09 V2 전환처럼 필드명이 바뀌면 모든 행에서 비게 됩니다.
REQUIRED_FIELDS = ("basDt", "clpr")


def _missing(field: str, value) -> bool:
    if field == "clpr":  # '-' 처럼 숫자로 못 읽는 값도 종가 없음으로 봅니다
        return to_decimal(value) is None
    return value is None or not str(value).strip()


def parse_prices(payload: dict) -> list[dict]:
    """응답 JSON → core.price_daily 용 행 목록.

    필수 필드(REQUIRED_FIELDS)가 빈 행은 버리고 경고합니다. 예전에는 종가 없는 행이 close=NULL 로 저장돼
    이미 있던 정상 값을 덮어쓸 수 있었습니다. 모든 행에서 비면 응답 형식이 바뀐 것으로 보고 실패합니다."""
    header = payload.get("response", {}).get("header", {})
    if header.get("resultCode") not in ("00", None):
        raise RuntimeError(f"[fsc] 오류 {header.get('resultCode')}: {header.get('resultMsg')}")
    items = payload.get("response", {}).get("body", {}).get("items", {})
    if not items or isinstance(items, str):
        return []
    item = items.get("item", [])
    if isinstance(item, dict):
        item = [item]
    rows, skipped = [], []
    for it in item:
        missing = [f for f in REQUIRED_FIELDS if _missing(f, it.get(f))]
        if missing:
            skipped.append((it.get("srtnCd"), it.get("basDt"), missing))
            continue
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
                "ticker": (it.get("srtnCd") or "").strip().lstrip("A") or None,  # 전 종목 조회에서 종목 구분
            }
        )
    if skipped and not rows:
        raise RuntimeError(
            f"[fsc] 응답 형식 변경 의심: 필수 필드 {'/'.join(REQUIRED_FIELDS)} 가 모든 행({len(skipped)}행)에서 비어 있음 "
            f"(예: 종목 {skipped[0][0]}, 기준일 {skipped[0][1]}, 빈 필드 {skipped[0][2]})"
        )
    if skipped:
        print(f"[WARN] fsc 필수 필드 빈 행 {len(skipped)}개 제외 (예: 종목 {skipped[0][0]}, "
              f"기준일 {skipped[0][1]}, 빈 필드 {skipped[0][2]})")
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


def total_count(payload: dict) -> int:
    return to_int(payload.get("response", {}).get("body", {}).get("totalCount")) or 0


def fetch_all_prices(session, begin: str, end: str, rows: int = 5000, max_pages: int = 40) -> list[dict]:
    """기간 안의 전 종목 시세 (종목 지정 없음). KOSPI 200 처럼 종목이 많을 때 종목별 호출 대신 씁니다.

    하루 약 2,800행(코스피·코스닥·코넥스)이라 10일이면 5,000행씩 6쪽 정도입니다.
    """
    out, page = [], 1
    while page <= max_pages:
        params = {
            "serviceKey": env("FSC_API_KEY", required=True),
            "resultType": "json",
            "numOfRows": rows,
            "pageNo": page,
            "beginBasDt": begin,
            "endBasDt": end,
        }
        payload = get_json(session, "fsc", URL, params=params, limiter=_limiter, timeout=90)
        part = parse_prices(payload)
        out += part
        if not part or page * rows >= total_count(payload):
            break
        page += 1
    return out


def latest_shares(rows: list[dict]) -> tuple[int, object] | None:
    """시세 행 중 가장 최근 날짜의 상장주식수 → (주식수, 기준일)."""
    rows = [r for r in rows if r.get("shares")]
    if not rows:
        return None
    last = max(rows, key=lambda r: r["trade_date"])
    return last["shares"], last["trade_date"]
