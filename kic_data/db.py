"""DB 접근은 이 모듈 한 곳으로 모읍니다 (나중에 다른 DB로 옮길 때 여기만 수정).

psycopg 는 함수 안에서 불러오므로, 파서·테스트는 psycopg 없이도 import 됩니다.
"""
from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path

from .config import env

# 웹 실행기(kic_data/jobs.py)가 한 번에 일부 종목만 처리할 때 쓰는 필터. None 이면 전체.
ONLY_TICKERS: ContextVar[frozenset | None] = ContextVar("ONLY_TICKERS", default=None)

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def connect():
    import psycopg

    # keepalive: 수집 API 를 오래 기다리는 동안 쉬는 연결이 중간에서 끊기지 않게
    return psycopg.connect(env("DATABASE_URL", required=True), connect_timeout=30,
                           keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5)


def apply_schema(conn) -> list[str]:
    """sql/*.sql 을 번호순으로 실행 (모두 IF NOT EXISTS 라 여러 번 실행해도 안전)."""
    applied = []
    for path in sorted(SQL_DIR.glob("*.sql")):
        with conn.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))
        conn.commit()
        applied.append(path.name)
    return applied


def get_or_create_company(conn, market: str, ticker: str, name: str | None = None, **extra) -> int:
    cols = {k: v for k, v in extra.items() if k in ("isin", "corp_code", "cik", "sector") and v}
    sets = ", ".join(f"{k} = EXCLUDED.{k}" for k in cols)
    sets = (", " + sets) if sets else ""
    names = ", ".join(["market", "ticker", "name", *cols])
    marks = ", ".join(["%s"] * (3 + len(cols)))
    sql = (
        f"INSERT INTO core.company ({names}) VALUES ({marks}) "
        f"ON CONFLICT (market, ticker) DO UPDATE SET name = COALESCE(EXCLUDED.name, core.company.name){sets} "
        "RETURNING company_id"
    )
    with conn.cursor() as cur:
        cur.execute(sql, [market, ticker, name, *cols.values()])
        company_id = cur.fetchone()[0]
    conn.commit()
    return company_id


def list_companies(conn, market: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT company_id, ticker, name, corp_code, cik FROM core.company WHERE market = %s ORDER BY ticker",
            [market],
        )
        rows = cur.fetchall()
    only = ONLY_TICKERS.get()
    return rows if only is None else [r for r in rows if (market, r[1]) in only]


def upsert_prices(conn, company_id: int, rows: list[dict], source: str) -> int:
    sql = (
        "INSERT INTO core.price_daily (company_id, trade_date, open, high, low, close, volume, trade_value, market_cap, source) "
        "VALUES (%(company_id)s, %(trade_date)s, %(open)s, %(high)s, %(low)s, %(close)s, %(volume)s, "
        "%(trade_value)s, %(market_cap)s, %(source)s) "
        "ON CONFLICT (company_id, trade_date) DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high, "
        "low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume, trade_value=EXCLUDED.trade_value, "
        "market_cap=EXCLUDED.market_cap, source=EXCLUDED.source, collected_at=now()"
    )
    params = [{**r, "company_id": company_id, "source": source} for r in rows]
    with conn.cursor() as cur:
        cur.executemany(sql, params)
    conn.commit()
    return len(params)


def upsert_documents(conn, company_id: int, source: str, rows: list[dict], keep_first: bool = False) -> int:
    """rag.document 에 뉴스·공시 메타데이터를 적재 (company_id+source+url 기준 갱신).
    rows: [{title, url, published_at, summary}]  ※ sql/007 의 유니크 인덱스 필요
    keep_first=True 면 이미 있는 글은 그대로 둡니다 (카페글: 처음 수집한 날짜 유지)."""
    conflict = ("DO NOTHING" if keep_first else
                "DO UPDATE SET title=EXCLUDED.title, published_at=EXCLUDED.published_at, summary=EXCLUDED.summary")
    sql = (
        "INSERT INTO rag.document (company_id, source, title, url, published_at, summary) "
        "VALUES (%(company_id)s, %(source)s, %(title)s, %(url)s, %(published_at)s, %(summary)s) "
        f"ON CONFLICT (company_id, source, url) {conflict}"
    )
    params = [{**r, "company_id": company_id, "source": source} for r in rows]
    with conn.cursor() as cur:
        cur.executemany(sql, params)
    conn.commit()
    return len(params)


def docs_without_chunks(conn, source: str, limit: int | None = None) -> list[tuple]:
    """아직 청크가 없는 문서 → [(doc_id, title, summary)]"""
    sql = (
        "SELECT d.doc_id, d.title, d.summary FROM rag.document d "
        "LEFT JOIN rag.chunk c ON c.doc_id = d.doc_id "
        "WHERE d.source = %s AND c.doc_id IS NULL ORDER BY d.doc_id"
    )
    params: list = [source]
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def insert_chunks(conn, rows: list[tuple]) -> int:
    """rows: [(doc_id, chunk_idx, content, '[0.1,...]')]  (임베딩은 rag.vec_literal 결과)"""
    sql = (
        "INSERT INTO rag.chunk (doc_id, chunk_idx, content, embedding) VALUES (%s, %s, %s, %s::vector) "
        "ON CONFLICT (doc_id, chunk_idx) DO UPDATE SET content = EXCLUDED.content, embedding = EXCLUDED.embedding"
    )
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()
    return len(rows)


def upsert_index_rows(conn, symbol: str, name: str, rows: list[dict], source: str) -> int:
    sql = (
        "INSERT INTO core.market_index_daily (symbol, trade_date, name, open, high, low, close, volume, source) "
        "VALUES (%(symbol)s, %(trade_date)s, %(name)s, %(open)s, %(high)s, %(low)s, %(close)s, %(volume)s, %(source)s) "
        "ON CONFLICT (symbol, trade_date) DO UPDATE SET name=EXCLUDED.name, open=EXCLUDED.open, high=EXCLUDED.high, "
        "low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume, source=EXCLUDED.source, collected_at=now()"
    )
    params = [{**r, "symbol": symbol, "name": name, "source": source} for r in rows]
    with conn.cursor() as cur:
        cur.executemany(sql, params)
    conn.commit()
    return len(params)


def upsert_etf_prices(conn, ticker: str, name: str, rows: list[dict], source: str) -> int:
    sql = (
        "INSERT INTO core.etf_price_daily (ticker, trade_date, name, close, nav, volume, trade_value, market_cap, "
        "net_assets, base_index, source) VALUES (%(ticker)s, %(trade_date)s, %(name)s, %(close)s, %(nav)s, %(volume)s, "
        "%(trade_value)s, %(market_cap)s, %(net_assets)s, %(base_index)s, %(source)s) "
        "ON CONFLICT (ticker, trade_date) DO UPDATE SET name=EXCLUDED.name, close=EXCLUDED.close, nav=EXCLUDED.nav, "
        "volume=EXCLUDED.volume, trade_value=EXCLUDED.trade_value, market_cap=EXCLUDED.market_cap, "
        "net_assets=EXCLUDED.net_assets, base_index=EXCLUDED.base_index, source=EXCLUDED.source, collected_at=now()"
    )
    params = [{**r, "ticker": ticker, "name": r.get("name") or name, "source": source} for r in rows]
    with conn.cursor() as cur:
        cur.executemany(sql, params)
    conn.commit()
    return len(params)


def upsert_disclosures(conn, company_id: int, rows: list[dict], source: str) -> int:
    sql = (
        "INSERT INTO core.disclosure (rcept_no, company_id, report_nm, rcept_dt, filer, source, category, event_type, url) "
        "VALUES (%(rcept_no)s, %(company_id)s, %(report_nm)s, %(rcept_dt)s, %(filer)s, %(source)s, %(category)s, %(event_type)s, %(url)s) "
        "ON CONFLICT (rcept_no) DO UPDATE SET report_nm=EXCLUDED.report_nm, category=EXCLUDED.category, "
        "event_type=EXCLUDED.event_type, url=EXCLUDED.url, collected_at=now()"
    )
    params = [{**r, "company_id": company_id, "source": source} for r in rows]
    with conn.cursor() as cur:
        cur.executemany(sql, params)
    conn.commit()
    return len(params)


def upsert_search_trend(conn, rows: list[dict], source: str) -> int:
    sql = (
        "INSERT INTO core.search_trend (subject, keyword, trade_date, ratio, anchor_ratio, source) "
        "VALUES (%(subject)s, %(keyword)s, %(trade_date)s, %(ratio)s, %(anchor_ratio)s, %(source)s) "
        "ON CONFLICT (subject, trade_date, source) DO UPDATE SET keyword=EXCLUDED.keyword, ratio=EXCLUDED.ratio, "
        "anchor_ratio=EXCLUDED.anchor_ratio, collected_at=now()"
    )
    params = [{**r, "source": source} for r in rows]
    with conn.cursor() as cur:
        cur.executemany(sql, params)
    conn.commit()
    return len(params)


def fetch_search_trend(conn, subject: str, days: int = 120) -> list[tuple]:
    """[(날짜, ratio, anchor_ratio)] 오름차순, 최근 days 일."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT trade_date, ratio, anchor_ratio FROM core.search_trend WHERE subject = %s "
            "AND trade_date >= current_date - %s ORDER BY trade_date", [subject, days])
        return cur.fetchall()


SENTIMENT_SOURCES = ("naver_news", "naver_blog", "naver_cafe", "youtube_comment")


def docs_without_sentiment(conn, method: str, limit: int | None = None) -> list[tuple]:
    """[(doc_id, title, summary)] 아직 해당 방식으로 점수가 없는 뉴스·블로그·카페글."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT d.doc_id, d.title, d.summary FROM rag.document d "
            "LEFT JOIN core.doc_sentiment s ON s.doc_id = d.doc_id AND s.method = %s "
            "WHERE d.source = ANY(%s) AND s.doc_id IS NULL ORDER BY d.doc_id "
            + ("LIMIT %s" if limit else ""),
            [method, list(SENTIMENT_SOURCES)] + ([limit] if limit else []))
        return cur.fetchall()


def upsert_sentiment(conn, rows: list[tuple]) -> int:
    """rows: [(doc_id, score, label, method)]"""
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO core.doc_sentiment (doc_id, score, label, method) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (doc_id) DO UPDATE SET score=EXCLUDED.score, label=EXCLUDED.label, "
            "method=EXCLUDED.method, scored_at=now()", rows)
    conn.commit()
    return len(rows)


def upsert_youtube_videos(conn, company_id: int, query: str, videos: list[dict], stats: dict[str, dict]) -> int:
    """core.youtube_video 갱신 (조회수 등 통계는 다시 수집할 때마다 최신 값으로)."""
    sql = (
        "INSERT INTO core.youtube_video (video_id, company_id, query, title, channel, published_at, "
        "view_count, like_count, comment_count) VALUES (%(video_id)s, %(company_id)s, %(query)s, %(title)s, "
        "%(channel)s, %(published_at)s, %(view_count)s, %(like_count)s, %(comment_count)s) "
        "ON CONFLICT (video_id) DO UPDATE SET title=EXCLUDED.title, view_count=EXCLUDED.view_count, "
        "like_count=EXCLUDED.like_count, comment_count=EXCLUDED.comment_count, collected_at=now()"
    )
    empty = {"view_count": None, "like_count": None, "comment_count": None}
    params = [{**v, **stats.get(v["video_id"], empty), "company_id": company_id, "query": query} for v in videos]
    with conn.cursor() as cur:
        cur.executemany(sql, params)
    conn.commit()
    return len(params)


def fetch_reactions(conn, company_id: int, since) -> list[dict]:
    """[{source, published_at, score}] 뉴스·블로그·카페글 (점수 없으면 score=None)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT d.source, d.published_at, s.score FROM rag.document d "
            "LEFT JOIN core.doc_sentiment s USING (doc_id) "
            "WHERE d.company_id = %s AND d.source = ANY(%s) AND d.published_at >= %s",
            [company_id, list(SENTIMENT_SOURCES), since])
        return [{"source": r[0], "published_at": r[1], "score": None if r[2] is None else float(r[2])}
                for r in cur.fetchall()]


def insert_new_documents(conn, company_id: int, source: str, rows: list[dict]) -> int:
    """이미 있는 글은 건드리지 않고 새 글만 넣고, 새로 들어간 건수를 돌려줍니다."""
    sql = (
        "INSERT INTO rag.document (company_id, source, title, url, published_at, summary) "
        "VALUES (%(company_id)s, %(source)s, %(title)s, %(url)s, %(published_at)s, %(summary)s) "
        "ON CONFLICT (company_id, source, url) DO NOTHING RETURNING doc_id"
    )
    n = 0
    with conn.cursor() as cur:
        for r in rows:
            cur.execute(sql, {**r, "company_id": company_id, "source": source})
            n += cur.fetchone() is not None
    conn.commit()
    return n


PULSE_COLS = ("trade_date", "n_today", "n_news", "n_blog", "n_positive", "n_negative", "sentiment",
              "mentions_vs_30d", "new_docs", "method", "new_since_last")


def insert_mention_counts(conn, company_id: int, captured_at, trade_date, totals: dict) -> None:
    """totals: {source: (query, total)}"""
    rows = [(company_id, src, q, captured_at, trade_date, t) for src, (q, t) in totals.items() if t is not None]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO core.mention_count (company_id, source, query, captured_at, trade_date, total) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", rows)
    conn.commit()


def last_mention_totals(conn, company_id: int, trade_date) -> dict:
    """같은 날 마지막 스냅샷의 {source: total}."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (source) source, total FROM core.mention_count "
            "WHERE company_id = %s AND trade_date = %s ORDER BY source, captured_at DESC", [company_id, trade_date])
        return dict(cur.fetchall())


def fetch_mention_daily(conn, company_id: int, days: int = 45) -> dict:
    """{source: [(날짜, 그날 마지막 total)]} 오름차순."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (source, trade_date) source, trade_date, total FROM core.mention_count "
            "WHERE company_id = %s AND trade_date >= current_date - %s ORDER BY source, trade_date, captured_at DESC",
            [company_id, days])
        out: dict = {}
        for src, d, t in cur.fetchall():
            out.setdefault(src, []).append((d, int(t)))
        return out


def insert_pulse(conn, company_id: int, snap: dict) -> None:
    cols = ("company_id", "captured_at") + PULSE_COLS
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO core.reaction_pulse ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
            "ON CONFLICT DO NOTHING",
            [company_id, snap["captured_at"]] + [snap.get(c) for c in PULSE_COLS])
    conn.commit()


def last_pulse(conn, company_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(f"SELECT captured_at, {', '.join(PULSE_COLS)} FROM core.reaction_pulse "
                    "WHERE company_id = %s ORDER BY captured_at DESC LIMIT 1", [company_id])
        r = cur.fetchone()
    if not r:
        return None
    out = dict(zip(("captured_at",) + PULSE_COLS, r))
    from zoneinfo import ZoneInfo

    out["captured_at"] = out["captured_at"].astimezone(ZoneInfo("Asia/Seoul"))
    for k in ("sentiment", "mentions_vs_30d"):
        out[k] = None if out[k] is None else float(out[k])
    return out


def pulse_history(conn, company_id: int, trade_date) -> list[dict]:
    """오늘 스냅샷 추이 [{time, sentiment, n_today}] (대시보드 선 그래프용)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT captured_at, sentiment, n_today, n_positive, n_negative, new_since_last FROM core.reaction_pulse "
            "WHERE company_id = %s AND trade_date = %s ORDER BY captured_at", [company_id, trade_date])
        from zoneinfo import ZoneInfo

        kst = ZoneInfo("Asia/Seoul")  # DB 는 UTC 로 돌려주므로 한국 시간으로 변환
        return [{"time": r[0].astimezone(kst).isoformat(), "sentiment": None if r[1] is None else float(r[1]),
                 "n_today": r[2], "n_positive": r[3], "n_negative": r[4], "new_since_last": r[5]}
                for r in cur.fetchall()]


def fetch_news_dates(conn, company_id: int, since) -> list:
    """네이버 뉴스 게시일 목록 (기사 1건 = 1개)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT published_at FROM rag.document WHERE company_id = %s AND source = 'naver_news' "
            "AND published_at >= %s", [company_id, since])
        return [r[0] for r in cur.fetchall()]


def upsert_facts(conn, company_id: int, rows: list[dict], source: str) -> int:
    sql = (
        "INSERT INTO core.financial_fact (company_id, source, fiscal_year, report_code, fs_div, sj_div, "
        "account_id, account_nm, amount, amount_ytd, currency, rcept_no, period_end) "
        "VALUES (%(company_id)s, %(source)s, %(fiscal_year)s, %(report_code)s, %(fs_div)s, %(sj_div)s, "
        "%(account_id)s, %(account_nm)s, %(amount)s, %(amount_ytd)s, %(currency)s, %(rcept_no)s, %(period_end)s) "
        "ON CONFLICT (company_id, source, fiscal_year, report_code, fs_div, sj_div, account_id, account_nm) "
        "DO UPDATE SET amount=EXCLUDED.amount, amount_ytd=EXCLUDED.amount_ytd, currency=EXCLUDED.currency, "
        "rcept_no=EXCLUDED.rcept_no, period_end=EXCLUDED.period_end, collected_at=now()"
    )
    defaults = {"amount_ytd": None, "period_end": None}
    params = [{**defaults, **r, "company_id": company_id, "source": source} for r in rows]
    with conn.cursor() as cur:
        cur.executemany(sql, params)
    conn.commit()
    return len(params)


def fetch_facts(conn, company_id: int, source: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT fiscal_year, report_code, account_id, amount, amount_ytd, currency, period_end "
            "FROM core.financial_fact WHERE company_id = %s AND source = %s "
            "AND (source <> 'dart' OR (fs_div = 'CFS' AND sj_div IN ('BS', 'IS', 'CIS')))",  # 자본변동표·현금흐름표 제외
            [company_id, source],
        )
        cols = ["fiscal_year", "report_code", "account_id", "amount", "amount_ytd", "currency", "period_end"]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def upsert_quarters(conn, company_id: int, rows: list[dict], source: str) -> int:
    sql = (
        "INSERT INTO core.financial_quarter (company_id, period_end, fiscal_year, fiscal_q, metric, amount, currency, source) "
        "VALUES (%(company_id)s, %(period_end)s, %(fiscal_year)s, %(fiscal_q)s, %(metric)s, %(amount)s, %(currency)s, %(source)s) "
        "ON CONFLICT (company_id, period_end, metric) DO UPDATE SET fiscal_year=EXCLUDED.fiscal_year, "
        "fiscal_q=EXCLUDED.fiscal_q, amount=EXCLUDED.amount, currency=EXCLUDED.currency, source=EXCLUDED.source"
    )
    params = [{**r, "company_id": company_id, "source": source} for r in rows]
    with conn.cursor() as cur:
        cur.executemany(sql, params)
    conn.commit()
    return len(params)


def upsert_macro(conn, series_id: str, name: str, source: str, frequency: str, rows: list[tuple]) -> int:
    """rows: [(date, Decimal|None), ...]"""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO core.macro_series (series_id, name, source, frequency) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (series_id) DO UPDATE SET name=EXCLUDED.name, frequency=EXCLUDED.frequency",
            [series_id, name, source, frequency],
        )
        cur.executemany(
            "INSERT INTO core.macro_obs (series_id, obs_date, value, source) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (series_id, obs_date) DO UPDATE SET value=EXCLUDED.value, collected_at=now()",
            [(series_id, d, v, source) for d, v in rows],
        )
    conn.commit()
    return len(rows)


def update_company_info(conn, company_id: int, sector=None, sector_desc=None, shares=None, shares_asof=None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE core.company SET sector = COALESCE(%s, sector), sector_desc = COALESCE(%s, sector_desc), "
            "shares_outstanding = COALESCE(%s, shares_outstanding), shares_asof = COALESCE(%s, shares_asof) "
            "WHERE company_id = %s",
            [sector or None, sector_desc or None, shares, shares_asof, company_id],
        )
    conn.commit()


def create_peer_group(conn, base_company_id: int, method: str, members: list[tuple]) -> int:
    """같은 기준 종목·방법의 기존 그룹을 지우고 다시 만듭니다. members: [(company_id, rank, reason)]"""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM core.peer_group WHERE base_company_id = %s AND method = %s", [base_company_id, method])
        cur.execute(
            "INSERT INTO core.peer_group (base_company_id, method) VALUES (%s, %s) RETURNING peer_group_id",
            [base_company_id, method],
        )
        gid = cur.fetchone()[0]
        cur.executemany(
            "INSERT INTO core.peer_member (peer_group_id, company_id, rank, reason) VALUES (%s,%s,%s,%s)",
            [(gid, cid, rank, reason) for cid, rank, reason in members],
        )
    conn.commit()
    return gid


def find_company(conn, market: str, ticker: str):
    with conn.cursor() as cur:
        cur.execute("SELECT company_id, name FROM core.company WHERE market=%s AND ticker=%s", [market, ticker])
        return cur.fetchone()


def market_caps(conn) -> dict[int, float]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.company_id, v.market_cap FROM core.company c JOIN core.v_valuation v "
            "ON v.market = c.market AND v.ticker = c.ticker WHERE v.market_cap IS NOT NULL"
        )
        return {r[0]: float(r[1]) for r in cur.fetchall()}


def fetch_snapshots(conn) -> dict:
    """v_valuation + v_ttm 병합: {(market, ticker): {...}}"""
    sql = (
        "SELECT v.market, v.ticker, c.company_id, v.close, v.market_cap, v.fiscal_year, v.fin_currency, "
        "v.per AS per_annual, v.pbr AS pbr_annual, v.op_margin AS op_margin_annual, v.roe AS roe_annual, "
        "v.debt_ratio AS debt_ratio_annual, v.revenue AS revenue_annual, v.net_income AS net_income_annual, "
        "v.equity AS equity_annual, t.ttm_end, t.revenue_ttm, t.operating_income_ttm, t.net_income_ttm, "
        "t.per_ttm, t.pbr, t.op_margin_ttm, t.roe_ttm, t.debt_ratio AS debt_ratio_ttm, t.currency AS ttm_currency, "
        "c.name "
        "FROM core.v_valuation v JOIN core.company c ON c.market = v.market AND c.ticker = v.ticker "
        "LEFT JOIN core.v_ttm t ON t.market = v.market AND t.ticker = v.ticker"
    )
    with conn.cursor() as cur:
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        return {(r[0], r[1]): dict(zip(cols, r)) for r in cur.fetchall()}


def fetch_prices(conn, company_id: int, n: int = 300) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT trade_date, close, volume FROM core.price_daily WHERE company_id = %s AND close IS NOT NULL "
            "ORDER BY trade_date DESC LIMIT %s", [company_id, n])
        rows = [{"trade_date": r[0], "close": r[1], "volume": r[2]} for r in cur.fetchall()]
    return rows[::-1]


def fetch_quarters(conn, company_id: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT period_end, fiscal_year, fiscal_q, metric, amount FROM core.financial_quarter WHERE company_id = %s", [company_id])
        return [dict(zip(["period_end", "fiscal_year", "fiscal_q", "metric", "amount"], r)) for r in cur.fetchall()]


def fetch_macro_all(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT series_id, obs_date, value FROM core.macro_obs ORDER BY series_id, obs_date")
        out: dict = {}
        for sid, d, v in cur.fetchall():
            out.setdefault(sid, []).append((d, v))
        return out


def replace_features(conn, company_id: int, as_of, rows: list[dict]) -> int:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM core.feature_snapshot WHERE company_id = %s AND as_of = %s", [company_id, as_of])
        cur.executemany(
            "INSERT INTO core.feature_snapshot (company_id, as_of, perspective, feature, value, unit, note) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            [(company_id, as_of, r["perspective"], r["feature"], r["value"], r["unit"], r["note"]) for r in rows],
        )
    conn.commit()
    return len(rows)


def read_features(conn, as_of=None) -> dict:
    """{(market, ticker): {"name":..., "as_of":..., perspective: {feature: {value, unit, note}}}} (as_of 생략 시 최신)"""
    with conn.cursor() as cur:
        cur.execute("SELECT max(as_of) FROM core.feature_snapshot")
        as_of = as_of or cur.fetchone()[0]
        if as_of is None:
            return {}
        cur.execute(
            "SELECT c.market, c.ticker, c.name, f.perspective, f.feature, f.value, f.unit, f.note "
            "FROM core.feature_snapshot f JOIN core.company c USING (company_id) WHERE f.as_of = %s "
            "ORDER BY c.market, c.ticker, f.perspective, f.feature", [as_of])
        out: dict = {}
        for m, t, n, p, feat, v, u, note in cur.fetchall():
            d = out.setdefault((m, t), {"name": n, "as_of": str(as_of)})
            d.setdefault(p, {})[feat] = {"value": None if v is None else float(v), "unit": u, "note": note}
        return out
