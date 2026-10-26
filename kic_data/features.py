"""5개 관점(밸류에이션·펀더멘털·수급·거시·리스크) 지표 계산. 순수 함수라 DB 없이 테스트됩니다.

반환 행: {"perspective", "feature", "value", "unit", "note"}  (value 는 float 또는 None)
"""
from __future__ import annotations

import math
import statistics
from datetime import date, timedelta

OK_CCY = ("USD", "KRW")
KR_NI_NOTE = "한국: 비지배지분 포함 당기순이익 / 미국: 지배주주 귀속 순이익 (정의 차이)"

# (지표, 시리즈, 방식, 기간(일))   방식: level | diff(%p 변화) | pct(% 변화)
MACRO_SPEC = [
    ("kr_base_rate", "ECOS_BASE_RATE", "level", 0, "%"),
    ("kr_base_rate_chg_12m", "ECOS_BASE_RATE", "diff", 365, "%p"),
    ("usdkrw", "ECOS_USDKRW", "level", 0, "KRW"),
    ("usdkrw_chg_3m", "ECOS_USDKRW", "pct", 91, "%"),
    ("usdkrw_chg_12m", "ECOS_USDKRW", "pct", 365, "%"),
    ("us_10y", "DGS10", "level", 0, "%"),
    ("us_10y_chg_12m", "DGS10", "diff", 365, "%p"),
    ("fed_funds", "FEDFUNDS", "level", 0, "%"),
    ("fed_funds_chg_12m", "FEDFUNDS", "diff", 365, "%p"),
    ("us_cpi_yoy", "CPIAUCSL", "pct", 365, "%"),
]


def _f(x):
    return None if x is None else float(x)


def _row(perspective, feature, value, unit=None, note=None):
    v = _f(value)
    if v is not None and (math.isnan(v) or math.isinf(v)):
        v = None
    return {"perspective": perspective, "feature": feature, "value": v, "unit": unit, "note": note}


def _div(a, b):
    a, b = _f(a), _f(b)
    return None if a is None or b in (None, 0) else a / b


# ---------- 시세 기반 ----------
def pct_return(closes: list[float], n: int):
    return None if len(closes) <= n else closes[-1] / closes[-1 - n] - 1


def annualized_vol(closes: list[float], n: int):
    if len(closes) < n + 1:
        return None
    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - n, len(closes))]
    return statistics.stdev(rets) * math.sqrt(252)


def max_drawdown(closes: list[float]):
    if len(closes) < 2:
        return None
    peak, worst = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        worst = min(worst, c / peak - 1)
    return worst


def avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


# ---------- 거시 ----------
def value_at(series: list[tuple], target: date):
    """series: [(date, value)] 오름차순. target 이하 가장 최근 값."""
    best = None
    for d, v in series:
        if d <= target and v is not None:
            best = (d, v)
        elif d > target:
            break
    return best


def macro_rows(macro: dict[str, list[tuple]]) -> list[dict]:
    rows = []
    for feature, sid, kind, days, unit in MACRO_SPEC:
        series = [(d, _f(v)) for d, v in macro.get(sid, []) if v is not None]
        if not series:
            rows.append(_row("macro", feature, None, unit, f"{sid} 데이터 없음"))
            continue
        last_d, last_v = series[-1]
        if kind == "level":
            val = last_v
        else:
            prev = value_at(series, last_d - timedelta(days=days))
            if prev is None:
                val = None
            elif kind == "diff":
                val = last_v - prev[1]
            else:
                val = (last_v / prev[1] - 1) * 100 if prev[1] else None
        rows.append(_row("macro", feature, val, unit, f"{sid} 기준일 {last_d}"))
    return rows


# ---------- 환율 환산 (외국 기업 재무 → 시세 통화 USD) ----------
# 통화: (FRED 시리즈, 방식)  mul = 시리즈가 '1단위당 USD' / div = 시리즈가 '1 USD 당 해당 통화'
FX_TO_USD = {"EUR": ("DEXUSEU", "mul"), "TWD": ("DEXTAUS", "div")}


def usd_per_unit(macro: dict, ccy: str):
    """(1 ccy 당 USD, 기준일) 또는 None. USD 는 (1.0, None)."""
    if ccy == "USD":
        return 1.0, None
    spec = FX_TO_USD.get(ccy)
    if not spec:
        return None
    series = [(d, _f(v)) for d, v in macro.get(spec[0], []) if v is not None and _f(v)]
    if not series:
        return None
    d, v = series[-1]
    return (v if spec[1] == "mul" else 1.0 / v), d


def fill_fx_valuation(snap: dict, macro: dict) -> dict:
    """연간 PER/PBR 이 통화 불일치로 비어 있는 해외 종목(ASML·TSMC 등)을 최근 환율로 환산해 채웁니다.
    snap 의 market_cap 은 시세 통화(USD), net_income_annual·equity_annual 은 재무 통화입니다."""
    ccy = snap.get("fin_currency")
    if snap.get("market") != "US" or ccy in OK_CCY or snap.get("per_annual") is not None:
        return snap
    fx = usd_per_unit(macro, ccy or "")
    mcap = _f(snap.get("market_cap"))
    if fx is None or mcap is None:
        return snap
    rate, asof = fx
    out = dict(snap)
    ni, eq = _f(snap.get("net_income_annual")), _f(snap.get("equity_annual"))
    out["per_annual"] = mcap / (ni * rate) if ni and ni > 0 else None
    out["pbr_annual"] = mcap / (eq * rate) if eq and eq > 0 else None
    out["fx_note"] = f"{ccy}→USD 환산 (FRED {FX_TO_USD[ccy][0]} {asof}, 1{ccy}={rate:.4f}USD)"
    return out


# ---------- Peer ----------
def peer_medians(peer_snaps: list[dict]) -> dict:
    def pick(s, key):  # PBR 은 TTM(분기말 자본) 값이 없으면 연간 값(환율 환산 포함) 사용
        v = s.get(key)
        return s.get("pbr_annual") if v is None and key == "pbr" else v

    def med(key, positive=False):
        xs = [_f(pick(s, key)) for s in peer_snaps]
        xs = [x for x in xs if x is not None and (x > 0 or not positive)]
        return statistics.median(xs) if xs else None

    return {"per_ttm": med("per_ttm", True), "pbr": med("pbr", True),
            "op_margin_ttm": med("op_margin_ttm"), "roe_ttm": med("roe_ttm"), "n": len(peer_snaps)}


def growth_yoy(quarters: list[dict], metric: str):
    """가장 최근 분기 값 대비 전년 같은 분기 증가율 (전년 값이 양수일 때만)."""
    rows = [q for q in quarters if q["metric"] == metric and q["amount"] is not None]
    if not rows:
        return None, None
    last = max(rows, key=lambda q: q["period_end"])
    prev = next((q for q in rows if q["fiscal_q"] == last["fiscal_q"] and q["fiscal_year"] == last["fiscal_year"] - 1), None)
    if prev is None or _f(prev["amount"]) is None or _f(prev["amount"]) <= 0:
        return None, last["period_end"]
    return _f(last["amount"]) / _f(prev["amount"]) - 1, last["period_end"]


# ---------- 종합 ----------
RECENCY_WEIGHTS = {0: 3.0, 1: 2.0}  # 경과일 → 가중치 (그 외 1.0)


def daily_increments(series: list[tuple]) -> list[tuple]:
    """[(날짜, 그날 마지막 누적 total)] → [(날짜, 하루당 증가분)]. 날짜가 비면 일수로 나눔, 감소는 0."""
    out = []
    for (d0, t0), (d1, t1) in zip(series, series[1:]):
        gap = (d1 - d0).days
        if gap > 0:
            out.append((d1, max(0, t1 - t0) / gap))
    return out


def mention_ratios(mentions: dict, sources: tuple, recent_days: int, min_days: int):
    """출처 합산 하루 증가분 → (최근 recent_days 평균 / 그 이전 평균, 누적 일수)."""
    by_day: dict = {}
    for src in sources:
        for d, inc in daily_increments(mentions.get(src) or []):
            by_day[d] = by_day.get(d, 0.0) + inc
    incs = [v for _, v in sorted(by_day.items())]
    if len(incs) < min_days:
        return None, len(incs)
    recent, base = incs[-recent_days:], incs[:-recent_days][-30:]
    return (avg(recent) / avg(base) if base and avg(base) else None), len(incs)


def attention_rows(attention: dict | None) -> list[dict]:
    """검색 관심도(데이터랩)·뉴스 건수로 '시장의 관심' 지표를 만듭니다. 값이 부족하면 None."""
    attention = attention or {}
    today = attention.get("today") or date.today()
    search = attention.get("search") or []  # [(날짜, 기준 대비 상대값)]
    recent = [v for d, v in search[-7:]]
    base = [v for d, v in search[-97:-7]]
    last = search[-1][0] if search else None
    note = f"네이버 검색('주가'·'주식' 포함) 기준일 {last}" if last else "검색 관심도 미수집 (collect-trends)"
    spike = (avg(recent) / avg(base)) if len(recent) >= 5 and len(base) >= 30 and avg(base) else None
    level = avg(recent) if len(recent) >= 5 else None

    # 언급량: 검색 결과 누적 수(total)의 하루 증가분 (수집 표본 건수는 큰 종목에서 왜곡되므로 쓰지 않음)
    mentions = attention.get("mentions") or {}
    all_src = ("naver_news", "naver_blog", "naver_cafe")
    m1, n_days = mention_ratios(mentions, all_src, 1, 6)
    crowd_ratio, n_c = mention_ratios(mentions, ("naver_blog", "naver_cafe"), 7, 14)
    news_ratio, n_n = mention_ratios(mentions, ("naver_news",), 7, 14)
    news_inc = daily_increments(mentions.get("naver_news") or [])
    news_per_day = avg([v for _, v in news_inc[-7:]]) if len(news_inc) >= 3 else None

    def acc(n, need):
        return f" (누적 {n}일 / 필요 {need}일, pulse·daily-update 가 하루 1회 이상 돌면 쌓임)" if n < need else ""

    # 반응 톤. 7일 값은 최근일 가중(오늘 3배, 어제 2배, 그 이전 1배)이라 당일 급변이 희석되지 않습니다.
    reactions = attention.get("reactions") or []

    def age(r):
        return (today - r["published_at"]).days if r["published_at"] else None

    def tone(sources, lo, hi, weighted=False, min_n=5):
        pts = [(r["score"], age(r)) for r in reactions
               if r["source"] in sources and r["score"] is not None and age(r) is not None and lo <= age(r) < hi]
        if len(pts) < min_n:
            return None, len(pts)
        ws = [RECENCY_WEIGHTS.get(a, 1.0) if weighted else 1.0 for _, a in pts]
        return sum(s * w for (s, _), w in zip(pts, ws)) / sum(ws), len(pts)

    crowd = ("naver_blog", "naver_cafe")
    dated = ("naver_news", "naver_blog")  # 작성일이 확실한 출처만 (카페는 수집일 기준이라 당일 지표에서 제외)
    crowd_7, n_c7 = tone(crowd, 0, 7, weighted=True)
    crowd_prev, _ = tone(crowd, 7, 30)
    news_7, n_n7 = tone(("naver_news",), 0, 7, weighted=True)
    yt_7, n_y7 = tone(("youtube_comment",), 0, 7, weighted=True)
    all_7, _ = tone(dated, 0, 7, weighted=True)
    day_1, n_d1 = tone(dated, 0, 1, min_n=3)
    tone_note = "키워드 기반 톤 -1(부정)~+1(긍정)"
    w_note = "최근일 가중(오늘×3, 어제×2), 5건 이상일 때"

    return [
        _row("supply_demand", "sentiment_1d", day_1, "score", f"오늘({today}) 뉴스·블로그 {n_d1}건, 3건 이상일 때. {tone_note}"),
        _row("supply_demand", "sentiment_1d_vs_7d", (day_1 - all_7) if None not in (day_1, all_7) else None, "score_diff",
             "오늘 톤 − 최근 7일 톤. 크게 벗어나면 당일 이슈 발생"),
        _row("supply_demand", "mentions_1d_vs_30d", m1, "x",
             "오늘 새 뉴스·블로그·카페 글 수 / 이전 일평균 (검색 결과 누적 수 증가분). 2 이상이면 언급 급증" + acc(n_days, 6)),
        _row("supply_demand", "crowd_sentiment_7d", crowd_7, "score", f"블로그·카페 {n_c7}건. {tone_note}. {w_note}"),
        _row("supply_demand", "crowd_sentiment_chg", (crowd_7 - crowd_prev) if None not in (crowd_7, crowd_prev) else None,
             "score_diff", "최근 7일(가중) − 직전 8~30일. +면 투자자 분위기 개선"),
        _row("supply_demand", "news_sentiment_7d", news_7, "score", f"뉴스 {n_n7}건. {tone_note}. {w_note}"),
        _row("supply_demand", "youtube_sentiment_7d", yt_7, "score", f"유튜브 공개 댓글 {n_y7}건. {tone_note}. {w_note}"),
        _row("supply_demand", "crowd_posts_7d_vs_30d", crowd_ratio, "x",
             "블로그·카페 새 글 최근 7일 일평균 / 이전 일평균. 1보다 크면 언급 증가" + acc(n_c, 14)),
        _row("supply_demand", "search_attention_7d_vs_90d", spike, "x", f"최근 7일 / 직전 90일 평균. 1보다 크면 관심 증가. {note}"),
        _row("supply_demand", "search_attention_level_7d", level, "x", f"'주식' 검색량 대비 상대 크기 (종목 간 비교용). {note}"),
        _row("supply_demand", "news_per_day_7d", news_per_day, "건",
             "네이버 뉴스 검색 결과(본문 언급 포함) 하루 증가분, 최근 7일 평균" + acc(len(news_inc), 3)),
        _row("supply_demand", "news_7d_vs_30d", news_ratio, "x", "뉴스 새 기사 최근 7일 일평균 / 이전 일평균" + acc(n_n, 14)),
    ]


def flow_rows(flows: list[dict] | None) -> list[dict]:
    """투자자별 순매수 (core.investor_flow_daily, 오름차순). 국내 종목만, 해외는 None → 행 없음."""
    if flows is None:
        return []
    note = "네이버 증권 투자자별 매매동향(수량 기준)"
    last = flows[-20:]
    vol = sum(_f(r["volume"]) or 0 for r in last)
    rows = []
    for who, col in (("foreign", "foreign_net_qty"), ("institution", "institution_net_qty"), ("individual", "individual_net_qty")):
        net = [_f(r[col]) for r in last if r[col] is not None]
        val = sum(net) / vol if len(last) >= 20 and net and vol else None
        rows.append(_row("supply_demand", f"{who}_net_buy_20d_ratio", val, "ratio",
                         f"최근 20거래일 순매수 수량 합 / 거래량 합. {note}" if val is not None else "투자자별 데이터 20거래일 미만"))
    streak = 0
    for r in reversed(flows):
        q = _f(r["foreign_net_qty"])
        if not q or (streak and (q > 0) != (streak > 0)):
            break
        streak += 1 if q > 0 else -1
    rows.append(_row("supply_demand", "foreign_net_buy_streak", streak if flows else None, "days",
                     "외국인 연속 순매수(+)·순매도(−) 거래일 수"))
    holds = [(r["trade_date"], _f(r["foreign_hold_pct"])) for r in flows if r["foreign_hold_pct"] is not None]
    rows.append(_row("supply_demand", "foreign_hold_pct", holds[-1][1] if holds else None, "%", "외국인 보유율"))
    chg = holds[-1][1] - holds[-21][1] if len(holds) >= 21 else None
    rows.append(_row("supply_demand", "foreign_hold_chg_20d", chg, "%p", "외국인 보유율 20거래일 변화"))
    return rows


def lending_rows(lend: list[dict] | None, shares) -> list[dict]:
    """대차잔고 (core.stock_lending_daily, 오름차순). 공매도 압력의 대용 지표. 국내 종목만."""
    if lend is None:
        return []
    note = "금융위원회 주식대차거래정보"
    bal = [(r["trade_date"], _f(r["balance_qty"])) for r in lend if r["balance_qty"] is not None]
    last = bal[-1][1] if bal else None
    chg = _div(last, bal[-21][1]) - 1 if len(bal) >= 21 and bal[-21][1] else None
    return [
        _row("supply_demand", "lending_balance_to_shares", _div(last, shares), "ratio",
             f"대차잔고 주식수 / 발행주식수 ({bal[-1][0]}). {note}" if bal else "대차 데이터 없음"),
        _row("supply_demand", "lending_balance_chg_20d", chg, "ratio", f"대차잔고 주식수 20거래일 변화율. {note}"),
    ]


def compute_features(snap: dict, prices: list[dict], quarters: list[dict], macro: dict, peers: dict,
                     attention: dict | None = None, flows: list[dict] | None = None,
                     lend: list[dict] | None = None) -> list[dict]:
    """snap: v_valuation + v_ttm 병합 행. prices: [{trade_date, close, volume}] 오름차순.
    flows·lend: 국내 종목의 투자자별 순매수·대차거래 (오름차순, 해외는 None)."""
    rows = []
    closes = [_f(p["close"]) for p in prices if p["close"] is not None]
    vols = [_f(p["volume"]) for p in prices]
    ccy_ok = snap.get("ttm_currency") in OK_CCY
    has_ttm = snap.get("ttm_end") is not None

    # 밸류에이션
    rows.append(_row("valuation", "market_cap", snap.get("market_cap"), snap.get("fin_currency") or "", "종가 × 발행주식수 (ADR은 ADR 주식수로 환산)"))
    rows.append(_row("valuation", "per_ttm", snap.get("per_ttm"), "x", "최근 4분기 순이익 기준" if has_ttm else "TTM 데이터 없음"))
    fx_note = snap.get("fx_note")
    pbr_val = snap.get("pbr") if has_ttm and snap.get("pbr") is not None else snap.get("pbr_annual")
    pbr_note = "최근 분기말 자본 기준" if has_ttm and snap.get("pbr") is not None else "연간 자본 기준"
    rows.append(_row("valuation", "pbr", pbr_val, "x", pbr_note + (f". {fx_note}" if fx_note else "")))
    rows.append(_row("valuation", "per_annual", snap.get("per_annual"), "x",
                     f"FY{snap.get('fiscal_year')} 기준" + (f". {fx_note}" if fx_note else "")))
    ps = _div(snap.get("market_cap"), snap.get("revenue_ttm")) if ccy_ok else None
    rows.append(_row("valuation", "ps_ttm", ps, "x", None if ps is not None else "통화 불일치 또는 TTM 없음"))
    for feat, key, own in (("per_ttm_vs_peer_median", "per_ttm", snap.get("per_ttm")), ("pbr_vs_peer_median", "pbr", pbr_val)):
        m, v = peers.get(key), _f(own)
        rows.append(_row("valuation", feat, (v / m - 1) if (m and v and v > 0) else None, "ratio",
                         f"비교군 {peers.get('n', 0)}개 중앙값 대비 프리미엄(+)/할인(−)"))

    # 펀더멘털
    rev_g, rev_end = growth_yoy(quarters, "revenue")
    op_g, _ = growth_yoy(quarters, "operating_income")
    ni_ttm, rev_ttm = snap.get("net_income_ttm"), snap.get("revenue_ttm")
    rows.append(_row("fundamentals", "revenue_growth_yoy_q", rev_g, "ratio", f"최근 분기 {rev_end} vs 전년 동기" if rev_end else "분기 데이터 없음"))
    rows.append(_row("fundamentals", "operating_income_growth_yoy_q", op_g, "ratio", "전년 동기 영업이익이 양수일 때만"))
    basis = "TTM" if has_ttm else f"연간 FY{snap.get('fiscal_year')}"
    rows.append(_row("fundamentals", "op_margin", snap.get("op_margin_ttm") if has_ttm else snap.get("op_margin_annual"), "ratio", basis))
    rows.append(_row("fundamentals", "net_margin_ttm", _div(ni_ttm, rev_ttm), "ratio", "TTM" if has_ttm else "TTM 데이터 없음"))
    rows.append(_row("fundamentals", "roe", snap.get("roe_ttm") if has_ttm else snap.get("roe_annual"), "ratio", f"{basis}. {KR_NI_NOTE}"))
    rows.append(_row("fundamentals", "debt_ratio", snap.get("debt_ratio_ttm") if has_ttm else snap.get("debt_ratio_annual"), "ratio", "부채/자본"))
    for feat, key in (("op_margin_vs_peer_median", "op_margin_ttm"), ("roe_vs_peer_median", "roe_ttm")):
        m = peers.get(key)
        v = _f(snap.get("op_margin_ttm" if key == "op_margin_ttm" else "roe_ttm"))
        rows.append(_row("fundamentals", feat, (v - m) if (m is not None and v is not None) else None, "ratio_diff", "비교군 중앙값과의 차이"))

    # 수급 (시세·거래량 기반 + 국내 투자자별 순매수·대차잔고. KRX 공매도 통계는 로그인 필요라 대차로 대신)
    v20, v60 = avg(vols[-20:]), avg(vols[-60:])
    notional = [c * v for c, v in zip(closes[-20:], vols[-20:]) if c is not None and v is not None]
    hi, lo = (max(closes[-250:]), min(closes[-250:])) if closes else (None, None)
    rows.append(_row("supply_demand", "volume_ratio_20_60", _div(v20, v60), "x", "최근 20일 평균 거래량 / 60일 평균"))
    rows.append(_row("supply_demand", "turnover_20d_avg", avg(notional), snap.get("ccy") or "", "최근 20일 평균 거래대금(종가×거래량)"))
    rows.append(_row("supply_demand", "return_20d", pct_return(closes, 20), "ratio"))
    rows.append(_row("supply_demand", "return_60d", pct_return(closes, 60), "ratio"))
    rows.append(_row("supply_demand", "position_in_52w_range", _div(closes[-1] - lo, hi - lo) if closes and hi != lo else None, "ratio", "0=52주 저가, 1=52주 고가"))
    rows += flow_rows(flows)
    rows += lending_rows(lend, snap.get("shares_outstanding"))
    rows += attention_rows(attention)

    # 거시 (시장 공통)
    rows.extend(macro_rows(macro))

    # 리스크
    rows.append(_row("risk", "volatility_60d", annualized_vol(closes, 60), "ratio", "일수익률 표준편차 × √252"))
    rows.append(_row("risk", "volatility_250d", annualized_vol(closes, 250), "ratio"))
    rows.append(_row("risk", "max_drawdown_250d", max_drawdown(closes[-250:]), "ratio"))
    rows.append(_row("risk", "below_52w_high", (closes[-1] / hi - 1) if closes and hi else None, "ratio"))
    rows.append(_row("risk", "debt_ratio", snap.get("debt_ratio_ttm") if has_ttm else snap.get("debt_ratio_annual"), "ratio", "부채/자본"))
    ni = _f(ni_ttm)
    rows.append(_row("risk", "net_loss_ttm", None if ni is None else (1.0 if ni < 0 else 0.0), "flag", "최근 4분기 순손실이면 1"))
    return rows
