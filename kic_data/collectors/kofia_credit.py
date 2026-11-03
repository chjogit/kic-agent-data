"""신용공여 잔고 추이 (공공데이터포털 '금융위원회_금융투자협회종합통계정보' 15094809, 11/4 부터 키 작동). 시장 전체 합계이며 종목별이 아닙니다.

에이전트 4(수급)·6(심리)의 시장 배경(레버리지 지표)용. 엔드포인트·필드 이름은 활용자 가이드/첫 응답으로 확인해야 하므로(11/3 시점 미확인):
  - URL 은 포털 서비스명(GetKofiaStatisticsInfoService) 기준 추정입니다. 틀리면 `probe-credit-balance` 로 확인해 URL 만 고치세요.
  - 날짜(basDt)는 값 모양으로 읽고, 숫자 칸은 이름 그대로 values 에 모두 두며, 원 응답(raw)도 저장해 나중에 다시 해석합니다.
  - 인증 오류(키 미승인·미적용)는 '건너뜀'으로 처리합니다 (호출 쪽).
이용허락: 공공누리 — 보고서에 '출처: 금융위원회(금융투자협회)'를 표시하세요.
"""
from __future__ import annotations

import re

from ..config import data_go_key
from ..http_util import RateLimiter, get_json
from ..util import parse_ymd, to_decimal, to_int

URL = "https://apis.data.go.kr/1160100/service/GetKofiaStatisticsInfoService/getGrantingOfCreditBalanceInfo"
_limiter = RateLimiter(0.3)
ROWS = 1000
LOAN_RE = re.compile(r"(Fing|융자|loan)", re.I)   # 신용거래융자 잔고 후보 필드 이름
AUTH_RE = re.compile(r"SERVICE.?KEY|NO_OPENAPI|UNREGISTERED|NOT_REGISTERED", re.I)


class AuthSkipped(RuntimeError):
    """키가 아직 이 서비스에 승인·적용되지 않음 (호출 쪽이 '건너뜀'으로 기록)."""


def _date(v):
    t = re.sub(r"[^0-9]", "", str(v or ""))
    if len(t) != 8:
        return None
    try:
        return parse_ymd(t)
    except ValueError:
        return None


def parse_items(payload: dict) -> tuple[list[dict], int]:
    """응답 JSON → (행 목록, totalCount). basDt 가 없는 행은 버리고, 숫자 칸은 모두 values 에 둡니다."""
    if not isinstance(payload, dict) or "response" not in payload:
        raise RuntimeError("[kofia_credit] 응답 구조가 바뀜 (response 없음)")
    header = payload["response"].get("header", {})
    code, msg = header.get("resultCode"), header.get("resultMsg")
    if code not in ("00", None):
        if AUTH_RE.search(str(msg)) or code in ("20", "30", "31", "32"):
            raise AuthSkipped(f"[kofia_credit] 인증 오류 {code}: {msg}")
        raise RuntimeError(f"[kofia_credit] 오류 {code}: {msg}")
    body = payload["response"].get("body", {})
    items = body.get("items") or {}
    item = items.get("item", []) if isinstance(items, dict) else []
    if isinstance(item, dict):
        item = [item]
    rows = []
    for it in item:
        d = _date(it.get("basDt"))
        if d is None:
            continue
        values = {k: to_decimal(v) for k, v in it.items() if k != "basDt" and to_decimal(v) is not None}
        loan = next((v for k, v in values.items() if LOAN_RE.search(k)), None)
        rows.append({"bas_dt": d, "loan_total": loan, "values": {k: str(v) for k, v in values.items()}, "raw": it})
    return rows, to_int(body.get("totalCount")) or 0


def fetch_range(session, begin: str, end: str, max_pages: int = 20) -> list[dict]:
    """begin·end: YYYYMMDD (포함). 페이지를 끝까지 받음."""
    out = []
    for page in range(1, max_pages + 1):
        params = {"serviceKey": data_go_key(), "resultType": "json", "numOfRows": ROWS, "pageNo": page,
                  "beginBasDt": begin, "endBasDt": end}
        rows, total = parse_items(get_json(session, "kofia_credit", URL, params=params, limiter=_limiter, timeout=60))
        out += rows
        if not rows or page * ROWS >= total:
            break
    return out


def fetch_raw(session, begin: str, end: str, rows: int = 5) -> dict:
    """확인용: 원 응답 그대로 (probe-credit-balance)."""
    params = {"serviceKey": data_go_key(), "resultType": "json", "numOfRows": rows, "pageNo": 1,
              "beginBasDt": begin, "endBasDt": end}
    return get_json(session, "kofia_credit", URL, params=params, limiter=_limiter, timeout=60)
