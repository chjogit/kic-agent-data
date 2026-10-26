"""종목별 일별 주식 대차거래 (공공데이터포털 '금융위원회_주식대차거래정보', 2026-10-26 활용신청 승인).

공매도는 먼저 주식을 빌려야 하므로 대차잔고를 공매도 압력의 대용 지표로 씁니다.
KRX 공매도 거래·잔고 통계는 2025-12-27 부터 로그인이 필요해 쓰지 않습니다 (팀 공유 프로젝트 원칙).
  - 기준일자(basDt)와 시장구분(코스피)으로 부르면 그날 코스피 전 종목이 옵니다 (하루 약 900행) → 종목마다 부르지 않고 하루 1회.
  - 휴장일은 0행. 기준일 다음 영업일 오후 1시 이후 갱신, 2021-04 이후 자료만 제공.
이용허락: 공공누리 4유형(출처표시·상업적 이용금지·변경금지) → 보고서에 출처를 표시하세요.
"""
from __future__ import annotations

from ..config import data_go_key
from ..http_util import RateLimiter, get_json
from ..util import parse_ymd, to_decimal, to_int, ymd

URL = "https://apis.data.go.kr/1160100/service/GetCMStckLnbInfoService/getStckLnbDetail"
_limiter = RateLimiter(0.3)
ROWS = 10000


def parse_lending(payload: dict) -> tuple[list[dict], int]:
    """응답 JSON → (행 목록, totalCount)."""
    if not isinstance(payload, dict) or "response" not in payload:
        raise RuntimeError("[lending] 응답 구조가 바뀜 (response 없음)")
    header = payload["response"].get("header", {})
    if header.get("resultCode") not in ("00", None):
        raise RuntimeError(f"[lending] 오류 {header.get('resultCode')}: {header.get('resultMsg')}")
    body = payload["response"].get("body", {})
    items = body.get("items") or {}
    item = items.get("item", []) if isinstance(items, dict) else []
    if isinstance(item, dict):
        item = [item]
    rows = []
    for it in item:
        ticker = (it.get("stckItmsCd") or "").strip().lstrip("A")
        if not ticker or not it.get("basDt"):
            continue
        rows.append({
            "ticker": ticker,
            "trade_date": parse_ymd(it["basDt"]),
            "lent_qty": to_int(it.get("cclStckCnt")),
            "repaid_qty": to_int(it.get("rdptStckCnt")),
            "balance_qty": to_int(it.get("balnStckCnt")),
            "balance_amt": to_decimal(it.get("balnStckAmt")),
        })
    return rows, to_int(body.get("totalCount")) or 0


def fetch_day(session, day, market: str = "코스피", max_pages: int = 3) -> list[dict]:
    """day 하루의 market 전 종목 행 (휴장일이면 빈 목록)."""
    out = []
    for page in range(1, max_pages + 1):
        params = {"serviceKey": data_go_key(), "resultType": "json", "numOfRows": ROWS, "pageNo": page,
                  "basDt": ymd(day), "mrktClsfNm": market}
        payload = get_json(session, "lending", URL, params=params, limiter=_limiter, timeout=90)
        rows, total = parse_lending(payload)
        out += rows
        if not rows or page * ROWS >= total:
            break
    return out
