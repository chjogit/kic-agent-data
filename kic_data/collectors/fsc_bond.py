"""회사채 발행 종목 (공공데이터포털 '금융위원회_채권발행정보', 2026-10-29 활용신청 승인, 2026-10-30 부터 사용).

에이전트 1 의 차입 조건(D2): 발행금액·표면이율·금리구분(고정·변동)·만기일. 사채·CP 잔액 공시가 없는 회사의 빈 곳을 채우는 데 씁니다.
  - 엔드포인트: GetBondTradInfoService_V2 / getIssuIssuItemStat_V2 (발행자별 발행 종목 현황, 포털 상세기능 기준)
  - 조회: 법인등록번호(crno) 또는 채권발행인명(bondIsurNm), 기준일자(basDt). 기준일 다음 영업일 오후 1시 이후 갱신.
  - 응답 필드(2026-10-29 첫 응답으로 확인): isinCd·isinCdNm·bondIsurNm·bondIssuDt·bondExprDt·bondIssuAmt(원)·
    bondIssuCurCd·bondSrfcInrt(%)·irtChngDcdNm('고정'/'변동')·scrsItmsKcdNm(일반회사채 등)·bondIntTcdNm(이표채 등).
    법인등록번호만 주고 기준일자를 빼면 그 회사가 발행한 종목 전체(만기 지난 것 포함)가 옵니다. 기준일자를 주면 0건인 경우가 많음.
    발행사 본인 종목만 나옵니다(연결 자회사 발행분 없음). 조기상환·콜은 반영되지 않으므로 '만기 전 = 미상환'은 근사입니다.
    이름이 바뀌면 후보·값 모양으로 다시 고르고, 원 응답(raw)을 함께 저장해 나중에 다시 해석할 수 있게 합니다.
이용허락: 공공누리 2유형(출처표시·상업적 이용금지) → 보고서에 '출처: 금융위원회(한국예탁결제원)'를 표시하세요.
"""
from __future__ import annotations

import re

from ..config import data_go_key
from ..http_util import RateLimiter, get_json
from ..util import parse_ymd, to_decimal, to_int

URL = "https://apis.data.go.kr/1160100/GetBondTradInfoService_V2/getIssuIssuItemStat_V2"
_limiter = RateLimiter(0.3)
ROWS = 1000

# 필드 이름 (앞의 것이 확인된 이름, 없으면 정규식으로 찾음)
KEYS = {
    "isin": (("isinCd",), r"^isinCd$"),
    "bond_name": (("isinCdNm", "bondNm"), r"(isinCdNm|ItmsNm|bondNm)$"),
    "issuer": (("bondIsurNm",), r"Isur.*Nm$"),
    "issue_date": (("bondIssuDt", "issuDt"), r"Issu.*Dt$"),
    "maturity_date": (("bondExprDt", "exprDt"), r"(Expr|Matu|Mtrt).*Dt$"),
    "issue_amount": (("bondIssuAmt", "issuAmt"), r"Issu.*Amt$"),
    "currency": (("bondIssuCurCd", "issuCurCd"), r"Cur.*Cd$"),
    "coupon_rate": (("bondSrfcInrt", "srfcInrt", "surfIntrt"), r"(Srfc|Surf|srfc|surf).*(Inrt|Intrt|Rt)$"),
    "rate_type": (("irtChngDcdNm",), r"(Intrt|Inrt|irt|Irt).*(Clsf|Tp|Knd|Dcd).*Nm$"),
    "bond_type": (("scrsItmsKcdNm",), r"(Knd|Kcd).*Nm$"),
}
FIXED_RE = re.compile(r"고정")
FLOAT_RE = re.compile(r"변동")


def _field(it: dict, name: str):
    exact, pat = KEYS[name]
    for k in exact:
        if it.get(k) not in (None, ""):
            return it[k]
    for k, v in it.items():
        if v not in (None, "") and re.search(pat, k):
            return v
    return None


def _date(v):
    """'20290310' · '2029-03-10' → date, 그 밖(빈 값·'-'·'99991231' 영구채 표기 등)은 None."""
    t = re.sub(r"[^0-9]", "", str(v or ""))
    if len(t) != 8 or t.startswith("9999"):
        return None
    try:
        return parse_ymd(t)
    except ValueError:
        return None


def rate_kind(text) -> str | None:
    """금리구분 원문 → fixed / floating (모르면 None). 값만 보고 고르므로 필드 이름이 달라도 동작."""
    t = str(text or "")
    if FLOAT_RE.search(t):
        return "floating"
    if FIXED_RE.search(t):
        return "fixed"
    return None


def parse_items(payload: dict) -> tuple[list[dict], int]:
    """응답 JSON → (행 목록, totalCount). 행마다 원 응답(raw)을 함께 둡니다."""
    if not isinstance(payload, dict) or "response" not in payload:
        raise RuntimeError("[fsc_bond] 응답 구조가 바뀜 (response 없음)")
    header = payload["response"].get("header", {})
    if header.get("resultCode") not in ("00", None):
        raise RuntimeError(f"[fsc_bond] 오류 {header.get('resultCode')}: {header.get('resultMsg')}")
    body = payload["response"].get("body", {})
    items = body.get("items") or {}
    item = items.get("item", []) if isinstance(items, dict) else []
    if isinstance(item, dict):
        item = [item]
    rows = []
    for it in item:
        isin = (_field(it, "isin") or "").strip()
        if not isin:
            continue
        rt = _field(it, "rate_type")
        kind = rate_kind(rt)
        if kind is None:   # 금리구분 필드 이름을 못 맞췄으면 값이 '고정'·'변동'인 칸을 찾음
            kind = next((rate_kind(v) for v in it.values() if isinstance(v, str) and len(v) <= 8 and rate_kind(v)), None)
        rows.append({
            "isin": isin,
            "bond_name": _field(it, "bond_name"),
            "issuer": _field(it, "issuer"),
            "issue_date": _date(_field(it, "issue_date")),
            "maturity_date": _date(_field(it, "maturity_date")),
            "issue_amount": to_decimal(_field(it, "issue_amount")),
            "currency": _field(it, "currency"),
            "coupon_rate": to_decimal(_field(it, "coupon_rate")),
            "rate_type": rt,
            "rate_kind": kind,
            "bond_type": _field(it, "bond_type"),
            "bas_dt": _date(it.get("basDt")),
            "raw": it,
        })
    return rows, to_int(body.get("totalCount")) or 0


def fetch_issuer(session, crno: str, bas_dt: str | None = None, max_pages: int = 5) -> list[dict]:
    """법인등록번호 하나의 발행 종목 (bas_dt: YYYYMMDD, 없으면 원천 기본값)."""
    out = []
    for page in range(1, max_pages + 1):
        params = {"serviceKey": data_go_key(), "resultType": "json", "numOfRows": ROWS, "pageNo": page, "crno": crno}
        if bas_dt:
            params["basDt"] = bas_dt
        payload = get_json(session, "fsc_bond", URL, params=params, limiter=_limiter, timeout=60)
        rows, total = parse_items(payload)
        out += rows
        if not rows or page * ROWS >= total:
            break
    return out


def fetch_raw(session, crno: str, bas_dt: str | None = None, rows: int = 5) -> dict:
    """확인용: 원 응답 그대로 (probe-bond-issue)."""
    params = {"serviceKey": data_go_key(), "resultType": "json", "numOfRows": rows, "pageNo": 1, "crno": crno}
    if bas_dt:
        params["basDt"] = bas_dt
    return get_json(session, "fsc_bond", URL, params=params, limiter=_limiter, timeout=60)
