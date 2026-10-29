"""사업보고서 원문 표에서 기업 고유 거시 노출을 뽑습니다 (규칙 기반, LLM 없음).

뽑는 것
- fx_sens      : 환율 n% 변동 시 손익 영향 (통화별, 상승 시 기준, 원)  — 재무위험관리 주석의 민감도 표
- fx_asset / fx_liab : 외화 화폐성 자산·부채 (통화별, 원화 환산, 원)
- export_share : 수출 매출 비중 (%), 사업의 내용 '매출 실적'의 내수·수출 표에서 계산
- fx_derivative: 통화선도·통화스왑 등 외환 파생상품 사용 여부 (1/0) + 근거 문장
- ir_sens      : 이자율 n%p(또는 n bp) 상승 시 손익 영향 (원) — 이자율위험 주석의 민감도 표 (변동금리 차입 노출)
- price_text   : 사업의 내용 '가격변동추이·가격변동원인' 문단 발췌 (판가 전가력 판단 근거, 숫자로 바꾸지 않음)
- ir_sens_liab : (v2) 이자율 민감도 표의 부채 쪽 행(금융부채·차입금·사채·이자비용)만의 영향 (원). 에이전트가
                 |값| ÷ 변동폭 으로 변동금리부 부채 규모를 역산 (대용치)
- fx_hedge_acct: (v2) 환율 관련 위험회피회계 적용 여부 (1) + 근거 문장
- hedge_text   : (v2) 통화선도·스왑 계약 표의 앞 문맥과 첫 행 발췌 (계약 규모는 회사마다 표기·단위가 달라 숫자로 바꾸지 않음)

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
PARSER_VERSION = "v2"   # v2 (10-29): ir_sens_liab · fx_hedge_acct · hedge_text 추가 → 낮은 버전으로 읽은 원문은 다시 읽음
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

PRIOR_RE = re.compile(r"^전\s*기|^전기말|^전\s*년")
TOTAL_ROW_RE = re.compile(r"법인세\s*(비용)?\s*차감\s*전|세전\s*(순)?\s*(이익|손익)|당기\s*(순)?\s*(이익|손익)")
UP_RE = re.compile(r"상승|인상|절하|증가")


def _up_col(t: Table) -> int | None:
    """머리행에서 '상승(인상)' 열 중 첫째 열. 당기·상승이 앞에 오는 표가 대부분이지만 '하락'이 먼저인 표도 있어 확인."""
    for r in t.rows[:3]:
        for i, c in enumerate(r):
            cc = c.replace(" ", "")
            if i > 0 and UP_RE.search(cc) and "하락" not in cc and "인하" not in cc:
                return i
    return None


def _ccy_cell(r: list[str]) -> tuple[int, str] | None:
    """행의 앞 두 칸에서 통화를 찾습니다 ('당 기 | USD | …' 처럼 기간이 앞에 오는 표)."""
    for i, c in enumerate(r[:2]):
        code = ccy_of(c)
        if code:
            return i, code
    return None


def _is_sensitivity(t: Table) -> bool:
    b = _blob(t, 6)
    if not ("환율" in b and re.search(r"민감|변동\s*시|상승\s*시|하락\s*시|상승시|하락시|변동시|인상\s*시|인하\s*시", b)
            and _pct(b) is not None):
        return False
    has_ccy = any(ccy_of(c) for r in t.rows for c in r[:2] + t.rows[0])
    has_total = any(r and TOTAL_ROW_RE.search(r[0].replace(" ", "")) for r in t.rows)
    return has_ccy or has_total


def sensitivity(t: Table) -> list[Metric]:
    """민감도 표에서 통화별 '상승 시' 손익 영향(당기)을 원 단위로. 통화 구분이 없으면 currency='ALL'."""
    f = unit_factor(t.unit)
    if f is None:
        return []
    b = _blob(t, 6)
    pct, basis = _pct(b), _basis(b)
    out: dict[str, float] = {}
    up = _up_col(t)
    # 배치 A: 행 = 통화(앞 두 칸 중 하나), 열 = 상승/하락 (당기 먼저). 전기 행은 건너뜀
    for r in t.rows:
        hit = _ccy_cell(r) if r else None
        if not hit or PRIOR_RE.search(r[0].replace(" ", "")):
            continue
        i, code = hit
        if code in out:
            continue
        if up is not None and up > i and up < len(r) and num(r[up]) is not None:
            out[code] = num(r[up])
            continue
        vals = [v for v in (num(c) for c in r[i + 1:]) if v is not None]
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
    # 배치 C: 통화 구분 없이 손익 합계 한 행 ('세전순이익 | (6,410) | 6,410 | …')
    if not out:
        for r in t.rows:
            label = r[0].replace(" ", "") if r else ""
            if not TOTAL_ROW_RE.search(label) or "하락" in label or "감소" in label:
                continue
            v = num(r[up]) if up is not None and up < len(r) else None
            if v is None:
                vals = [x for x in (num(c) for c in r[1:]) if x is not None]
                v = vals[0] if vals else None
            if v is not None:
                out["ALL"] = v
                basis = _basis(label) or basis
                break
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


# ---------------------------------------------------------------- 이자율 민감도

BP_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(bp|BP|bps|베이시스\s*포인트|%\s*p|%p|%포인트|%)")
IR_ROW_RE = re.compile(r"법인세\s*(비용)?\s*차감\s*전|세전|당기\s*(순)?\s*(이익|손익)|이자\s*(비용|손익|수익)|순이익|손익")


def _ir_change(text: str) -> float | None:
    """'100bp' → 1.0, '1%p' → 1.0, '0.5%' → 0.5 (%p 단위)."""
    m = BP_RE.search(text)
    if not m:
        return None
    v = float(m.group(1))
    return v / 100 if m.group(2).lower().startswith(("bp", "베이")) else v


def _is_ir_sensitivity(t: Table) -> bool:
    b = _blob(t, 6)
    return ("이자율" in b or "금리" in b) and "환율" not in b \
        and bool(re.search(r"변동\s*시|상승\s*시|하락\s*시|상승시|하락시|변동시|민감도|인상\s*시|인하\s*시", b)) \
        and _ir_change(b) is not None


def ir_sensitivity(t: Table) -> list[Metric]:
    """이자율 상승 시 손익 영향 한 값. 상승 열이 있으면 그 열, 없으면 '상승' 행의 첫 숫자."""
    f = unit_factor(t.unit)
    if f is None:
        return []
    b = _blob(t, 6)
    up = _up_col(t)
    v = None
    for r in t.rows:
        label = r[0].replace(" ", "") if r else ""
        if PRIOR_RE.search(label) or "하락" in label or "인하" in label:
            continue
        if up is not None and up < len(r) and IR_ROW_RE.search(label + " " + b[:200]):
            v = num(r[up])
        elif up is None and ("상승" in label or "인상" in label):
            vals = [x for x in (num(c) for c in r[1:]) if x is not None]
            v = vals[0] if vals else None
        if v is not None:
            break
    if v is None:
        return []
    return [Metric("ir_sens", "ALL", v * f, _ir_change(b), _basis(b), t.unit, _evidence(t) or b[:300], t.index, t.file)]


LIAB_ROW_RE = re.compile(r"부채|차입|사채|이자\s*비용")
NOT_LIAB_RE = re.compile(r"자산|수익|예금|순효과|합계|총계|순\s*영향|전\s*기")


def ir_liability(t: Table) -> list[Metric]:
    """이자율 민감도 표에서 부채 쪽 행(금융부채·차입금 및 사채·이자비용)의 상승 시 영향만. 순효과 한 행뿐이면 없음.
    예: 삼성전자 '금융부채 △33,597', LG화학 '이자비용 (92,814)', 현대차 '차입금 및 사채 (173,638)' (백만원, 1%p)."""
    f = unit_factor(t.unit)
    up = _up_col(t)
    if f is None or up is None:
        return []
    b = _blob(t, 6)
    for r in t.rows:
        label = (r[0] if r else "").replace(" ", "")
        if not LIAB_ROW_RE.search(label) or NOT_LIAB_RE.search(label) or up >= len(r):
            continue
        v = num(r[up])
        if v is not None:
            return [Metric("ir_sens_liab", "ALL", v * f, _ir_change(b), _basis(b), t.unit, _evidence(t) or b[:300],
                           t.index, t.file)]
    return []


HEDGE_ACCT_RE = re.compile(r"위험\s*회피\s*회계")
HEDGE_TABLE_RE = re.compile(r"통화\s*선도|통화\s*스왑|선물환|통화\s*옵션|통화\s*이자율\s*스왑")


def fx_hedge_accounting(tables: list[Table]) -> Metric | None:
    for t in tables:
        b = _blob(t, 8)
        if HEDGE_ACCT_RE.search(b) and re.search(r"통화|환율|외화|환위험", b):
            return Metric("fx_hedge_acct", "ALL", 1.0, None, None, None, _evidence(t) or b[:300], t.index, t.file)
    return None


def hedge_text(tables: list[Table], width: int = 500) -> str:
    """통화 파생 계약 표(계약 현황) 한 개의 앞 문맥 + 첫 행들. 위험관리 절·주석 표를 먼저."""
    cands = [t for t in tables if HEDGE_TABLE_RE.search(_blob(t, 3))
             and re.search(r"계약|약정|매입|매도|명목|평가", _blob(t, 3))]
    if not cands:
        return ""
    t = min(cands, key=lambda x: (_section_rank(x), x.index))
    return (t.context[-250:] + " " + t.text(5))[:width]


# ---------------------------------------------------------------- 가격변동 문단 (판가 전가력 근거)

PRICE_HEAD_RE = re.compile(r"(주요\s*)?(제품|원재료|원\s*재료)\s*(등의)?\s*가격\s*변동\s*(추이|원인)|가격\s*변동\s*원인")


def price_text(files: dict[str, str], limit: int = 3, width: int = 600) -> list[str]:
    """본문에서 '주요 제품 등의 가격변동추이', '주요 원재료 등의 가격변동추이', '가격변동원인' 뒤 문단을 발췌.
    표 숫자는 빼고 문장만 남깁니다(판단은 에이전트가 '판단 보류'로 다루고, 이건 근거로만 씀)."""
    from .collectors.dart_doc import TABLE_RE, clean

    main = next(iter(files.values()), "")
    text = clean(TABLE_RE.sub(" ", main))
    out: list[str] = []
    for m in PRICE_HEAD_RE.finditer(text):
        piece = text[m.start():m.start() + width].strip()
        if len(re.sub(r"[^가-힣]", "", piece)) < 40:   # 제목만 있고 문장이 없는 경우
            continue
        if any(piece[:60] in o for o in out):
            continue
        out.append(piece)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- 묶음

def _section_rank(t: Table) -> int:
    sec = (t.extra or {}).get("section", "").replace(" ", "")
    if "연결재무제표주석" in sec or "연결재무제표에대한주석" in sec:
        return 0
    if "재무제표주석" in sec or "재무제표에대한주석" in sec or "위험관리" in sec:
        return 1
    return 2


def extract(files: dict[str, str], revenue_krw: float | None = None) -> dict:
    """원문 파일들 → {'metrics': [Metric], 'risk_text': 환위험 관리 서술(RAG용), 'price_text': [발췌], 'found': [...]}.
    본문(접수번호.xml) → 첨부(감사보고서) 순으로 보고, 지표마다 처음 찾은 표를 씁니다.
    환율 민감도는 같은 파일 안에 표가 여럿이면 문서 목차(TITLE)로 고릅니다:
    '연결재무제표 주석' → '재무제표 주석'·'위험관리 및 파생거래' → 그 밖(영업실적 분석, 계열회사 현황 등) 순. 같은 순위 안에서는 처음 나온 표.
    지주사 보고서에는 관계회사 표가 함께 실려 있어, 값 크기나 순서만으로 고르면 다른 회사 값을 집을 수 있습니다."""
    from .collectors.dart_doc import tables_from_xml

    metrics: list[Metric] = []
    got: set[str] = set()
    risk_text = ""
    all_tables: list[Table] = []
    for name, text in files.items():
        tables = tables_from_xml(text, name)
        all_tables += tables
        if "fx_sens" not in got:
            cands = [(ms, t) for t in tables if _is_sensitivity(t) for ms in [sensitivity(t)] if ms]
            if cands:
                ms, t = min(cands, key=lambda c: (_section_rank(c[1]), c[1].index))
                metrics += ms
                got.add("fx_sens")
                risk_text = risk_text or t.context
        if "ir_sens" not in got:
            cands = [(ms, t) for t in tables if _is_ir_sensitivity(t) for ms in [ir_sensitivity(t)] if ms]
            if cands:
                ms, _t = min(cands, key=lambda c: (_section_rank(c[1]), c[1].index))
                metrics += ms
                got.add("ir_sens")
        if "ir_sens_liab" not in got:
            cands = [(ms, t) for t in tables if _is_ir_sensitivity(t) for ms in [ir_liability(t)] if ms]
            if cands:
                ms, _t = min(cands, key=lambda c: (_section_rank(c[1]), c[1].index))
                metrics += ms
                got.add("ir_sens_liab")
        for t in tables:
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
    h = fx_hedge_accounting(all_tables)
    if h:
        metrics.append(h)
        got.add("fx_hedge_acct")
    hedge = hedge_text(all_tables)
    if hedge:
        got.add("hedge_text")
    prices = price_text(files)
    if prices:
        got.add("price_text")
    return {"metrics": metrics, "risk_text": risk_text[-900:], "price_text": prices, "hedge_text": hedge,
            "found": sorted(got)}
