"""분기 단독 값 도출 (순수 함수, DB 불필요).

- DART: 1Q=1분기 누적, 2Q=반기누적−1Q누적, 3Q=3분기누적−반기누적, 4Q=사업보고서−3분기누적.
  분기말은 12월 결산 가정(3/31, 6/30, 9/30, 12/31).
- SEC: Q1~Q3 는 10-Q 의 3개월 값, Q4 = 연간(10-K) − (Q1+Q2+Q3).
"""
from __future__ import annotations

from datetime import date

FLOW_IDS = {
    "revenue": ["ifrs-full_Revenue", "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"],
    "operating_income": ["dart_OperatingIncomeLoss", "OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"],
    "net_income": ["ifrs-full_ProfitLoss", "NetIncomeLoss", "ProfitLoss"],
}
STOCK_IDS = {
    "assets": ["ifrs-full_Assets", "Assets"],
    "liabilities": ["ifrs-full_Liabilities", "Liabilities"],
    "equity": ["ifrs-full_Equity", "StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
}
DART_CODES = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


def _d(x):
    return date.fromisoformat(x) if isinstance(x, str) else x


def _index(facts):
    idx = {}
    for f in facts:
        idx.setdefault((f["fiscal_year"], f["report_code"], f["account_id"]), f)
    return idx


def _pick(idx, year, code, ids, field="amount"):
    for i in ids:
        f = idx.get((year, code, i))
        if f is not None and f.get(field) is not None:
            return f[field], f
    return None, None


def _row(year, q, period_end, metric, amount, currency):
    return {"period_end": period_end, "fiscal_year": year, "fiscal_q": q, "metric": metric,
            "amount": amount, "currency": currency}


def _fill_liabilities(rows):
    have = {(r["period_end"], r["metric"]): r for r in rows}
    for (pe, metric), r in list(have.items()):
        if metric != "assets" or (pe, "liabilities") in have:
            continue
        eq = have.get((pe, "equity"))
        if eq is not None:
            rows.append(_row(r["fiscal_year"], r["fiscal_q"], pe, "liabilities", r["amount"] - eq["amount"], r["currency"]))
    return rows


def derive_dart(facts: list[dict]) -> list[dict]:
    idx = _index(facts)
    out = []
    for y in sorted({f["fiscal_year"] for f in facts}):
        for metric, ids in FLOW_IDS.items():
            ytd, cur = {}, "KRW"
            for q, code in DART_CODES.items():
                field = "amount" if q == 4 else "amount_ytd"
                v, f = _pick(idx, y, code, ids, field)
                if v is None and q == 1:
                    v, f = _pick(idx, y, code, ids, "amount")
                ytd[q] = v
                if f is not None and f.get("currency"):
                    cur = f["currency"]
            for q in (1, 2, 3, 4):
                if ytd[q] is None or (q > 1 and ytd[q - 1] is None):
                    continue
                val = ytd[q] if q == 1 else ytd[q] - ytd[q - 1]
                out.append(_row(y, q, date(y, *QUARTER_END[q]), metric, val, cur))
        for q, code in DART_CODES.items():
            for metric, ids in STOCK_IDS.items():
                v, f = _pick(idx, y, code, ids)
                if v is not None:
                    out.append(_row(y, q, date(y, *QUARTER_END[q]), metric, v, (f or {}).get("currency") or "KRW"))
    return list({(r["period_end"], r["metric"]): r for r in _fill_liabilities(out)}.values())


def derive_sec(facts: list[dict]) -> list[dict]:
    idx = _index(facts)
    out = []
    for y in sorted({f["fiscal_year"] for f in facts}):
        for metric, ids in FLOW_IDS.items():
            qs = {}
            for q in (1, 2, 3):
                v, f = _pick(idx, y, f"Q{q}", ids)
                if v is not None and f.get("period_end"):
                    qs[q] = (v, f)
                    out.append(_row(y, q, _d(f["period_end"]), metric, v, f.get("currency")))
            fy, ffy = _pick(idx, y, "FY", ids)
            if fy is not None and len(qs) == 3 and ffy.get("period_end"):
                out.append(_row(y, 4, _d(ffy["period_end"]), metric, fy - sum(v for v, _ in qs.values()), ffy.get("currency")))
        for metric, ids in STOCK_IDS.items():
            for q in (1, 2, 3):
                v, f = _pick(idx, y, f"Q{q}", ids)
                if v is not None and f.get("period_end"):
                    out.append(_row(y, q, _d(f["period_end"]), metric, v, f.get("currency")))
            v, f = _pick(idx, y, "FY", ids)
            if v is not None and f.get("period_end"):
                out.append(_row(y, 4, _d(f["period_end"]), metric, v, f.get("currency")))
    return list({(r["period_end"], r["metric"]): r for r in _fill_liabilities(out)}.values())


def ttm_gaps(rows: list[dict]) -> list[str]:
    """최근 4개 분기가 연속하지 않은 흐름 지표 이름 목록 (rows 가 비어 있으면 빈 목록)."""
    if not any(r["metric"] in FLOW_IDS for r in rows):  # 분기 흐름 데이터가 없는 종목(6-K 제출 외국 기업 등)은 대상 아님
        return []
    bad = []
    for metric in FLOW_IDS:
        ends = sorted((r["period_end"] for r in rows if r["metric"] == metric), reverse=True)[:4]
        if len(ends) < 4 or (ends[0] - ends[-1]).days > 290:
            bad.append(metric)
    return bad
