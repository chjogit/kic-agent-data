"""OpenDART 정기보고서 주요정보(DS002) 중 차입 조건 + 전체 재무제표의 매출채권.

출처: 금융감독원 OpenDART (https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS002), 키 DART_API_KEY,
      하루 20,000회 한도(키당). 2015년 이후 사업·반기·분기보고서 기준.

엔드포인트 (공식 개발가이드에서 이름·응답 필드 확인, 2026-10-27)
- cprndNrdmpBlce.json            회사채 미상환 잔액 (잔여만기 1년 이하 ~ 10년 초과)
- entrprsBilScritsNrdmpBlce.json 기업어음증권(CP) 미상환 잔액 (10일 이하 ~ 3년 초과)
- srtpdPsndbtNrdmpBlce.json      단기사채 미상환 잔액 (10일 이하 ~ 1년 이하, 발행·잔여 한도)
- detScritsIsuAcmslt.json        채무증권 발행실적 (발행일·권면총액·이자율·신용등급·만기일·상환여부)
- fnlttSinglAcntAll.json         단일회사 전체 재무제표 (다중회사 주요계정 fnlttMultiAcnt 에는 매출채권이 없음)

※ 미상환 잔액 금액은 보고서 표의 숫자를 그대로 주며 단위를 알려 주지 않습니다(회사마다 원·백만원 등).
   그래서 금액은 amount_raw 로만 저장하고, 단위가 필요 없는 만기 비중(1년 이하 비중 등)을 뷰에서 계산합니다.
"""
from __future__ import annotations

import re
from datetime import date

from ..config import env
from ..http_util import RateLimiter, get_json
from ..util import to_decimal
from .dart import BASE, STATUS

_limiter = RateLimiter(0.15)

# kind → (엔드포인트, [(응답 필드, 만기 구간 이름)])
BALANCE_APIS = {
    "bond": ("cprndNrdmpBlce", [
        ("yy1_below", "1y"), ("yy1_excess_yy2_below", "1-2y"), ("yy2_excess_yy3_below", "2-3y"),
        ("yy3_excess_yy4_below", "3-4y"), ("yy4_excess_yy5_below", "4-5y"), ("yy5_excess_yy10_below", "5-10y"),
        ("yy10_excess", "10y+"), ("sm", "total")]),
    "cp": ("entrprsBilScritsNrdmpBlce", [
        ("de10_below", "10d"), ("de10_excess_de30_below", "10-30d"), ("de30_excess_de90_below", "30-90d"),
        ("de90_excess_de180_below", "90-180d"), ("de180_excess_yy1_below", "180d-1y"),
        ("yy1_excess_yy2_below", "1-2y"), ("yy2_excess_yy3_below", "2-3y"), ("yy3_excess", "3y+"), ("sm", "total")]),
    "stb": ("srtpdPsndbtNrdmpBlce", [
        ("de10_below", "10d"), ("de10_excess_de30_below", "10-30d"), ("de30_excess_de90_below", "30-90d"),
        ("de90_excess_de180_below", "90-180d"), ("de180_excess_yy1_below", "180d-1y"), ("sm", "total"),
        ("isu_lmt", "limit"), ("remndr_lmt", "limit_left")]),
}
ISSUE_API = "detScritsIsuAcmslt"
# 1년 안에 만기가 오는 구간 (뷰 core.v_debt_maturity 와 같게 유지)
WITHIN_1Y = {"1y", "10d", "10-30d", "30-90d", "90-180d", "180d-1y"}

# 매출채권 계정: 순수 매출채권을 먼저, 없으면 '매출채권 및 기타채권' (어느 것을 썼는지 account_nm 으로 남김)
RECEIVABLE_NAMES = (
    ("매출채권", "단기매출채권", "매출채권(순액)", "유동매출채권"),
    ("매출채권및기타채권", "매출채권및기타유동채권", "단기매출채권및기타채권", "매출채권및기타수취채권"),
)


def _check(payload: dict) -> bool:
    """정상이면 True, 자료 없음(013)이면 False, 그 밖의 상태는 오류."""
    status = payload.get("status")
    if status == "013":
        return False
    if status != "000":
        raise RuntimeError(f"[dart] {status} {STATUS.get(status, payload.get('message', ''))}")
    return True


DATE_RE = re.compile(r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")


def _date(text) -> date | None:
    """'20260310', '2026-03-10', '2026.03.10', '2026년 3월 10일' → date. 그 밖(빈칸, '-')은 None."""
    t = str(text or "").strip()
    m = DATE_RE.search(t)
    if m:
        y, mo, d = (int(x) for x in m.groups())
    elif re.fullmatch(r"\d{8}", t):
        y, mo, d = int(t[:4]), int(t[4:6]), int(t[6:])
    else:
        return None
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _text(value) -> str | None:
    t = re.sub(r"\s+", " ", str(value or "")).strip()
    return None if t in ("", "-") else t


def parse_balance(payload: dict, kind: str) -> list[dict]:
    """미상환 잔액 응답 → [{label, bucket, amount_raw, stlm_dt, rcept_no}] (행 이름은 원문 그대로, 예: '공모|합계')."""
    if not _check(payload):
        return []
    fields = BALANCE_APIS[kind][1]
    out = []
    for it in payload.get("list", []):
        label = "|".join(x for x in ((it.get("remndr_exprtn1") or "").strip(),
                                     (it.get("remndr_exprtn2") or "").strip()) if x)
        for field, bucket in fields:
            amount = to_decimal(it.get(field))
            if amount is None:
                continue
            out.append({"label": label, "bucket": bucket, "amount_raw": amount,
                        "stlm_dt": _date(it.get("stlm_dt")), "rcept_no": it.get("rcept_no")})
    return out


def parse_issues(payload: dict) -> list[dict]:
    """채무증권 발행실적 → 발행 건별 행 (이자율 '3.25' → 3.25, 비어 있으면 None)."""
    if not _check(payload):
        return []
    out = []
    for it in payload.get("list", []):
        issuer = _text(it.get("isu_cmpny")) or ""
        if re.sub(r"\s", "", issuer) in ("합계", "총계", "계"):   # 원문 표의 합계 행
            continue
        out.append({
            "seq": len(out),
            "issuer": issuer,
            "kind_nm": _text(it.get("scrits_knd_nm")),
            "method": _text(it.get("isu_mth_nm")),
            "issue_date": _date(it.get("isu_de")),
            "face_amount_raw": to_decimal(it.get("facvalu_totamt")),
            "rate": to_decimal((it.get("intrt") or "").replace("%", "")),
            "rating": _text(it.get("evl_grad_instt")),
            "maturity_date": _date(it.get("mtd")),
            "repaid": _text(it.get("repy_at")),
            "underwriter": _text(it.get("mngt_cmpny")),
            "stlm_dt": _date(it.get("stlm_dt")),
            "rcept_no": it.get("rcept_no"),
            "_raw_dates": (it.get("isu_de"), it.get("mtd")),
        })
    return out


def pick_receivable(rows: list[dict]) -> dict | None:
    """전체 재무제표(재무상태표) 행에서 매출채권 하나. 연결(CFS) 응답만 넘깁니다."""
    bs = [r for r in rows if r.get("sj_div") == "BS" and r.get("amount") is not None]
    for names in RECEIVABLE_NAMES:
        for r in bs:
            if (r.get("account_nm") or "").replace(" ", "") in names:
                return r
    return None


# 재무상태표 차입 계정(총차입금 용). 이름에서 공백을 빼고 맞춥니다. 리스부채는 넣지 않습니다.
BORROW_RE = re.compile(r"차입금|차입부채|사채")
BORROW_NOT_RE = re.compile(r"할인|할증|상환|증가|감소|발행차금|미지급|평가|손익|손실|이익|조정|파생|전환권|교환권|"
                           r"신주인수권|대여|채권|예수")


def borrow_kind(account_nm: str) -> str | None:
    """차입 계정 구분: short(단기) · current_lt(유동성 장기) · long(장기) · bond(사채) · bank(금융업 차입부채) ·
    mixed(차입금·사채 합본, 유동/비유동 모름). 차입 계정이 아니면 None."""
    nm = (account_nm or "").replace(" ", "")
    if not BORROW_RE.search(nm) or BORROW_NOT_RE.search(nm):
        return None
    if "차입부채" in nm:
        return "bank"
    if nm.startswith("유동성") or (nm.startswith("유동") and "장기" in nm) or "유동성대체" in nm \
            or ("유동성부분" in nm and "비유동성부분" not in nm):
        return "current_lt"
    if nm.startswith(("단기", "유동")):
        return "short"
    if "차입금" not in nm:
        return "bond"
    if nm.startswith(("장기", "비유동")):
        return "long"
    return "mixed"


def pick_borrowings(rows: list[dict]) -> list[dict]:
    """전체 재무제표에서 재무상태표 차입 계정 행들(총차입금 = 합). 한 응답(연결 또는 별도)만 넘깁니다.
    응답은 표 순서(자산 → 자산총계 → 부채 → 부채총계)라, 자산총계와 부채총계 사이 행만 봅니다
    (자산 쪽 '전환사채' 투자 같은 행을 차입으로 잡지 않게). 두 행을 못 찾으면 전체에서 고릅니다."""
    bs = [r for r in rows if r.get("sj_div") == "BS"]
    names = [(r.get("account_nm") or "").replace(" ", "") for r in bs]
    if "자산총계" in names and "부채총계" in names and names.index("자산총계") < names.index("부채총계"):
        bs = bs[names.index("자산총계") + 1:names.index("부채총계")]
    out, seen = [], set()
    for r in bs:
        if r.get("sj_div") != "BS" or r.get("amount") is None or not borrow_kind(r.get("account_nm")):
            continue
        nm, n = r.get("account_nm") or "", 1
        while (r.get("account_id") or "", nm) in seen:  # 같은 ID·이름이 두 번(유동·비유동) 나오면 순번을 붙여 둘 다 남김
            n += 1
            nm = f"{r.get('account_nm')}#{n}"
        seen.add((r.get("account_id") or "", nm))
        out.append({**r, "account_nm": nm})
    return out


def _params(corp_code: str, year: int, reprt_code: str) -> dict:
    return {"crtfc_key": env("DART_API_KEY", required=True), "corp_code": corp_code,
            "bsns_year": str(year), "reprt_code": reprt_code}


def fetch_balance(session, kind: str, corp_code: str, year: int, reprt_code: str) -> list[dict]:
    api = BALANCE_APIS[kind][0]
    payload = get_json(session, "dart", f"{BASE}/{api}.json", params=_params(corp_code, year, reprt_code),
                       limiter=_limiter)
    return parse_balance(payload, kind)


def fetch_issues(session, corp_code: str, year: int, reprt_code: str) -> list[dict]:
    payload = get_json(session, "dart", f"{BASE}/{ISSUE_API}.json", params=_params(corp_code, year, reprt_code),
                       limiter=_limiter)
    return parse_issues(payload)
