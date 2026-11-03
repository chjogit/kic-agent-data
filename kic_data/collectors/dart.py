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


# 다중회사 주요계정(fnlttMultiAcnt): 계정 ID 가 없어 이름으로 단일회사 API 와 같은 ID 를 붙입니다.
MULTI_MAX = 100  # 한 번에 넣을 수 있는 고유번호 수
_MULTI_IDS = [
    (("매출액", "수익(매출액)", "영업수익", "매출"), "ifrs-full_Revenue"),
    (("영업이익", "영업이익(손실)", "영업손실"), "dart_OperatingIncomeLoss"),
    (("당기순이익", "당기순이익(손실)", "당기순손실", "분기순이익", "분기순이익(손실)", "반기순이익",
      "반기순이익(손실)"), "ifrs-full_ProfitLoss"),
    (("자산총계",), "ifrs-full_Assets"),
    (("부채총계",), "ifrs-full_Liabilities"),
    (("자본총계",), "ifrs-full_Equity"),
]


def multi_account_id(account_nm: str) -> str:
    nm = (account_nm or "").replace(" ", "")
    for names, aid in _MULTI_IDS:
        if nm in names:
            return aid
    return ""


def parse_multi(payload: dict, fiscal_year: int, report_code: str) -> dict[str, list[dict]]:
    """응답 → {종목코드: [financial_fact 행]}. 연결(CFS)·별도(OFS)를 모두 담습니다."""
    status = payload.get("status")
    if status == "013":
        return {}
    if status != "000":
        raise RuntimeError(f"[dart] {status} {STATUS.get(status, payload.get('message', ''))}")
    out: dict[str, list[dict]] = {}
    for it in payload.get("list", []):
        key = (it.get("stock_code") or "").strip() or (it.get("corp_code") or "").strip()
        if not key:
            continue
        out.setdefault(key, []).append(
            {
                "fiscal_year": fiscal_year,
                "report_code": report_code,
                "fs_div": it.get("fs_div") or "",
                "sj_div": it.get("sj_div") or "",
                "account_id": multi_account_id(it.get("account_nm")),
                "account_nm": it.get("account_nm") or "",
                "amount": to_decimal(it.get("thstrm_amount")),
                "amount_ytd": to_decimal(it.get("thstrm_add_amount")),
                "currency": it.get("currency") or "KRW",
                "rcept_no": it.get("rcept_no"),
            }
        )
    return out


def fetch_multi(session, corp_codes: list[str], year: int, report_code: str = REPORT_ANNUAL) -> dict[str, list[dict]]:
    if len(corp_codes) > MULTI_MAX:
        raise ValueError(f"corp_code 는 한 번에 {MULTI_MAX}개까지")
    params = {
        "crtfc_key": env("DART_API_KEY", required=True),
        "corp_code": ",".join(corp_codes),
        "bsns_year": str(year),
        "reprt_code": report_code,
    }
    payload = get_json(session, "dart", f"{BASE}/fnlttMultiAcnt.json", params=params, limiter=_limiter, timeout=90)
    return parse_multi(payload, year, report_code)


def parse_company(payload: dict) -> dict:
    """기업개황 → 업종코드(KSIC), 정식 회사명 등."""
    status = payload.get("status")
    if status != "000":
        raise RuntimeError(f"[dart] {status} {STATUS.get(status, payload.get('message', ''))}")
    return {
        "induty_code": (payload.get("induty_code") or "").strip(),
        "corp_cls": payload.get("corp_cls"),
        "corp_name": (payload.get("corp_name") or "").strip() or None,
        "jurir_no": re.sub(r"[^0-9]", "", payload.get("jurir_no") or "") or None,  # 법인등록번호 (채권발행정보 조회 키)
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
    # --- 지분공시(D). 임원·주요주주 소유상황보고서는 event_type 을 비워 둠(기존 테스트가 None 으로 고정, category 'D' 로 구분) ---
    ("대량보유상황보고서", "major_holding_report"),
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
                "corp_code": (it.get("corp_code") or "").strip(),  # 시장 전체 조회에서 회사 구분
            }
        )
    return rows, int(payload.get("total_page") or 1)


def fetch_disclosures(session, corp_code: str | None, bgn_de: str, end_de: str, pblntf_ty: str, max_pages: int = 10,
                      corp_cls: str | None = None) -> list[dict]:
    """corp_code 없이 corp_cls(Y=유가증권, K=코스닥)를 주면 시장 전체 공시 (기간은 3개월 이내)."""
    rows, page = [], 1
    while page <= max_pages:
        params = {
            "crtfc_key": env("DART_API_KEY", required=True),
            **({"corp_code": corp_code} if corp_code else {"corp_cls": corp_cls or "Y"}),
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


# ---------- 지분공시 정형 응답 (대량보유상황보고 majorstock.json · 최대주주 현황 hyslrSttus.json) ----------
def _check_status(payload: dict) -> bool:
    """013(조회된 데이터 없음)이면 False, 다른 오류면 예외."""
    status = payload.get("status")
    if status == "013":
        return False
    if status != "000":
        raise RuntimeError(f"[dart] {status} {STATUS.get(status, payload.get('message', ''))}")
    return True


def _date(value) -> str | None:
    d = re.sub(r"\D", "", value or "")
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else None


def parse_major_stock(payload: dict) -> list[dict]:
    """주식등의 대량보유상황보고(5% 룰) 보고 한 건 = 한 행. 지분율은 % 숫자, 읽지 못하면 None."""
    if not _check_status(payload):
        return []
    rows = []
    for it in payload.get("list", []):
        no = (it.get("rcept_no") or "").strip()
        if not no:
            continue
        rows.append(
            {
                "rcept_no": no,
                "rcept_dt": _date(it.get("rcept_dt")),
                "repror": (it.get("repror") or "").strip(),
                "report_tp": (it.get("report_tp") or "").strip() or None,
                "report_resn": (it.get("report_resn") or "").strip() or None,
                "stkqy": to_decimal(it.get("stkqy")),
                "stkrt": to_decimal(it.get("stkrt")),
                "stkrt_irds": to_decimal(it.get("stkrt_irds")),
                "ctr_stkrt": to_decimal(it.get("ctr_stkrt")),
                "raw": it,
            }
        )
    return rows


def fetch_major_stock(session, corp_code: str) -> list[dict]:
    params = {"crtfc_key": env("DART_API_KEY", required=True), "corp_code": corp_code}
    return parse_major_stock(get_json(session, "dart", f"{BASE}/majorstock.json", params=params, limiter=_limiter))


def parse_largest_holder(payload: dict, fiscal_year: int, report_code: str) -> list[dict]:
    """최대주주 및 특수관계인 현황. 기말 지분율(trmend_posesn_stock_qota_rt)을 stake_rt 로 둡니다."""
    if not _check_status(payload):
        return []
    rows = []
    for it in payload.get("list", []):
        name = (it.get("nm") or "").strip()
        if not name:
            continue
        rows.append(
            {
                "fiscal_year": fiscal_year,
                "report_code": report_code,
                "holder": name,
                "relate": (it.get("relate") or "").strip() or None,
                "stock_kind": (it.get("stock_knd") or "").strip() or "",
                "end_qty": to_decimal(it.get("trmend_posesn_stock_co")),
                "stake_rt": to_decimal(it.get("trmend_posesn_stock_qota_rt")),
                "begin_rt": to_decimal(it.get("bsis_posesn_stock_qota_rt")),
                "settle_dt": _date(it.get("stlm_dt")),
                "rcept_no": (it.get("rcept_no") or "").strip() or None,
                "raw": it,
            }
        )
    return rows


def fetch_largest_holder(session, corp_code: str, fiscal_year: int, report_code: str = REPORT_ANNUAL) -> list[dict]:
    params = {"crtfc_key": env("DART_API_KEY", required=True), "corp_code": corp_code,
              "bsns_year": str(fiscal_year), "reprt_code": report_code}
    return parse_largest_holder(get_json(session, "dart", f"{BASE}/hyslrSttus.json", params=params, limiter=_limiter),
                                fiscal_year, report_code)


# ---------- 감가상각·이자비용 항목 매핑 (#36): 전체 재무제표(fnlttSinglAcntAll)의 현금흐름표·손익계산서에서 항목을 골라냄 ----------
ITEM_SJ = ("IS", "CIS", "CF")


def fin_item(sj_div: str, account_id: str, account_nm: str) -> str | None:
    """계정 → 항목 이름. dep_amort 감가상각비 및 무형자산상각비(합산 계정) / depreciation 감가상각비 / rou_depreciation 사용권자산상각비 / amortization 무형자산상각비 /
    interest_expense 이자비용 / finance_costs 금융비용(이자 외 외환손실 등 포함) / interest_paid 이자지급(현금흐름).
    표준계정 ID 를 우선하고, 없으면 계정명 변형으로 찾습니다. 상각후원가 금융자산처럼 이름만 비슷한 계정은 제외."""
    if sj_div not in ITEM_SJ:
        return None
    a, n = account_id or "", re.sub(r"\s+", "", account_nm or "")
    if a.endswith("DepreciationAndAmortisationExpense") or n in ("감가상각비및무형자산상각비", "감가상각비와무형자산상각비"):
        return "dep_amort"
    if "DepreciationRightofuse" in a or ("사용권자산" in n and "상각" in n and "취득" not in n and "처분" not in n):
        return "rou_depreciation"
    if a.endswith("AdjustmentsForDepreciationExpense") or re.match(r"^(유형자산)?감가상각비(에대한조정|\(유형자산\))?$", n) \
            or n in ("유형자산상각비", "유형자산감가상각비"):
        return "depreciation"
    if a.endswith("AdjustmentsForAmortisationExpense") or n.startswith("무형자산상각비"):
        return "amortization"
    if re.search(r"(^|_)(AdjustmentsForInterestExpenses?|InterestExpense|InterestExpenseFinanceExpense)$", a) or n in ("이자비용", "이자비용(금융비용)", "이자비용조정"):
        return "interest_expense"
    if a == "ifrs-full_FinanceCosts" or n in ("금융비용", "금융원가"):
        return "finance_costs"
    if a.startswith("ifrs-full_InterestPaid") or n in ("이자의지급", "이자지급", "이자지급(영업)"):
        return "interest_paid"
    return None


def extract_fin_items(rows: list[dict]) -> list[dict]:
    """parse_financials 결과 중 위 항목에 해당하고 금액이 있는 행만 (같은 항목이 IS·CF 에 중복돼도 둘 다 둠)."""
    out = []
    for r in rows:
        item = fin_item(r["sj_div"], r["account_id"], r["account_nm"])
        if item and r["amount"] is not None:
            out.append({"fiscal_year": r["fiscal_year"], "report_code": r["report_code"], "fs_div": r["fs_div"], "item": item,
                        "sj_div": r["sj_div"], "account_id": r["account_id"], "account_nm": r["account_nm"],
                        "amount": r["amount"], "rcept_no": r["rcept_no"]})
    return out
