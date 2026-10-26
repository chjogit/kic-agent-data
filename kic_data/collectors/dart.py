"""OpenDART: 고유번호(corp_code) 목록, 단일회사 전체 재무제표."""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile

from ..config import env
from ..http_util import RateLimiter, get, get_json
from ..util import to_decimal

BASE = "https://opendart.fss.or.kr/api"
_limiter = RateLimiter(0.15)

REPORT_ANNUAL = "11011"  # 사업보고서

STATUS = {
    "000": "정상",
    "010": "등록되지 않은 키",
    "011": "사용할 수 없는 키",
    "013": "조회된 데이터 없음",
    "020": "요청 제한 초과",
    "100": "필드 값 오류",
    "800": "시스템 점검 중",
    "900": "정의되지 않은 오류",
}


def parse_corp_codes(zip_bytes: bytes) -> list[dict]:
    """corpCode.xml(zip) → [{corp_code, corp_name, stock_code}] (상장사만)."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        name = zf.namelist()[0]
        root = ET.fromstring(zf.read(name))
    out = []
    for node in root.iter("list"):
        stock = (node.findtext("stock_code") or "").strip()
        if not stock:
            continue
        out.append(
            {
                "corp_code": (node.findtext("corp_code") or "").strip(),
                "corp_name": (node.findtext("corp_name") or "").strip(),
                "stock_code": stock,
            }
        )
    return out


def fetch_corp_codes(session) -> list[dict]:
    response = get(session, "dart", f"{BASE}/corpCode.xml", params={"crtfc_key": env("DART_API_KEY", required=True)}, limiter=_limiter)
    return parse_corp_codes(response.content)


def parse_financials(payload: dict, fiscal_year: int, report_code: str, fs_div: str) -> list[dict]:
    status = payload.get("status")
    if status == "013":
        return []
    if status != "000":
        raise RuntimeError(f"[dart] {status} {STATUS.get(status, payload.get('message', ''))}")
    rows = []
    for it in payload.get("list", []):
        rows.append(
            {
                "fiscal_year": fiscal_year,
                "report_code": report_code,
                "fs_div": fs_div,
                "sj_div": it.get("sj_div") or "",
                "account_id": it.get("account_id") or "",
                "account_nm": it.get("account_nm") or "",
                "amount": to_decimal(it.get("thstrm_amount")),
                "amount_ytd": to_decimal(it.get("thstrm_add_amount")),  # 반기·3분기 보고서의 누적값
                "currency": it.get("currency") or "KRW",
                "rcept_no": it.get("rcept_no"),
            }
        )
    return rows


def fetch_financials(session, corp_code: str, year: int, report_code: str = REPORT_ANNUAL, fs_div: str = "CFS") -> list[dict]:
    params = {
        "crtfc_key": env("DART_API_KEY", required=True),
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": report_code,
        "fs_div": fs_div,
    }
    payload = get_json(session, "dart", f"{BASE}/fnlttSinglAcntAll.json", params=params, limiter=_limiter)
    return parse_financials(payload, year, report_code, fs_div)


def parse_company(payload: dict) -> dict:
    """기업개황 → 업종코드(KSIC) 등."""
    status = payload.get("status")
    if status != "000":
        raise RuntimeError(f"[dart] {status} {STATUS.get(status, payload.get('message', ''))}")
    return {
        "induty_code": (payload.get("induty_code") or "").strip(),
        "corp_cls": payload.get("corp_cls"),
        "corp_name": payload.get("corp_name"),
    }


def fetch_company(session, corp_code: str) -> dict:
    params = {"crtfc_key": env("DART_API_KEY", required=True), "corp_code": corp_code}
    return parse_company(get_json(session, "dart", f"{BASE}/company.json", params=params, limiter=_limiter))


# ---------- 공시 목록 (list.json) ----------
# 정기공시 A · 주요사항보고 B · 거래소공시 I. 잠정실적은 '거래소공시(I)'에, 유상증자·자사주는 '주요사항보고(B)'에 있습니다.
DISCLOSURE_TYPES = ("A", "B", "I")

# 보고서명에 포함된 키워드로 사건 유형을 붙입니다 (앞의 것이 우선). 필요하면 추가하세요.
EVENT_KEYWORDS = [
    ("유상증자", "rights_issue"),
    ("무상증자", "bonus_issue"),
    ("자기주식", "buyback"),
    ("신주인수권부사채", "bond_with_warrant"),
    ("전환사채", "convertible_bond"),
    ("영업(잠정)실적", "earnings_prelim"),
    ("잠정", "earnings_prelim"),
    ("합병", "merger"),
    ("분할", "spin_off"),
    ("감자", "capital_reduction"),
    ("배당", "dividend"),
    ("단일판매", "supply_contract"),
    ("소송", "litigation"),
    ("횡령", "embezzlement"),
    ("관리종목", "delisting_risk"),
    ("상장폐지", "delisting_risk"),
    # --- 거래소공시(I)에서 많이 나오는 유형 (실제 수집 결과의 미분류 상위를 반영) ---
    ("조회공시요구", "rumor_inquiry"),
    ("풍문또는보도", "rumor_clarification"),
    ("매출액또는손익구조", "earnings_change"),
    ("장래사업", "guidance"),
    ("주식소각", "stock_retirement"),
    ("주식교환", "share_exchange"),
    ("타법인주식및출자증권취득", "investment_acquisition"),
    ("영업양수", "business_transfer"),
    ("영업양도", "business_transfer"),
    ("금전대여", "loan_decision"),
    ("신규시설투자", "capex"),
    ("유형자산취득", "capex"),
    ("전환청구권", "conversion_exercise"),
    ("최대주주등소유주식변동", "major_holder_change"),
    ("기업가치제고", "value_up_plan"),
    ("기업설명회", "ir_event"),
    ("주주총회", "shareholders_meeting"),
    ("주주명부폐쇄", "record_date"),
    ("감사보고서", "audit_report"),
    ("기업지배구조", "governance_report"),
    ("지속가능경영", "esg_report"),
    ("수시공시의무", "fair_disclosure"),
    ("기타경영사항", "other_management"),
    ("사업보고서", "annual_report"),
    ("반기보고서", "half_year_report"),
    ("분기보고서", "quarterly_report"),
]


def classify_event(report_nm: str) -> str | None:
    name = re.sub(r"\s+", "", report_nm or "")
    for kw, event in EVENT_KEYWORDS:
        if kw in name:
            return event
    return None


def parse_disclosures(payload: dict, pblntf_ty: str) -> tuple[list[dict], int]:
    """list.json 응답 → (행 목록, 전체 페이지 수)"""
    status = payload.get("status")
    if status == "013":
        return [], 0
    if status != "000":
        raise RuntimeError(f"[dart] {status} {STATUS.get(status, payload.get('message', ''))}")
    rows = []
    for it in payload.get("list", []):
        d = (it.get("rcept_dt") or "").strip()
        if len(d) != 8:
            continue
        no = it["rcept_no"]
        rows.append(
            {
                "rcept_no": no,
                "report_nm": (it.get("report_nm") or "").strip(),
                "rcept_dt": f"{d[:4]}-{d[4:6]}-{d[6:]}",
                "filer": (it.get("flr_nm") or "").strip(),
                "category": pblntf_ty,
                "event_type": classify_event(it.get("report_nm")),
                "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={no}",
            }
        )
    return rows, int(payload.get("total_page") or 1)


def fetch_disclosures(session, corp_code: str, bgn_de: str, end_de: str, pblntf_ty: str, max_pages: int = 10) -> list[dict]:
    rows, page = [], 1
    while page <= max_pages:
        params = {
            "crtfc_key": env("DART_API_KEY", required=True),
            "corp_code": corp_code,
            "bgn_de": bgn_de,
            "end_de": end_de,
            "pblntf_ty": pblntf_ty,
            "page_no": page,
            "page_count": 100,
        }
        payload = get_json(session, "dart", f"{BASE}/list.json", params=params, limiter=_limiter)
        part, total_pages = parse_disclosures(payload, pblntf_ty)
        rows += part
        if page >= total_pages:
            break
        page += 1
    return rows
