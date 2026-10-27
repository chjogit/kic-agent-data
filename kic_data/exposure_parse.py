"""사업보고서 원문 표에서 기업 고유 거시 노출을 뽑습니다 (규칙 기반, LLM 없음).

뽑는 것
- fx_sens      : 환율 n% 변동 시 손익 영향 (통화별, 상승 시 기준, 원)  — 재무위험관리 주석의 민감도 표
- fx_asset / fx_liab : 외화 화폐성 자산·부채 (통화별, 원화 환산, 원)
- export_share : 수출 매출 비중 (%), 사업의 내용 '매출 실적'의 내수·수출 표에서 계산
- fx_derivative: 통화선도·통화스왑 등 외환 파생상품 사용 여부 (1/0) + 근거 문장

원칙: 표에서 읽은 숫자와, 그 숫자로 한 단순 계산(비중)만 저장합니다. 못 찾으면 저장하지 않습니다(추정 없음).
모든 행에 근거(표 앞 문맥, 파일, 표 번호)를 함께 남깁니다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .collectors.dart_doc import Table

CCY = ("USD", "EUR", "JPY", "CNY", "GBP", "HKD", "TWD", "VND", "INR", "AUD", "CAD", "SGD", "CHF", "MXN", "BRL",
       "PLN", "IDR", "THB", "HUF", "CZK", "RUB")
CCY_KO = {"미국달러": "USD", "미달러": "USD", "달러": "USD", "유로": "EUR", "엔": "JPY", "일본엔": "JPY", "엔화": "JPY",
          "위안": "CNY", "위안화": "CNY", "중국위안": "CNY", "파운드": "GBP"}
UNIT_KRW = {"원": 1, "천원": 1e3, "백만원": 1e6, "십억원": 1e9, "억원": 1e8, "조원": 1e12}
NUM_RE = re.compile(r"^[△▲(\-−]?\s*\(?\s*[\d,]+(?:\.\d+)?\s*\)?$")
PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:p\s*)?(?:변동|상승|하락|증가|감소|절상|절하|강세|약세|만큼)")
TOTAL_WORDS = ("합계", "총계", "소계", "계", "합 계", "총 계", "연결기준합계", "연결합계")
FX_DERIV_RE = re.compile(r"통화\s*선도|통화\s*스왑|통화\s*옵션|통화\s*이자율\s*스왑|선물환|외환\s*파생|NDF")


@dataclass
class Metric:
    metric: str
    currency: str
    value: float
    change_pct: float | None
    basis: str | None
    unit_raw: str | None
    evidence: str
    table_index: int
    file: str


def num(cell: str) -> float | None:
    """'1,234' '(1,234)' '△1,234' '-1,234' → 숫자. 숫자가 아니면 None."""
    c = (cell or "").replace(" ", "").replace("−", "-")
    if not c or not NUM_RE.match(c):
        return None
    neg = c.startswith(("△", "▲", "-", "(")) or c.endswith(")")
    digits = re.sub(r"[^\d.]", "", c)
    if not digits or digits == ".":
        return None
    v = float(digits)
    return -v if neg else v


def unit_factor(unit: str | None) -> float | None:
    if not unit:
        return None
    u = unit.replace(" ", "")
    for k in sorted(UNIT_KRW, key=len, reverse=True):
        if u.startswith(k) or u.endswith(k) or k in u.split(","):
            return UNIT_KRW[k]
    return None


def ccy_of(cell: str) -> str | None:
    c = (cell or "").strip().upper().replace(" ", "")
    for code in CCY:
        if c.startswith(code) or c == f"{code}/KRW":
            return code
    raw = (cell or "").replace(" ", "")
    for k, code in CCY_KO.items():
        if raw.startswith(k):
            return code
    return None


def _blob(t: Table, rows: int = 4) -> str:
    return t.context + " " + " ".join(" ".join(r) for r in t.rows[:rows])


def _basis(text: str) -> str | None:
    if re.search(r"법인세\s*(비용)?\s*차감\s*전|세전", text):
        return "세전이익"
    if re.search(r"세후|당기\s*순\s*(이익|손익)|당기\s*손익", text):
        return "당기손익"
    if "자본" in text:
        return "자본"
    return None


def _pct(text: str) -> float | None:
    m = PCT_RE.search(text)
    return float(m.group(1)) if m else None


def _evidence(t: Table) -> str:
    return re.sub(r"\s+", " ", t.context)[-300:]


# ---------------------------------------------------------------- 환율 민감도

def _is_sensitivity(t: Table) -> bool:
    b = _blob(t, 6)
    return "환율" in b and bool(re.search(r"민감|변동\s*시|상승\s*시|하락\s*시|상승시|하락시|변동시", b)) \
        and (_pct(b) is not None) and any(ccy_of(c) for r in t.rows for c in r[:1] + t.rows[0])


def sensitivity(t: Table) -> list[Metric]:
    """민감도 표에서 통화별 '상승 시' 손익 영향(당기)을 원 단위로."""
    f = unit_factor(t.unit)
    if f is None:
        return []
    b = _blob(t, 6)
    pct, basis = _pct(b), _basis(b)
    out: dict[str, float] = {}
    # 배치 A: 행 = 통화, 열 = 상승/하락 (당기 먼저)
    for r in t.rows:
        code = ccy_of(r[0]) if r else None
        if not code or code in out:
            continue
        vals = [num(c) for c in r[1:]]
        vals = [v for v in vals if v is not None]
        if vals:
            out[code] = vals[0]
    # 배치 B: 머리행 = 통화, 행 = '상승 시 증가/감소'
    if not out:
        header = next((r for r in t.rows if sum(bool(ccy_of(c)) for c in r) >= 1), None)
        if header:
            cols = {i: ccy_of(c) for i, c in enumerate(header) if ccy_of(c)}
            for r in t.rows:
                label = r[0] if r else ""
                if "상승" not in label and "증가" not in label:
                    continue
                if "하락" in label:
                    continue
                sign = -1 if "감소" in label else 1
                for i, code in cols.items():
                    if i < len(r) and code not in out:
                        v = num(r[i])
                        if v is not None:
                            out[code] = sign * abs(v) if "감소" in label or "증가" in label else v
    ev = _evidence(t)
    return [Metric("fx_sens", code, v * f, pct, basis, t.unit, ev, t.index, t.file) for code, v in out.items()]


# ---------------------------------------------------------------- 외화 자산·부채

def _is_fx_position(t: Table) -> bool:
    head = " ".join(" ".join(r) for r in t.rows[:3]).replace(" ", "")
    return "자산" in head and "부채" in head and "외화" in _blob(t, 2) \
        and any(ccy_of(r[0]) for r in t.rows if r) and not _is_sensitivity(t)


def fx_position(t: Table) -> list[Metric]:
    f = unit_factor(t.unit)
    if f is None:
        return []
    head_rows = t.rows[:3]
    ncol = max(len(r) for r in t.rows)
    a_cols, l_cols, krw_cols = [], [], set()
    for r in head_rows:
        for i, c in enumerate(r):
            cc = c.replace(" ", "")
            if "자산" in cc:
                a_cols.append(i)
            if "부채" in cc:
                l_cols.append(i)
            if "원화" in cc or "환산" in cc:
                krw_cols.add(i)
    if not a_cols or not l_cols:
        return []

    def pick(cols):
        prefer = [i for i in cols if i in krw_cols]
        return (prefer or cols)[0] if (prefer or cols) else None

    ai, li = pick(sorted(set(a_cols))), pick(sorted(set(l_cols)))
    if ai is None or li is None or ai >= ncol or li >= ncol:
        return []
    out = []
    ev = _evidence(t)
    for r in t.rows[1:]:
        code = ccy_of(r[0]) if r else None
        if not code:
            continue
        a = num(r[ai]) if ai < len(r) else None
        lv = num(r[li]) if li < len(r) else None
        if a is not None:
            out.append(Metric("fx_asset", code, a * f, None, None, t.unit, ev, t.index, t.file))
        if lv is not None:
            out.append(Metric("fx_liab", code, lv * f, None, None, t.unit, ev, t.index, t.file))
    return out


# ---------------------------------------------------------------- 수출 비중

def _cells(r: list[str]) -> list[str]:
    return [c.replace(" ", "") for c in r]


def _is_export(t: Table) -> bool:
    flat = {c for r in t.rows for c in _cells(r)}
    return "내수" in flat and "수출" in flat


def export_share(t: Table, revenue_krw: float | None) -> list[Metric]:
    """내수·수출 표에서 당기 수출 비중. 합계 행과 맞고, 회사 매출 규모와 맞는 표만 받습니다."""
    dom = exp = 0.0
    total = None
    n_dom = n_exp = 0
    for r in t.rows:
        cs = _cells(r)
        if not cs:
            continue
        labels = [c for c in cs if num(c) is None]
        vals = [num(c) for c in cs if num(c) is not None]
        if not vals:
            continue
        if any(c in TOTAL_WORDS for c in labels) and "내수" not in labels and "수출" not in labels:
            total = vals[0]
            continue
        if any(c in ("소계", "계", "합계") for c in labels):
            continue  # 부문별 소계: 이중 합산 방지
        if "내수" in labels:
            dom += vals[0]
            n_dom += 1
        elif "수출" in labels:
            exp += vals[0]
            n_exp += 1
    s = dom + exp
    if not n_dom or not n_exp or s <= 0:
        return []
    if total is not None and abs(total - s) > 0.03 * abs(total):
        return []  # 합계와 안 맞음 (표 구조를 잘못 읽었을 수 있음)
    f = unit_factor(t.unit)
    if revenue_krw:
        cands = [f] if f else [1, 1e3, 1e6, 1e8]
        if not any(0.3 <= s * u / revenue_krw <= 1.6 for u in cands):
            return []  # 회사 전체가 아닌 일부 부문·종속회사 표
    elif not f:
        return []
    return [Metric("export_share", "ALL", exp / s * 100, None, None, t.unit, _evidence(t), t.index, t.file)]


# ---------------------------------------------------------------- 외환 파생상품

def fx_derivative(tables: list[Table]) -> Metric | None:
    for t in tables:
        b = _blob(t, 8)
        m = FX_DERIV_RE.search(b)
        if m and re.search(r"환율|외화|외환|환위험|통화", b):
            return Metric("fx_derivative", "ALL", 1.0, None, None, None, _evidence(t) or b[:300], t.index, t.file)
    return None


# ---------------------------------------------------------------- 묶음

def extract(files: dict[str, str], revenue_krw: float | None = None) -> dict:
    """원문 파일들 → {'metrics': [Metric], 'risk_text': 환위험 관리 서술(RAG용), 'found': {...}}.
    본문(접수번호.xml) → 첨부(감사보고서) 순으로 보고, 지표마다 처음 찾은 표를 씁니다."""
    from .collectors.dart_doc import tables_from_xml

    metrics: list[Metric] = []
    got: set[str] = set()
    risk_text = ""
    all_tables: list[Table] = []
    for name, text in files.items():
        tables = tables_from_xml(text, name)
        all_tables += tables
        for t in tables:
            if "fx_sens" not in got and _is_sensitivity(t):
                ms = sensitivity(t)
                if ms:
                    metrics += ms
                    got.add("fx_sens")
                    risk_text = risk_text or t.context
            if "fx_pos" not in got and _is_fx_position(t):
                ms = fx_position(t)
                if ms:
                    metrics += ms
                    got.add("fx_pos")
            if "export" not in got and _is_export(t):
                ms = export_share(t, revenue_krw)
                if ms:
                    metrics += ms
                    got.add("export")
    d = fx_derivative(all_tables)
    if d:
        metrics.append(d)
        got.add("fx_derivative")
        risk_text = (risk_text + " " + d.evidence).strip()
    return {"metrics": metrics, "risk_text": risk_text[-900:], "found": sorted(got)}
