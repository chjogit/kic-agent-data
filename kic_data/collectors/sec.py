"""SEC EDGAR: 티커→CIK, 연간 재무(companyfacts). 키 없이 User-Agent(이름+이메일)만 필요."""
from __future__ import annotations

from datetime import date

from ..config import env
from ..http_util import RateLimiter, get_json

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_limiter = RateLimiter(0.2)  # 한도 10 req/s 보다 훨씬 보수적

# 가져올 us-gaap 태그: (태그, 단위)
TAGS = [
    ("Revenues", "USD"),
    ("RevenueFromContractWithCustomerExcludingAssessedTax", "USD"),
    ("NetIncomeLoss", "USD"),
    ("OperatingIncomeLoss", "USD"),
    ("Assets", "USD"),
    ("Liabilities", "USD"),
    ("StockholdersEquity", "USD"),
    ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "USD"),
    ("ProfitLoss", "USD"),
]


# 20-F/40-F 제출 외국 기업(ASML, TSM 등)은 IFRS 항목(ifrs-full)으로 공시. 통화는 EUR, TWD 등 제각각.
IFRS_TAGS = ["Revenue", "ProfitLossFromOperatingActivities", "ProfitLoss", "Assets", "Liabilities", "Equity"]
ANNUAL_FORMS = ("10-K", "20-F", "40-F")


def _pick_currency_unit(units: dict) -> str | None:
    """주식수·비율 단위를 제외하고 항목 수가 가장 많은 통화 단위."""
    cands = {u: v for u, v in units.items() if u.lower() not in ("shares", "pure") and "/" not in u}
    return max(cands, key=lambda u: len(cands[u])) if cands else None


def user_agent() -> str:
    return env("SEC_USER_AGENT", required=True)


def parse_ticker_map(payload: dict) -> dict[str, str]:
    """{ 'AAPL': '0000320193', ... }"""
    return {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in payload.values()}


def fetch_ticker_map(session) -> dict[str, str]:
    payload = get_json(session, "sec", TICKERS_URL, headers={"User-Agent": user_agent()}, limiter=_limiter)
    return parse_ticker_map(payload)


def parse_annual_facts(payload: dict, min_year: int = 2015) -> list[dict]:
    """10-K 연간 값만 추출 → core.financial_fact 행."""
    facts = payload.get("facts", {})
    gaap = facts.get("us-gaap", {})
    ifrs = facts.get("ifrs-full", {})
    sources = []
    for tag, unit in TAGS:
        units = gaap.get(tag, {}).get("units", {})
        # 기본은 USD, 외국 기업(ASML 등)은 EUR 같은 보고통화를 자동 선택
        sources.append((tag, "us-gaap", tag, unit if unit in units else _pick_currency_unit(units), gaap))
    for tag in IFRS_TAGS:
        sources.append((f"ifrs-full_{tag}", "ifrs-full", tag, _pick_currency_unit(ifrs.get(tag, {}).get("units", {})), ifrs))
    rows = []
    for out_id, _, tag, unit, taxonomy in sources:
        if unit is None:
            continue
        for f in taxonomy.get(tag, {}).get("units", {}).get(unit, []):
            if f.get("form") not in ANNUAL_FORMS or f.get("fp") != "FY":
                continue
            year = f.get("fy")
            if not year or year < min_year:
                continue
            if f.get("start") and f.get("end"):  # 1년 기간 값만 (비교 대상 분기·다년 값 제외)
                try:
                    days = (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days
                except ValueError:
                    days = 365
                if not 300 <= days <= 400:
                    continue
            rows.append(
                {
                    "fiscal_year": year,
                    "report_code": "FY",
                    "fs_div": "",
                    "sj_div": "",
                    "account_id": out_id,
                    "account_nm": out_id,
                    "amount": f.get("val"),
                    "currency": unit,
                    "rcept_no": f.get("accn"),
                    "_end": f.get("end") or "",
                }
            )
    # 같은 (연도, 태그)에 전기 비교값이 섞여 있어, 기간 종료일이 가장 늦은 값만 유지
    best: dict = {}
    for r in rows:
        key = (r["fiscal_year"], r["account_id"])
        if key not in best or r["_end"] >= best[key]["_end"]:
            best[key] = r
    out = []
    for r in best.values():
        r = dict(r)
        r["period_end"] = r.pop("_end") or None
        out.append(r)
    return out


def fetch_annual_facts(session, cik: str, min_year: int = 2015) -> list[dict]:
    payload = get_json(session, "sec", FACTS_URL.format(cik=cik), headers={"User-Agent": user_agent()}, limiter=_limiter)
    return parse_annual_facts(payload, min_year)


def parse_submissions(payload: dict) -> dict:
    return {"sic": str(payload.get("sic") or ""), "sic_desc": payload.get("sicDescription") or ""}


def fetch_submissions(session, cik: str) -> dict:
    payload = get_json(session, "sec", SUBMISSIONS_URL.format(cik=cik), headers={"User-Agent": user_agent()}, limiter=_limiter)
    return parse_submissions(payload)


FLOW_TAGS = {"Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "NetIncomeLoss", "OperatingIncomeLoss", "ProfitLoss"}


def parse_quarterly_facts(payload: dict, min_year: int = 2020) -> list[dict]:
    """10-Q 의 Q1~Q3. 흐름 항목은 3개월(80~100일) 값만, 잔액 항목은 분기말 값."""
    gaap = payload.get("facts", {}).get("us-gaap", {})
    best: dict = {}
    for tag, unit in TAGS:
        units = gaap.get(tag, {}).get("units", {})
        use = unit if unit in units else _pick_currency_unit(units)
        if use is None:
            continue
        for f in units[use]:
            if f.get("form") != "10-Q" or f.get("fp") not in ("Q1", "Q2", "Q3"):
                continue
            year = f.get("fy")
            if not year or year < min_year or not f.get("end"):
                continue
            if tag in FLOW_TAGS:
                if not f.get("start"):
                    continue
                try:
                    days = (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days
                except ValueError:
                    continue
                if not 80 <= days <= 100:
                    continue
            key = (year, f["fp"], tag)
            if key not in best or f["end"] >= best[key]["period_end"]:
                best[key] = {
                    "fiscal_year": year, "report_code": f["fp"], "fs_div": "", "sj_div": "",
                    "account_id": tag, "account_nm": tag, "amount": f.get("val"), "currency": use,
                    "rcept_no": f.get("accn"), "period_end": f["end"],
                }
    return list(best.values())


def fetch_quarterly_facts(session, cik: str, min_year: int = 2020) -> list[dict]:
    payload = get_json(session, "sec", FACTS_URL.format(cik=cik), headers={"User-Agent": user_agent()}, limiter=_limiter)
    return parse_quarterly_facts(payload, min_year)


# ---------- 제출 서류 목록 (submissions) ----------
FILING_FORMS = ("8-K", "10-K", "10-Q", "20-F", "6-K", "40-F")
# 8-K 항목 번호 → 사건 유형 (여러 항목이면 앞의 것 우선)
ITEM_EVENTS = [
    ("2.02", "earnings"),
    ("1.01", "material_agreement"),
    ("2.01", "acquisition_completed"),
    ("1.03", "bankruptcy"),
    ("2.05", "restructuring"),
    ("5.02", "officer_change"),
    ("1.05", "cyber_incident"),
    ("4.02", "financials_non_reliance"),
    ("3.02", "unregistered_sale"),
    ("7.01", "reg_fd"),
    ("8.01", "other_events"),
]


def classify_filing(form: str, items: str) -> str | None:
    if form == "8-K":
        have = {x.strip() for x in (items or "").split(",")}
        return next((ev for code, ev in ITEM_EVENTS if code in have), None)
    return {"10-K": "annual_report", "20-F": "annual_report", "40-F": "annual_report",
            "10-Q": "quarterly_report", "6-K": "foreign_report"}.get(form)


def parse_filings(payload: dict, cik: str, since: date, forms=FILING_FORMS) -> list[dict]:
    recent = payload.get("filings", {}).get("recent", {})
    name = payload.get("name", "")
    cols = {k: recent.get(k, []) for k in ("accessionNumber", "filingDate", "form", "primaryDocument", "items")}
    rows = []
    for i, acc in enumerate(cols["accessionNumber"]):
        form = cols["form"][i] if i < len(cols["form"]) else ""
        if form not in forms:
            continue
        d = date.fromisoformat(cols["filingDate"][i])
        if d < since:
            continue
        items = cols["items"][i] if i < len(cols["items"]) else ""
        doc = cols["primaryDocument"][i] if i < len(cols["primaryDocument"]) else ""
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{doc}" if doc else None
        rows.append(
            {
                "rcept_no": acc,
                "report_nm": f"{form} {items}".strip(),
                "rcept_dt": d.isoformat(),
                "filer": name,
                "category": form,
                "event_type": classify_filing(form, items),
                "url": url,
            }
        )
    return rows


def fetch_filings(session, cik: str, since: date, forms=FILING_FORMS) -> list[dict]:
    payload = get_json(session, "sec", SUBMISSIONS_URL.format(cik=cik), headers={"User-Agent": user_agent()}, limiter=_limiter)
    return parse_filings(payload, cik, since, forms)


# ADR 1주가 원주 몇 주인지. yfinance 시세가 ADR 가격이라 원주 수를 ADR 기준으로 환산해야 시가총액이 맞습니다.
ADR_RATIO = {"TSM": 5, "ASML": 1}


def parse_shares_outstanding(payload: dict, ticker: str | None = None):
    """companyfacts 의 dei:EntityCommonStockSharesOutstanding 중 가장 최근 값 → (주식수, 기준일) 또는 None."""
    units = payload.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding", {}).get("units", {})
    vals = [v for v in units.get("shares", []) if v.get("val") and v.get("end")]
    if not vals:
        return None
    last_end = max(v["end"] for v in vals)
    latest = [v for v in vals if v["end"] == last_end]
    last = max(latest, key=lambda v: v.get("filed", ""))
    shares = int(last["val"])
    ratio = ADR_RATIO.get((ticker or "").upper(), 1)
    return shares // ratio, date.fromisoformat(last_end)


def fetch_shares_outstanding(session, cik: str, ticker: str | None = None):
    payload = get_json(session, "sec", FACTS_URL.format(cik=cik), headers={"User-Agent": user_agent()}, limiter=_limiter)
    return parse_shares_outstanding(payload, ticker)
