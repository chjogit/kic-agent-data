"""수집 현황 점검 (읽기 전용: SELECT 만 실행).

    python -m kic_data.cli status                 # 화면 출력
    python -m kic_data.cli status --json status.json   # 같은 내용을 파일로 (팀 공유·보고서용)

테이블이 아직 없으면 '없음' 으로 표시하고 넘어갑니다. 최신 날짜가 기준보다 오래되면 '오래됨' 으로 표시합니다.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from . import db
from .config import env

# 최신성 기준(일). 주말·휴일을 감안해 여유를 둡니다.
FRESH_DAYS = {"price": 5, "index": 5, "news": 3, "blog": 3, "cafe": 3, "disclosure": 14, "trend": 4,
              "mention": 3, "pulse": 3, "macro_D": 10, "macro_M": 75, "feature": 3, "weekly": 10}

# 새 수집 표 (표, 이름, 기준일 칸, 작업 주기). 주 1회 작업은 마지막 수집 시각을 주 단위(10일)로 판정합니다.
NEW_TABLES = (
    ("core.ownership_report", "지분공시(대량보유)", "rcept_dt", "weekly"),
    ("core.largest_holder", "최대주주 지분율", "settle_dt", "weekly"),
    ("core.audit_opinion", "감사의견", "settle_dt", "weekly"),
    ("core.financial_item", "재무 항목(감가상각·이자비용)", None, "weekly"),
)

# DB 용량 (Neon 무료 1GB). 경고 기준은 환경변수 DB_WARN_MB 로 바꿀 수 있습니다.
DB_LIMIT_MB = 1024
DB_WARN_MB = 700    # 내부 한도 800MB 에 맞춤(#69 PM 결정 11/6). 이전 900 은 773MB 에서도 울리지 않았음
NEON_GAP_MB = 21  # Neon 콘솔의 logical size 는 pg_database_size 보다 이만큼 크게 나옴 (10-30 실측)


def warn_threshold_mb() -> int:
    try:
        return max(1, int(env("DB_WARN_MB") or DB_WARN_MB))
    except ValueError:
        return DB_WARN_MB


def db_usage(size_bytes: int | None, warn_mb: int | None = None) -> dict | None:
    """DB 크기 → {mb, warn_mb, limit_mb, pct, over}. over 는 경고 기준 이상."""
    if size_bytes is None:
        return None
    warn_mb = warn_mb or warn_threshold_mb()
    mb = size_bytes / (1024 * 1024)
    return {"mb": round(mb, 1), "warn_mb": warn_mb, "limit_mb": DB_LIMIT_MB,
            "pct": round(mb / DB_LIMIT_MB * 100, 1), "over": mb >= warn_mb}


def business_days_since(last, today: date) -> int:
    """last 다음 날부터 today 까지의 평일 수 (공휴일은 모름 → 연휴엔 '오래됨'이 하루 이틀 일찍 뜰 수 있음)."""
    last = last.date() if hasattr(last, "date") else last
    return sum(1 for k in range(1, (today - last).days + 1) if (last + timedelta(days=k)).weekday() < 5)


def market_freshness(last, today: date, max_business_days: int = 2) -> str:
    """시장 전체 일별 지표용: 원천이 하루 늦게 올라오므로 평일 2일까지는 OK."""
    if last is None:
        return "없음"
    n = business_days_since(last, today)
    return "OK" if n <= max_business_days else f"오래됨({n}영업일 전)"


def freshness(last, today: date, max_age: int) -> str:
    """'OK' / '오래됨(N일 전)' / '없음'. last 는 date·datetime·None."""
    if last is None:
        return "없음"
    if isinstance(last, datetime):
        last = last.date()
    age = (today - last).days
    return "OK" if age <= max_age else f"오래됨({age}일 전)"


def _q_err(conn, sql: str) -> tuple[list[tuple] | None, str | None]:
    """_q 와 같지만 실패 이유를 함께 돌려줌 ('조회 실패: <이유>' 표시용; 비워 두고 넘어가지 않게)."""
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall(), None
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        return None, f"{type(exc).__name__}: {str(exc).splitlines()[0][:120]}"


def _q(conn, sql: str, params=None) -> list[tuple] | None:
    """쿼리 실행. 테이블·컬럼이 없으면 None (트랜잭션 복구)."""
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or [])
            return cur.fetchall()
    except Exception:  # UndefinedTable 등
        conn.rollback()
        return None


@contextmanager
def opinion_reader():
    """(kic-opinion 읽기 전용 연결 또는 None, 설정 여부). 접속 정보가 있는데 못 붙으면 (None, True)."""
    if not env(db.OPINION_URL_ENV):
        yield None, False
        return
    try:
        oc = db.connect_opinion(read_only=True)
    except Exception:  # noqa: BLE001  kic-opinion 에 못 붙어도 메인 현황은 보여 줌
        yield None, True
        return
    try:
        yield oc, True
    finally:
        oc.close()


def collect_all(conn, today: date | None = None) -> dict:
    """collect + 블로그·카페는 kic-opinion(OPINION_DATABASE_URL 설정 시)에서 읽음."""
    with opinion_reader() as (oc, configured):
        return collect(conn, today, oc, configured)


def collect(conn, today: date | None = None, opinion_conn=None, opinion_configured: bool | None = None) -> dict:
    """opinion_conn 이 있으면 블로그·카페 건수·최신일·감성 점수·문서 현황을 kic-opinion 에서 읽습니다.
    설정돼 있는데 연결이 없으면(접속 실패) 블로그·카페 값을 비워 둡니다 (메인의 옛 값으로 '오래됨' 오경보를 내지 않게)."""
    today = today or date.today()
    out: dict = {"checked_at": today.isoformat()}
    split = opinion_conn is not None and opinion_conn is not conn
    configured = split if opinion_configured is None else bool(opinion_configured)
    out["opinion"] = {"configured": configured, "connected": split}
    opin = ", ".join(f"'{s}'" for s in db.OPINION_SOURCES)
    size = _q(conn, "SELECT pg_database_size(current_database())")
    out["db"] = db_usage(int(size[0][0])) if size else None

    companies = _q(conn, "SELECT company_id, market, ticker, name, corp_code, cik, sector, shares_outstanding "
                         "FROM core.company ORDER BY market, ticker") or []
    per: dict[int, dict] = {
        cid: {"market": m, "ticker": t, "name": n, "corp_code": bool(cc), "cik": bool(ck),
              "sector": bool(sec), "shares": sh is not None}
        for cid, m, t, n, cc, ck, sec, sh in companies}

    def merge(sql: str, keys: tuple[str, ...], c=conn):
        for r in _q(c, sql) or []:
            if r[0] in per:
                per[r[0]].update(dict(zip(keys, r[1:])))

    merge("SELECT company_id, count(*), min(trade_date), max(trade_date), string_agg(DISTINCT source, ',') "
          "FROM core.price_daily GROUP BY company_id", ("price_rows", "price_from", "price_to", "price_source"))
    merge("SELECT company_id, min(fiscal_year), max(fiscal_year), "
          "count(DISTINCT fiscal_year) FILTER (WHERE report_code IN ('11011','FY')), "
          "count(DISTINCT (fiscal_year, report_code)) FILTER (WHERE report_code NOT IN ('11011','FY')) "
          "FROM core.financial_fact GROUP BY company_id", ("fin_from", "fin_to", "annual_years", "quarter_reports"))
    merge("SELECT company_id, count(DISTINCT period_end), max(period_end) FROM core.financial_quarter GROUP BY company_id",
          ("quarters", "last_quarter"))
    merge("SELECT c.company_id, t.ttm_end FROM core.v_ttm t JOIN core.company c USING (market, ticker)", ("ttm_end",))
    merge("SELECT company_id, count(*), max(rcept_dt) FROM core.disclosure GROUP BY company_id",
          ("disclosures", "disclosure_last"))
    for src, label in (("naver_news", "news"), ("naver_blog", "blog"), ("naver_cafe", "cafe")):
        c = conn
        if configured and src in db.OPINION_SOURCES:
            if not split:
                continue
            c = opinion_conn
        merge(f"SELECT company_id, count(*), max(published_at) FROM rag.document WHERE source = '{src}' GROUP BY company_id",
              (f"{label}_docs", f"{label}_last"), c)
    scored = "SELECT d.company_id, count(*) FROM core.doc_sentiment s JOIN rag.document d USING (doc_id)"
    merge(scored + (f" WHERE d.source NOT IN ({opin})" if configured else "") + " GROUP BY d.company_id", ("scored_docs",))
    for cid, n in (_q(opinion_conn, scored + " GROUP BY d.company_id") or []) if split else []:
        if cid in per:
            per[cid]["scored_docs"] = (per[cid].get("scored_docs") or 0) + n
    merge("SELECT company_id, count(DISTINCT trade_date), max(captured_at) FROM core.mention_count GROUP BY company_id",
          ("mention_days", "mention_last"))
    merge("SELECT company_id, count(*), max(captured_at) FROM core.reaction_pulse GROUP BY company_id",
          ("pulse_rows", "pulse_last"))
    trend = {r[0]: r[1:] for r in _q(conn, "SELECT subject, count(*), max(trade_date) FROM core.search_trend GROUP BY subject") or []}
    for p in per.values():
        n, last = trend.get(f"{p['market']}:{p['ticker']}", (0, None))
        p.update(trend_days=n, trend_last=last)
    merge("SELECT company_id, max(as_of), count(*) FILTER (WHERE value IS NOT NULL), count(*) "
          "FROM core.feature_snapshot WHERE as_of = (SELECT max(as_of) FROM core.feature_snapshot) GROUP BY company_id",
          ("feature_as_of", "features_filled", "features_total"))
    out["companies"] = list(per.values())
    r, err = _q_err(conn, "SELECT count(*), max(bas_dt), max(collected_at) FROM core.credit_balance")
    out["market_indicators"] = [{"label": "신용공여 잔고(시장 전체)", "rows": r[0][0], "last_date": r[0][1],
                                 "last_collected": r[0][2], "error": None}] if r else ([] if err is None else [
        {"label": "신용공여 잔고(시장 전체)", "rows": None, "last_date": None, "last_collected": None, "error": err}])
    out["new_collections"] = []
    for tbl, label, dcol, freq in NEW_TABLES:
        r, err = _q_err(conn, f"SELECT count(*), count(DISTINCT company_id), {f'max({dcol})' if dcol else 'NULL'}, max(collected_at) FROM {tbl}")
        if r:
            n, cos_n, last_dt, last_at = r[0]
            out["new_collections"].append({"table": tbl, "label": label, "rows": n, "companies": cos_n,
                                           "last_date": last_dt, "last_collected": last_at, "freq": freq, "error": None})
        elif err:
            out["new_collections"].append({"table": tbl, "label": label, "rows": None, "companies": None,
                                           "last_date": None, "last_collected": None, "freq": freq, "error": err})

    out["indices"] = [dict(zip(("symbol", "name", "rows", "from", "to"), r)) for r in _q(
        conn, "SELECT symbol, max(name), count(*), min(trade_date), max(trade_date) FROM core.market_index_daily "
              "GROUP BY symbol ORDER BY symbol") or []]
    out["macro"] = [dict(zip(("series_id", "name", "source", "frequency", "rows", "from", "to"), r)) for r in _q(
        conn, "SELECT s.series_id, s.name, s.source, s.frequency, count(o.obs_date), min(o.obs_date), max(o.obs_date) "
              "FROM core.macro_series s LEFT JOIN core.macro_obs o USING (series_id) "
              "GROUP BY s.series_id, s.name, s.source, s.frequency ORDER BY s.source, s.series_id") or []]
    doc_keys = ("source", "docs", "companies", "from", "to", "chunks")
    out["documents"] = [dict(zip(doc_keys, r)) for r in _q(
        conn, "SELECT d.source, count(*), count(DISTINCT d.company_id), min(d.published_at), max(d.published_at), "
              "(SELECT count(*) FROM rag.chunk c JOIN rag.document x USING (doc_id) WHERE x.source = d.source) "
              f"FROM rag.document d {f'WHERE d.source NOT IN ({opin}) ' if configured else ''}"
              "GROUP BY d.source ORDER BY d.source") or []]
    if split:   # kic-opinion 에는 청크 표가 없음 → 청크 '-'
        out["documents"] = sorted(out["documents"] + [dict(zip(doc_keys, (*r, None))) for r in _q(
            opinion_conn, "SELECT source, count(*), count(DISTINCT company_id), min(published_at), max(published_at) "
                          "FROM rag.document GROUP BY source") or []], key=lambda d: d["source"])
    out["disclosures"] = [dict(zip(("source", "category", "rows", "unclassified", "from", "to"), r)) for r in _q(
        conn, "SELECT source, category, count(*), count(*) FILTER (WHERE event_type IS NULL), min(rcept_dt), max(rcept_dt) "
              "FROM core.disclosure GROUP BY source, category ORDER BY source, category") or []]
    topics = _q(conn, "SELECT subject, count(*), max(trade_date) FROM core.search_trend WHERE subject LIKE 'topic:%%' "
                      "GROUP BY subject ORDER BY subject") or []
    out["trend_topics"] = [dict(zip(("subject", "days", "to"), r)) for r in topics]
    peers = _q(conn, "SELECT c.market, c.ticker, g.method, count(m.company_id) FROM core.peer_group g "
                     "JOIN core.company c ON c.company_id = g.base_company_id "
                     "LEFT JOIN core.peer_member m USING (peer_group_id) GROUP BY 1, 2, 3 ORDER BY 1, 2") or []
    out["peer_groups"] = [dict(zip(("market", "ticker", "method", "members"), r)) for r in peers]
    return out


def _s(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, datetime):
        return v.strftime("%m-%d %H:%M")
    return str(v)


def render(st: dict) -> str:
    today = date.fromisoformat(st["checked_at"])
    lines = [f"수집 현황 ({st['checked_at']} 기준)", ""]
    u = st.get("db")
    if u:
        lines.append(f"[DB 용량] {u['mb']}MB / 한도 {u['limit_mb']}MB ({u['pct']}%)  경고 기준 {u['warn_mb']}MB")
        if u["over"]:
            lines.append(f"  [경고] DB 가 경고 기준 {u['warn_mb']}MB 를 넘었습니다. 한도({u['limit_mb']}MB)에 닿으면 수집 쓰기가 실패합니다. "
                         f"(Neon 콘솔 수치는 이 값보다 약 {NEON_GAP_MB}MB 크게 보입니다)")
        lines.append("")
    op = st.get("opinion") or {}
    if op.get("configured"):
        lines += ["[kic-opinion] 블로그·카페는 kic-opinion 에서 읽음" if op.get("connected")
                  else "[kic-opinion] 접속 실패 — 블로그·카페 건수·최신성을 비워 둠", ""]
    cos = st["companies"]
    lines.append(f"[종목] {len(cos)}개  (DART 고유번호 {sum(c['corp_code'] for c in cos if c['market'] == 'KR')}"
                 f"/{sum(c['market'] == 'KR' for c in cos)}, CIK {sum(c['cik'] for c in cos if c['market'] == 'US')}"
                 f"/{sum(c['market'] == 'US' for c in cos)}, 발행주식수 {sum(c['shares'] for c in cos)}/{len(cos)})")
    lines.append(f"{'종목':<11}{'시세(행/기간)':<30}{'재무 연도':<11}{'분기':>4} {'TTM':<11}{'공시':>5} "
                 f"{'뉴스':>5} {'블로그':>5} {'카페':>5} {'감성':>5} {'검색':>4} {'언급':>4} {'지표':>7}")
    for c in cos:
        price = (f"{c.get('price_rows', 0)} {_s(c.get('price_from'))}~{_s(c.get('price_to'))}"
                 if c.get("price_rows") else "-")
        fin = f"{_s(c.get('fin_from'))}-{_s(c.get('fin_to'))}" if c.get("fin_from") else "-"
        feat = f"{c.get('features_filled', 0)}/{c.get('features_total', 0)}" if c.get("features_total") else "-"
        lines.append(
            f"{c['market']}:{c['ticker']:<8}{price:<30}{fin:<11}{c.get('quarters') or 0:>4} {_s(c.get('ttm_end')):<11}"
            f"{c.get('disclosures') or 0:>5} {c.get('news_docs') or 0:>5} {c.get('blog_docs') or 0:>5} "
            f"{c.get('cafe_docs') or 0:>5} {c.get('scored_docs') or 0:>5} {c.get('trend_days') or 0:>4} "
            f"{c.get('mention_days') or 0:>4} {feat:>7}")

    def latest(key):
        vals = [c.get(key) for c in cos if c.get(key) is not None]
        return max(vals) if vals else None

    lines += ["", "[최신성] (가장 최근 값 / 판정)"]
    for label, key, fk in (("국내·해외 시세", "price_to", "price"), ("뉴스", "news_last", "news"),
                           ("블로그", "blog_last", "blog"), ("카페(수집일)", "cafe_last", "cafe"),
                           ("공시", "disclosure_last", "disclosure"), ("검색 관심도", "trend_last", "trend"),
                           ("언급량 스냅샷", "mention_last", "mention"), ("장중 펄스", "pulse_last", "pulse"),
                           ("5개 관점 지표", "feature_as_of", "feature")):
        last = latest(key)
        lines.append(f"  {label:<14}{_s(last):<14}{freshness(last, today, FRESH_DAYS[fk])}")

    lines += ["", "[새 수집] 표별 (최신성 = 마지막 수집 시각, 주 1회 작업은 10일 기준)"]
    for t in st.get("new_collections", []):
        if t.get("error"):
            lines.append(f"  {t['label']:<22}조회 실패: {t['error']}")
            continue
        got = t["last_collected"]
        got = got.date() if hasattr(got, "date") else got
        lines.append(f"  {t['label']:<22}{t['rows']:>7}행  종목 {t['companies']:>3}개  기준일 {_s(t['last_date'])}  "
                     f"수집 {_s(got)}  {freshness(got, today, FRESH_DAYS[t['freq']])}")
    if not st.get("new_collections"):
        lines.append("  없음 (표가 없거나 아직 수집 전)")
    lines += ["", "[시장 지표] (시장 전체 합계, 종목별 아님; 최신성은 평일 2일 기준)"]
    lines += [f"  {m['label']:<20}조회 실패: {m['error']}" if m.get("error") else
              f"  {m['label']:<20}{m['rows']:>6}행  최신 기준일 {_s(m['last_date'])}  "
              f"{market_freshness(m['last_date'], today)}" for m in st.get("market_indicators", [])] or ["  없음 (collect-credit-balance)"]
    lines += ["", "[시장 지수]"]
    lines += [f"  {i['symbol']:<7}{(i['name'] or ''):<14}{i['rows']:>5}행 {i['from']}~{i['to']}  "
              f"{freshness(i['to'], today, FRESH_DAYS['index'])}" for i in st["indices"]] or ["  없음 (collect-indices)"]
    lines += ["", "[거시지표]"]
    lines += [f"  {m['series_id']:<16}{(m['name'] or '')[:22]:<24}{m['rows']:>6}행 {_s(m['from'])}~{_s(m['to'])}  "
              f"{freshness(m['to'], today, FRESH_DAYS['macro_D' if m['frequency'] == 'D' else 'macro_M'])}"
              for m in st["macro"]] or ["  없음 (collect-macro)"]
    lines += ["", "[문서] 출처별 (청크 = RAG 임베딩 적재 수, 대상 4개 소스만)"]
    lines += [f"  {d['source']:<12}{d['docs']:>7}건  종목 {d['companies']:>2}개  {_s(d['from'])}~{_s(d['to'])}  청크 {_s(d['chunks'])}"
              for d in st["documents"]] or ["  없음 (collect-news / collect-community)"]
    lines += ["", "[공시] 출처·유형별 (미분류 = event_type 키워드 미매칭)"]
    lines += [f"  {d['source']:<10}{(d['category'] or ''):<6}{d['rows']:>6}건  미분류 {d['unclassified']:>4}  {_s(d['from'])}~{_s(d['to'])}"
              for d in st["disclosures"]] or ["  없음 (collect-disclosures)"]
    lines += ["", "[검색 관심도 주제]"]
    lines += [f"  {t['subject']:<18}{t['days']:>4}일  ~{t['to']}" for t in st["trend_topics"]] or ["  없음 (collect-trends)"]
    lines += ["", "[Peer Group]"]
    lines += [f"  {p['market']}:{p['ticker']:<8}{p['method']:<22}{p['members']}개" for p in st["peer_groups"]] \
        or ["  없음 (build-peer-groups)"]
    return "\n".join(lines)


def run(json_path: str | None = None) -> int:
    with db.connect() as conn:
        st = collect_all(conn)
    print(render(st))
    if json_path:
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(st, fh, ensure_ascii=False, indent=2, default=str)
        print(f"\n[OK]   {json_path} 저장")
    return 0


if __name__ == "__main__":  # python -m kic_data.status [--json status.json]
    import sys

    args = sys.argv[1:]
    sys.exit(run(args[args.index("--json") + 1] if "--json" in args else None))
