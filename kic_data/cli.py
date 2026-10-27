"""명령줄 도구.  사용: python -m kic_data.cli <명령> [옵션]

명령
  check-db         DB 연결·pgvector 확인
  apply-schema     sql/*.sql 적용
  check-sources    각 API 키가 동작하는지 1회씩 호출해 확인
  status           수집 현황 점검 (읽기 전용: 테이블별 건수·기간·최신성, --json 으로 파일 저장)
  seed-companies   universe.py 의 종목을 core.company 에 등록
  sync-dart-codes  DART 고유번호를 KR 종목에 연결
  collect-company-info  업종코드·발행주식수 채우기
  build-peer-groups     universe.py 의 PEER_THEMES 로 Peer Group 생성
  show-valuation        PER/PBR/영업이익률/ROE 비교표 출력 (연간 기준)
  collect-dart-q / collect-sec-q  분기 재무 수집
  build-quarterly       분기 단독 값 도출 → core.financial_quarter
  show-ttm              TTM(최근 4분기) 기준 비교표
  build-features        5개 관점 지표 계산 → core.feature_snapshot
  show-features TICKER  종목 하나의 관점별 지표 보기
  export-features       에이전트 입력용 features.json 내보내기
  collect-news     네이버 뉴스(제목·요약·링크)를 rag.document 에 적재
  collect-indices  시장 지수(코스피·코스닥·S&P500·나스닥·SOX·VIX) 일봉
  collect-disclosures  DART(정기·주요사항·거래소공시)·SEC 제출서류 목록
  collect-customs  관세청 반도체(HS) 월별 수출입
  collect-trends   네이버 데이터랩 검색 관심도
  collect-community  개인 투자자 반응: 네이버 블로그·카페글 (공식 검색 API)
  pulse            장중 여론 펄스: 오늘 뉴스·블로그 톤 스냅샷 (15분 스케줄용, --tickers 로 일부만)
  show-pulse TICKER  오늘 여론 추이 출력
  score-sentiment  뉴스·블로그·카페글 제목+요약 감성 점수 → core.doc_sentiment
  daily-update     위 수집을 한 번에 최근분만 갱신 + 지표 재계산·features.json (스케줄러용)
  embed-docs       청크가 없는 문서를 청킹·임베딩해 rag.chunk 에 적재
  collect-etf      국내 ETF 시세·NAV (금융위원회 증권상품시세정보, universe.py 의 ETF_UNIVERSE)
  collect-finnhub  해외 종목 영문 뉴스 (Finnhub, FINNHUB_API_KEY 있을 때만)
  collect-youtube  유튜브 공개 영상·통계·공개 댓글 (YOUTUBE_API_KEY 있을 때만)
  collect-investor-flows  국내 종목 투자자별(외국인·기관·개인) 일별 순매수 (네이버 증권 공개 API)
  collect-lending  코스피 종목별 일별 주식 대차거래·잔고 (금융위원회 주식대차거래정보, 공매도 대용)
  collect-kr-prices / collect-us-prices / collect-dart / collect-sec / collect-macro
  tick             시간표(kic_data/jobs.py)상 차례가 된 작업을 실행 (GitHub Actions 매시간 실행용)

자동 실행은 kic_data/jobs.py 가 이 명령들을 시간표대로 호출합니다.
GitHub Actions 로 돌리는 방법은 docs/AUTO_RUN.md, 웹 대시보드(Vercel 등)는 docs/DEPLOY_VERCEL.md.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from . import db, features, krx_sector, pulse, quarterly, rag, sentiment, status
from .collectors import (community, customs, dart, etf, finnhub, fsc, indices, investor_flow, kospi200, lending, macro,
                         news, sec, trends, us_prices, youtube)
from .config import env
from .http_util import make_session
from .universe import ETF_UNIVERSE, KR_UNIVERSE, PEER_THEMES, US_UNIVERSE
from .util import date_windows, drop_incomplete_today, ymd


def _ok(label: str, detail: str = "") -> None:
    print(f"[OK]   {label} {detail}".rstrip())


def _fail(label: str, detail: str) -> None:
    print(f"[FAIL] {label}: {detail}")


def cmd_check_db(args) -> int:
    try:
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute("select version()")
            _ok("DB 연결", cur.fetchone()[0].split(",")[0])
            cur.execute("select extname from pg_extension where extname = 'vector'")
            if cur.fetchone():
                _ok("pgvector", "설치됨")
            else:
                print("[WARN] pgvector 미설치 → apply-schema 를 실행하세요")
        return 0
    except Exception as exc:  # 접속 문자열이 메시지에 섞이지 않도록 형식만 출력
        _fail("DB 연결", type(exc).__name__ + " (DATABASE_URL, 네트워크, Neon 프로젝트 상태 확인)")
        return 1


def cmd_apply_schema(args) -> int:
    with db.connect() as conn:
        for name in db.apply_schema(conn):
            _ok("적용", name)
    return 0


def cmd_check_sources(args) -> int:
    s = make_session()
    bad = 0
    today = date.today()
    checks = []
    if env("DART_API_KEY"):
        checks.append(("OpenDART", lambda: dart.fetch_financials(s, "00126380", today.year - 2)))
    if env("FSC_API_KEY"):
        checks.append(("FSC 시세", lambda: fsc.fetch_prices(s, "005930", ymd(today - timedelta(days=14)), ymd(today), 5)))
    if env("ECOS_API_KEY"):
        sid, _, stat, cyc, item = macro.ECOS_SERIES[0]
        checks.append(("ECOS", lambda: macro.fetch_ecos(s, stat, cyc, (today - timedelta(days=400)).strftime("%Y%m"), today.strftime("%Y%m"), item)))
    if env("FRED_API_KEY"):
        checks.append(("FRED", lambda: macro.fetch_fred(s, "DGS10", (today - timedelta(days=30)).isoformat())))
    if env("NAVER_CLIENT_ID") and env("NAVER_CLIENT_SECRET"):
        checks.append(("네이버 뉴스", lambda: news.fetch_news(s, "삼성전자", display=5)))
    if (env("NCP_APIHUB_KEY_ID") and env("NCP_APIHUB_KEY")) or env("NAVER_CLIENT_ID"):
        label = "검색어 트렌드(HUB)" if env("NCP_APIHUB_KEY_ID") else "검색어 트렌드(개발자센터)"
        checks.append((label, lambda: trends.fetch_trend(s, today - timedelta(days=8), today - timedelta(days=1),
                                                         [("t", ["주식"])])))
    if env("SEC_USER_AGENT"):
        checks.append(("SEC EDGAR", lambda: sec.fetch_ticker_map(make_session())))
    if not checks:
        print("확인할 키가 없습니다. .env 를 채우세요.")
        return 1
    for label, fn in checks:
        try:
            result = fn()
            n = len(result)
            _ok(label, f"{n}건 수신")
            if n == 0:
                print("       (0건: 키는 통과했지만 코드/기간을 확인하세요)")
        except Exception as exc:
            bad += 1
            _fail(label, str(exc))
    missing = [k for k in ("DART_API_KEY", "FSC_API_KEY", "ECOS_API_KEY", "FRED_API_KEY", "SEC_USER_AGENT", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET") if not env(k)]
    if missing:
        print("미설정:", ", ".join(missing))
    return 1 if bad else 0


def cmd_status(args) -> int:
    return status.run(args.json)


def cmd_seed(args) -> int:
    with db.connect() as conn:
        for t, n in KR_UNIVERSE:
            db.get_or_create_company(conn, "KR", t, n)
        for t, n in US_UNIVERSE:
            db.get_or_create_company(conn, "US", t, n)
    _ok("종목 등록", f"KR {len(KR_UNIVERSE)} / US {len(US_UNIVERSE)}")
    return 0


def cmd_sync_dart(args) -> int:
    s = make_session()
    from . import jobs

    codes = {c["stock_code"]: c for c in dart.fetch_corp_codes(s)}
    newly = []
    with db.connect() as conn:
        for _, ticker, name, corp_code, _ in db.list_companies(conn, "KR"):
            c = codes.get(ticker)
            if c:
                db.get_or_create_company(conn, "KR", ticker, name, corp_code=c["corp_code"])
                _ok(ticker, c["corp_code"])
                if not corp_code:
                    newly.append(ticker)
            else:
                print(f"[WARN] {ticker} DART 상장사 목록에 없음")
        if newly:
            # 고유번호가 없던 종목은 업종·공시·재무 과거분을 못 받았으므로 다시 받게 함
            jobs.reset_backfills(conn, jobs.DART_FOLLOWUPS)
            print(f"[OK]   새로 연결 {len(newly)}종목 → 업종·공시·재무 과거분 다시 받기")
    return 0


BULK_PRICE_DAYS = 31  # 이 기간 이하면 종목별 호출 대신 날짜 범위 전 종목 조회 (KOSPI 200 기준 200회 → 수 회)


def cmd_kr_prices(args) -> int:
    s = make_session()
    end, begin = date.today(), date.today() - timedelta(days=args.days)
    bad = 0
    if args.days <= BULK_PRICE_DAYS:
        return _kr_prices_bulk(s, begin, end)
    with db.connect() as conn:
        for cid, ticker, *_ in db.list_companies(conn, "KR"):
            # 한 번에 최대 1000행이라 약 330일씩 나눠 호출 (5년치도 누락 없이)
            by_date = {}
            try:
                for a, b in date_windows(begin, end, 330):
                    for r in fsc.fetch_prices(s, ticker, ymd(a), ymd(b)):
                        by_date[r["trade_date"]] = r
            except RuntimeError as exc:  # 한 종목이 실패해도 나머지는 계속
                _fail(ticker, str(exc))
                bad += 1
                continue
            rows = drop_incomplete_today([by_date[d] for d in sorted(by_date)], "KR")
            n = db.upsert_prices(conn, cid, rows, "fsc")
            _ok(ticker, f"{n}행 (fsc)")
    return 1 if bad else 0


def _kr_prices_bulk(s, begin, end) -> int:
    with db.connect() as conn:
        companies = db.list_companies(conn, "KR")
        if not companies:
            return 0
        try:
            rows = fsc.fetch_all_prices(s, ymd(begin), ymd(end))
        except RuntimeError as exc:
            _fail("국내 시세(전 종목)", str(exc))
            return 1
        by_ticker: dict[str, dict] = {}
        for r in rows:
            if r.get("ticker"):
                by_ticker.setdefault(r["ticker"], {})[r["trade_date"]] = r
        missing = 0
        for cid, ticker, *_ in companies:
            got = by_ticker.get(ticker)
            if not got:
                print(f"[WARN] {ticker} 기간 내 시세 없음 (거래정지·상장폐지 확인)")
                missing += 1
                continue
            day_rows = drop_incomplete_today([got[d] for d in sorted(got)], "KR")
            n = db.upsert_prices(conn, cid, day_rows, "fsc")
            shares = fsc.latest_shares(day_rows)
            if shares:
                db.update_company_info(conn, cid, shares=shares[0], shares_asof=shares[1])
            _ok(ticker, f"{n}행 (fsc 전 종목 조회)")
    _ok("국내 시세", f"전 종목 {len(rows)}행 중 {len(companies) - missing}/{len(companies)}종목 반영")
    return 1 if missing > len(companies) // 10 else 0


def cmd_us_prices(args) -> int:
    begin = date.today() - timedelta(days=args.days)
    bad = 0
    with db.connect() as conn:
        for cid, ticker, *_ in db.list_companies(conn, "US"):
            try:
                rows = drop_incomplete_today(us_prices.fetch_prices(ticker, begin.isoformat()), "US")
            except Exception as exc:  # yfinance 는 비공식이라 일시 차단(429 등)이 잦음 → 다음 실행에서 다시
                _fail(ticker, f"{type(exc).__name__}: {str(exc)[:120]}")
                bad += 1
                continue
            n = db.upsert_prices(conn, cid, rows, "yfinance")
            _ok(ticker, f"{n}행 (yfinance)")
    return 1 if bad else 0


def cmd_news(args) -> int:
    s = make_session()
    since = date.today() - timedelta(days=args.days)
    with db.connect() as conn:
        for market in ("KR", "US"):
            for cid, ticker, name, *_ in db.list_companies(conn, market):
                query, must = news.terms_for(market, ticker, name)
                try:
                    rows = news.collect_company_news(s, query, must, since, args.max_items)
                except RuntimeError as exc:
                    _fail(f"{market}:{ticker}", str(exc))
                    continue
                _ok(f"{market}:{ticker}", f"{db.upsert_documents(conn, cid, 'naver_news', rows)}건 (검색어 '{query}')")
    return 0


def cmd_community(args) -> int:
    """개인 투자자 반응: 네이버 블로그·카페글 (공식 검색 API)."""
    s = make_session()
    since = date.today() - timedelta(days=args.days)
    sources = [x.strip() for x in args.sources.split(",") if x.strip()]
    bad = 0
    with db.connect() as conn:
        for market in ("KR", "US"):
            for cid, ticker, name, *_ in db.list_companies(conn, market):
                term, must = news.terms_for(market, ticker, name)
                parts = []
                for src in sources:
                    try:
                        rows = community.collect(s, src, term, must, since, pages=args.pages)
                    except RuntimeError as exc:
                        _fail(f"{market}:{ticker} {src}", str(exc))
                        bad += 1
                        continue
                    n = db.upsert_documents(conn, cid, src, rows, keep_first=(src == "naver_cafe"))
                    parts.append(f"{src.split('_')[1]} {n}")
                _ok(f"{market}:{ticker}", ", ".join(parts) + f" (검색어 '{term} 주가/주식')")
    return 1 if bad else 0


def cmd_sentiment(args) -> int:
    """점수 없는 뉴스·블로그·카페글에 감성 점수를 매깁니다 (제목+요약)."""
    method = args.method or env("SENTIMENT_METHOD") or "lexicon"
    with db.connect() as conn:
        if getattr(args, "rescore", False):  # 사전을 고친 뒤 전체 다시 채점
            with conn.cursor() as cur:
                cur.execute("DELETE FROM core.doc_sentiment WHERE method = %s", [method])
            conn.commit()
        docs = db.docs_without_sentiment(conn, method, args.limit)
        if not docs:
            _ok("감성 점수", "새로 매길 문서 없음")
            return 0
        total = 0
        for i in range(0, len(docs), 500):
            part = docs[i:i + 500]
            scores = sentiment.score_texts([f"{t or ''} {sm or ''}" for _, t, sm in part], method)
            total += db.upsert_sentiment(conn, [(doc_id, sc, sentiment.label_of(sc), method)
                                                for (doc_id, _, _), sc in zip(part, scores)])
        _ok("감성 점수", f"{total}건 ({method})")
    return 0


def cmd_pulse(args) -> int:
    """장중 여론 펄스: 종목별 오늘 뉴스·블로그 톤 스냅샷 (15분 스케줄 또는 수동)."""
    now = pulse.kst_now()
    if args.market_hours and not (now.weekday() < 5 and "08:50" <= now.strftime("%H:%M") <= "15:40"):
        print(f"정규장 시간이 아니라 건너뜀 ({now:%a %H:%M} KST). --market-hours 없이 실행하면 강제 실행.")
        return 0
    wanted = {t.strip().upper() for t in args.tickers.split(",")} if args.tickers else None
    method = env("SENTIMENT_METHOD") or "lexicon"
    s = make_session()
    bad = 0
    with db.connect() as conn:
        for market in ("KR", "US"):
            for cid, ticker, name, *_ in db.list_companies(conn, market):
                if wanted and ticker.upper() not in wanted:
                    continue
                try:
                    snap = pulse.refresh(conn, s, cid, market, ticker, name, method, args.max_age)
                except RuntimeError as exc:
                    _fail(f"{market}:{ticker}", str(exc))
                    bad += 1
                    continue
                tone = "-" if snap["sentiment"] is None else f"{snap['sentiment']:+.2f}"
                since = "-" if snap.get("new_since_last") is None else f"+{snap['new_since_last']}"
                _ok(f"{market}:{ticker}", f"오늘 {snap['n_today']}건(뉴스 {snap['n_news']}·블로그 {snap['n_blog']}, 신규 {snap['new_docs']}, 직전 이후 {since}) "
                    f"톤 {tone} 긍{snap['n_positive']}/부{snap['n_negative']}" + (" (캐시)" if snap["cached"] else ""))
    return 1 if bad else 0


def cmd_show_pulse(args) -> int:
    with db.connect() as conn:
        found = next(((m, f) for m in ("KR", "US") if (f := db.find_company(conn, m, args.ticker.upper()))), None)
        if not found:
            print("등록되지 않은 종목입니다.")
            return 1
        market, (cid, name) = found
        rows = db.pulse_history(conn, cid, pulse.kst_now().date())
    print(f"{market}:{args.ticker.upper()} {name} 오늘 여론 추이 ({len(rows)}회)")
    for r in rows:
        tone = "-" if r["sentiment"] is None else f"{r['sentiment']:+.2f}"
        new = "-" if r["new_since_last"] is None else f"+{r['new_since_last']}"
        print(f"  {r['time'][11:16]}  톤 {tone:>6}  오늘 글 {r['n_today']:>3}  긍 {r['n_positive']:>3} / 부 {r['n_negative']:>3}"
              f"  직전 이후 새 글 {new:>6}")
    return 0


def cmd_embed(args) -> int:
    embedder = rag.load_embedder(args.model)
    total = 0
    with db.connect() as conn:
        docs = db.docs_without_chunks(conn, args.source, args.limit)
        _ok("대상 문서", f"{len(docs)}건 (source={args.source})")
        for i in range(0, len(docs), args.batch):
            batch = docs[i : i + args.batch]
            items = [(doc_id, idx, text) for doc_id, title, summary in batch
                     for idx, text in enumerate(rag.build_chunks(title, summary))]
            if not items:
                continue
            vectors = embedder.encode([t for _, _, t in items])
            rows = [(d, k, t, rag.vec_literal(v)) for (d, k, t), v in zip(items, vectors)]
            total += db.insert_chunks(conn, rows)
            print(f"       {min(i + args.batch, len(docs))}/{len(docs)} 문서 처리")
    _ok("청크 적재", f"{total}건")
    return 0


def cmd_indices(args) -> int:
    start = (date.today() - timedelta(days=args.days)).isoformat()
    with db.connect() as conn:
        for symbol, name in indices.INDEX_SYMBOLS:
            try:
                rows = indices.fetch_index(symbol, start)
            except Exception as exc:
                _fail(symbol, type(exc).__name__)
                continue
            _ok(f"{symbol} {name}", f"{db.upsert_index_rows(conn, symbol, name, rows, 'yfinance')}행")
    return 0


def cmd_disclosures(args) -> int:
    s = make_session()
    since = date.today() - timedelta(days=args.days)
    types = [t.strip() for t in args.types.split(",") if t.strip()]
    tmap = None
    with db.connect() as conn:
        kr = db.list_companies(conn, "KR")
        if getattr(args, "bulk", False) and kr:
            _kr_disclosures_bulk(s, conn, kr, since, types)
            kr = []
        for cid, ticker, name, corp_code, cik in kr:
            if not corp_code:
                print(f"[WARN] {ticker} corp_code 없음 → sync-dart-codes 먼저")
                continue
            total = 0
            for ty in types:
                rows = dart.fetch_disclosures(s, corp_code, ymd(since), ymd(date.today()), ty)
                total += db.upsert_disclosures(conn, cid, rows, "dart")
            _ok(f"KR:{ticker}", f"{total}건 (유형 {','.join(types)})")
        for cid, ticker, name, _, cik in db.list_companies(conn, "US"):
            if not cik:
                tmap = tmap or sec.fetch_ticker_map(s)
                cik = tmap.get(ticker.upper())
            if not cik:
                print(f"[WARN] {ticker} CIK 없음")
                continue
            rows = sec.fetch_filings(s, cik, since)
            _ok(f"US:{ticker}", f"{db.upsert_disclosures(conn, cid, rows, 'sec_edgar')}건")
    return 0


def cmd_customs(args) -> int:
    s = make_session()
    from_ym = str(args.from_ym)
    start = date(int(from_ym[:4]), int(from_ym[4:6]), 1)
    today = date.today()
    with db.connect() as conn:
        for hs in [h.strip() for h in args.hs.split(",") if h.strip()]:
            exp_rows, imp_rows = {}, {}
            for a, b in customs.month_windows(start, today):  # 1회 조회 1년 이내
                for d, exp, imp in customs.fetch_trade(s, hs, a, b):
                    exp_rows[d], imp_rows[d] = exp, imp
            label = customs.HS_NAMES.get(hs, f"HS {hs}")
            n1 = db.upsert_macro(conn, f"KCS_EXP_{hs}_USD", f"{label} 수출금액(USD)", "kcs", "M", sorted(exp_rows.items()))
            n2 = db.upsert_macro(conn, f"KCS_IMP_{hs}_USD", f"{label} 수입금액(USD)", "kcs", "M", sorted(imp_rows.items()))
            if n1 == 0:  # 응답은 정상인데 행이 없으면 실패로 남겨 자동 실행 요약에서 보이게 (1회 작업은 6시간 뒤 재시도)
                _fail(f"HS {hs}", "0건: 활용신청 승인·엔드포인트·HS 코드를 확인하세요")
            else:
                _ok(f"HS {hs}", f"수출 {n1}개월 / 수입 {n2}개월")
    return 0


def _kr_disclosures_bulk(s, conn, companies, since, types) -> None:
    """유가증권·코스닥 시장 전체 공시를 받아 대상 종목만 저장 (종목별 호출 200회 → 수십 쪽)."""
    by_corp = {corp_code: (cid, ticker) for cid, ticker, _, corp_code, _ in companies if corp_code}
    counts: dict[str, int] = {}
    for cls in ("Y", "K"):
        for ty in types:
            rows = dart.fetch_disclosures(s, None, ymd(since), ymd(date.today()), ty, max_pages=50, corp_cls=cls)
            grouped: dict[str, list[dict]] = {}
            for r in rows:
                if r.get("corp_code") in by_corp:
                    grouped.setdefault(r["corp_code"], []).append(r)
            for corp_code, part in grouped.items():
                cid, ticker = by_corp[corp_code]
                counts[ticker] = counts.get(ticker, 0) + db.upsert_disclosures(conn, cid, part, "dart")
    _ok("KR 공시(시장 전체)", f"{sum(counts.values())}건, {len(counts)}종목 (유형 {','.join(types)})")


def cmd_trends(args) -> int:
    s = make_session()
    end = date.today() - timedelta(days=1)  # 데이터랩은 전일까지
    start = end - timedelta(days=args.days)
    subjects = []  # (subject, 검색어)
    with db.connect() as conn:
        for market in ("KR", "US"):
            for _, ticker, name, *_ in db.list_companies(conn, market):
                subjects.append((f"{market}:{ticker}", trends.stock_keywords(news.terms_for(market, ticker, name)[0])))
        subjects += [(f"topic:{t}", [t]) for t in trends.TOPICS]
        anchor_name, anchor_kw = trends.ANCHOR
        total = 0
        for batch in trends.batches(subjects, trends.MAX_GROUPS - 1):
            groups = [(anchor_name, [anchor_kw])] + [(subj, kws) for subj, kws in batch]
            try:
                result = trends.fetch_trend(s, start, end, groups)
            except RuntimeError as exc:
                _fail("데이터랩", str(exc))
                return 1
            anchor = dict(result.get(anchor_name, []))
            rows = []
            for subj, kws in batch:
                for d, ratio in result.get(subj, []):
                    rows.append({"subject": subj, "keyword": ", ".join(kws), "trade_date": d, "ratio": ratio,
                                 "anchor_ratio": anchor.get(d)})
            total += db.upsert_search_trend(conn, rows, "naver_datalab")
        _ok("검색 관심도", f"{total}행 ({len(subjects)}개 대상)")
    return 0


def cmd_daily(args) -> int:
    """최근분만 갱신. 한 단계가 실패해도 나머지는 계속 실행하고, 마지막에 실패 목록을 출력합니다."""
    from argparse import Namespace

    steps = [
        ("국내 시세", cmd_kr_prices, Namespace(days=10)),
        ("해외 시세", cmd_us_prices, Namespace(days=10)),
        ("시장 지수", cmd_indices, Namespace(days=10)),
        ("ETF 시세", cmd_etf, Namespace(days=10)),
        ("뉴스", cmd_news, Namespace(days=3, max_items=300)),
        ("해외 영문 뉴스(Finnhub)", cmd_finnhub, Namespace(days=3)),
        ("공시", cmd_disclosures, Namespace(days=14, types="A,B,I")),
        ("거시지표", cmd_macro, Namespace(years=1)),
        ("검색 관심도", cmd_trends, Namespace(days=14)),
        ("블로그·카페 반응", cmd_community, Namespace(days=3, sources="naver_blog,naver_cafe", pages=1)),
        ("감성 점수", cmd_sentiment, Namespace(method=None, limit=None)),
        ("언급량 스냅샷(pulse)", cmd_pulse, Namespace(tickers=None, max_age=0, market_hours=False)),
        ("지표 재계산", cmd_build_features, Namespace()),
        ("features.json 내보내기", cmd_export_features, Namespace(out="features.json")),
    ]
    failed = []
    for label, fn, ns in steps:
        print(f"== {label} ==")
        try:
            if fn(ns) != 0:
                failed.append(label)
        except Exception as exc:
            _fail(label, f"{type(exc).__name__}: {exc}")
            failed.append(label)
    print("완료." if not failed else f"실패한 단계: {', '.join(failed)}")
    return 1 if failed else 0


def cmd_dart(args) -> int:
    s = make_session()
    with db.connect() as conn:
        for cid, ticker, _, corp_code, _ in db.list_companies(conn, "KR"):
            if not corp_code:
                print(f"[WARN] {ticker} corp_code 없음 → sync-dart-codes 먼저")
                continue
            for year in range(args.from_year, date.today().year):
                rows = dart.fetch_financials(s, corp_code, year)
                _ok(f"{ticker} {year}", f"{db.upsert_facts(conn, cid, rows, 'dart')}행")
    return 0


# 정기보고서 제출 기한 (월, 일). 연도 + 이 날짜가 지나야 받을 자료가 있음 (사업보고서는 다음 해)
REPORT_DUE = {"11013": (5, 15), "11012": (8, 14), "11014": (11, 14), "11011": (3, 31)}
COVERED_SHARE = 0.9  # 묶음 안 회사의 이 비율 이상이 이미 있으면 다시 받지 않음


def report_due(year: int, code: str, today: date) -> bool:
    m, d = REPORT_DUE.get(code, (1, 1))
    return today >= date(year + 1 if code == dart.REPORT_ANNUAL else year, m, d)


def cmd_dart_multi(args) -> int:
    """DART 다중회사 주요계정: 100개 회사씩 묶어 연도·보고서별로 한 번에 받습니다 (KOSPI 200 용).

    분기 2년치는 한 번에 50분 넘게 걸려 GitHub Actions 제한(40분)에 끊겼습니다. 그래서
    - 제출 기한이 지나지 않은 보고서는 건너뛰고,
    - 묶음 안 회사의 90% 이상이 이미 받은 연도·보고서는 다시 받지 않으며,
    - --max-seconds 를 넘기면 멈추고 jobs.MORE 를 돌려 다음 실행에서 이어 받습니다.
    """
    import time

    from .jobs import MORE

    s = make_session()
    reports = [r.strip() for r in args.reports.split(",") if r.strip()]
    last_year = date.today().year if any(r != dart.REPORT_ANNUAL for r in reports) else date.today().year - 1
    limit = getattr(args, "max_seconds", None)
    t0 = time.monotonic()
    bad = 0
    with db.connect() as conn:
        companies = [c for c in db.list_companies(conn, "KR") if c[3]]
        by_key = {}
        for cid, ticker, _, corp_code, _ in companies:
            by_key[ticker] = cid
            by_key[corp_code] = cid
        for year in range(args.from_year, last_year + 1):
            for code in reports:
                if not report_due(year, code, date.today()):
                    print(f"[SKIP] {year} {code} 제출 기한 전")
                    continue
                have = db.fact_companies(conn, year, code, "dart")
                n = got = skipped = 0
                for i in range(0, len(companies), dart.MULTI_MAX):
                    part = companies[i:i + dart.MULTI_MAX]
                    if sum(c[0] in have for c in part) >= COVERED_SHARE * len(part):
                        skipped += len(part)
                        continue
                    if limit and time.monotonic() - t0 > limit:
                        _ok(f"{year} {code}", f"{got}개사 {n}행 · 시간 예산 {limit}초 도달, 다음 실행에서 이어서")
                        return MORE
                    chunk = [c[3] for c in part]
                    try:
                        result = dart.fetch_multi(s, chunk, year, code)
                    except RuntimeError as exc:
                        _fail(f"{year} {code}", str(exc))
                        bad += 1
                        continue
                    for key, rows in result.items():
                        cid = by_key.get(key)
                        if cid is None:
                            continue
                        got += 1
                        n += db.upsert_facts(conn, cid, rows, "dart")
                _ok(f"{year} {code}", f"{got}/{len(companies)}개사 {n}행" + (f" (이미 있음 {skipped}개사 건너뜀)" if skipped else ""))
    return 1 if bad else 0


PROBE_KEYS = {
    "환율민감도": (("환율",), ("민감도", "변동", "상승", "하락")),
    "외화자산부채": (("외화",), ("자산", "부채")),
    "수출내수": (("수출",), ("내수", "국내")),
    "지역별": (("지역",), ("국내", "해외", "아시아", "미주", "유럽", "중국")),
    "파생헤지": (("통화선도", "통화스왑", "위험회피", "파생상품"), ("",)),
}


def _probe_extract(args) -> int:
    """전 종목(또는 --tickers)에 추출기를 돌려 결과와 실패 사례 표를 출력 (DB에 쓰지 않음)."""
    from . import exposure_parse
    from .collectors import dart_doc

    s = make_session()
    stats: dict[str, int] = {}
    n = 0
    with db.connect() as conn:
        companies = db.list_companies(conn, "KR")
        if args.tickers != "ALL":
            want = {t.strip() for t in args.tickers.split(",")}
            companies = [c for c in companies if c[1] in want]
        for cid, ticker, name, *_ in companies[: args.limit]:
            reports = db.annual_reports(conn, cid)
            if not reports:
                print(f"{ticker} {name}: 사업보고서 없음")
                continue
            fy = reports[0][1]
            files = None
            for rc, y, _ in reports:
                if y != fy:
                    break
                try:
                    files = dart_doc.fetch_document(s, rc)
                    break
                except RuntimeError as exc:
                    print(f"{ticker} {name}: {rc} {exc}")
            if not files:
                continue
            n += 1
            res = exposure_parse.extract(files, db.annual_revenue(conn, cid, fy))
            for k in res["found"]:
                stats[k] = stats.get(k, 0) + 1
            ms = {(m.metric, m.currency): m for m in res["metrics"]}
            usd = ms.get(("fx_sens", "USD"))
            exp = ms.get(("export_share", "ALL"))
            print(f"{ticker} {name} FY{fy}: {res['found']} | USD민감도="
                  f"{'-' if not usd else f'{usd.value/1e8:,.0f}억 ({usd.change_pct}% {usd.basis})'}"
                  f" | 수출비중={'-' if not exp else f'{exp.value:.1f}%'}")
            if "fx_sens" not in res["found"] and args.show_misses:
                shown = 0
                for fname, text in files.items():
                    for tb in dart_doc.tables_from_xml(text, fname):
                        blob = tb.context + " " + tb.text(4)
                        if "환율" in blob and "%" in blob and shown < 2:
                            shown += 1
                            print(f"   · 후보 {fname} 표#{tb.index} 단위={tb.unit} 문맥={tb.context[-160:]!r}")
                            print("     " + tb.text(6)[:500].replace(chr(10), chr(10) + "     "))
    print(f"\n== 요약: 원문 {n}개사, " + ", ".join(f"{k} {v}개사" for k, v in sorted(stats.items())))
    return 0


def cmd_probe_dart_doc(args) -> int:
    """사업보고서 원문에서 노출 관련 표가 어떤 모양인지 출력합니다 (파서 개발·점검용, DB에 쓰지 않음)."""
    from .collectors import dart_doc

    if args.extract:
        return _probe_extract(args)
    s = make_session()
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    with db.connect() as conn, conn.cursor() as cur:
        for t in tickers:
            cur.execute(
                "SELECT d.rcept_no, d.report_nm FROM core.disclosure d JOIN core.company c USING (company_id) "
                "WHERE c.market = 'KR' AND c.ticker = %s AND d.event_type = 'annual_report' "
                "ORDER BY d.rcept_dt DESC, d.rcept_no DESC LIMIT 1", [t])
            row = cur.fetchone()
            if not row:
                print(f"\n##### {t}: 사업보고서 접수번호 없음")
                continue
            rcept, nm = row
            try:
                files = dart_doc.fetch_document(s, rcept)
            except RuntimeError as exc:
                print(f"\n##### {t} {nm} {rcept}: {exc}")
                continue
            print(f"\n##### {t} {nm} {rcept} 파일 {[(n, len(v)) for n, v in files.items()]}")
            shown = 0
            for name, text in files.items():
                for tb in dart_doc.tables_from_xml(text, name):
                    blob = tb.context[-200:] + " " + tb.text(6)
                    hits = [k for k, (a, b) in PROBE_KEYS.items()
                            if any(x in blob for x in a) and any(y in blob for y in b)]
                    if not hits or shown >= args.max_tables:
                        continue
                    shown += 1
                    print(f"\n--- [{','.join(hits)}] {name} 표#{tb.index} 단위={tb.unit} 제목={tb.title!r}")
                    print("문맥:", tb.context[-220:])
                    print(tb.text(args.max_rows)[:args.max_chars])
    return 0


def cmd_report_exposure(args) -> int:
    """사업보고서 원문에서 기업 고유 거시 노출(환율 민감도·외화 자산부채·수출 비중·외환 파생)을 뽑아 저장.
    같은 접수번호를 이미 읽었으면 건너뜁니다 (--force 로 다시)."""
    from . import exposure_parse
    from .collectors import dart_doc

    s = make_session()
    bad = 0
    with db.connect() as conn:
        for cid, ticker, name, *_ in db.list_companies(conn, "KR"):
            reports = db.annual_reports(conn, cid)
            if not reports:
                _ok(f"KR:{ticker}", "사업보고서 접수번호 없음 (건너뜀)")
                continue
            fy = reports[0][1]
            if not getattr(args, "force", False) and db.report_parsed(conn, cid, fy) in {r[0] for r in reports if r[1] == fy}:
                continue
            files, rcept, rdt, last_err = None, None, None, ""
            for rc, y, dt in reports:
                if y != fy:
                    break
                try:
                    files, rcept, rdt = dart_doc.fetch_document(s, rc), rc, dt
                    break
                except RuntimeError as exc:
                    last_err = str(exc)
            if files is None:
                _fail(f"KR:{ticker}", f"FY{fy} 원문 받기 실패: {last_err}")
                bad += 1
                continue
            res = exposure_parse.extract(files, db.annual_revenue(conn, cid, fy))
            n = db.save_report_exposure(conn, cid, fy, rcept, res["metrics"], res["found"],
                                        "ok" if res["found"] else "not_found")
            if res["risk_text"]:
                db.upsert_documents(conn, cid, "dart_report", [{
                    "title": f"FY{fy} 사업보고서 · 환위험 관리", "published_at": rdt,
                    "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}#fx-risk",
                    "summary": res["risk_text"][:500]}])
            _ok(f"KR:{ticker}", f"FY{fy} {rcept} 지표 {n}개 ({', '.join(res['found']) or '찾지 못함'})")
    return 1 if bad else 0


def cmd_sec(args) -> int:
    s = make_session()
    tmap = sec.fetch_ticker_map(s)
    with db.connect() as conn:
        for cid, ticker, name, _, _ in db.list_companies(conn, "US"):
            cik = tmap.get(ticker.upper())
            if not cik:
                print(f"[WARN] {ticker} CIK 없음")
                continue
            db.get_or_create_company(conn, "US", ticker, name, cik=cik)
            rows = sec.fetch_annual_facts(s, cik, args.from_year)
            _ok(ticker, f"{db.upsert_facts(conn, cid, rows, 'sec_edgar')}행")
    return 0


def _macro_start(conn, series_id: str, start: date) -> date:
    """DB에 아직 없는 시리즈는 과거분(NEW_SERIES_YEARS)부터 받습니다. 매시간 작업은 최근 1년만 받기 때문."""
    if db.has_macro(conn, series_id):
        return start
    return min(start, date.today() - timedelta(days=365 * macro.NEW_SERIES_YEARS))


def cmd_macro(args) -> int:
    """ECOS·FRED. 키가 없는 쪽은 건너뛰고, 시리즈 하나가 실패해도 나머지는 계속합니다."""
    s = make_session()
    start = date.today() - timedelta(days=365 * args.years)
    bad = 0
    with db.connect() as conn:
        if not env("ECOS_API_KEY"):
            print("[SKIP] ECOS_API_KEY 없음 → 한국은행 지표 건너뜀")
        for sid, name, stat, cyc, item in (macro.ECOS_SERIES if env("ECOS_API_KEY") else []):
            fmt = {"D": "%Y%m%d", "M": "%Y%m"}[cyc]
            since = _macro_start(conn, sid, start)
            try:
                rows = macro.fetch_ecos(s, stat, cyc, since.strftime(fmt), date.today().strftime(fmt), item)
            except RuntimeError as exc:
                _fail(sid, str(exc))
                bad += 1
                continue
            _ok(sid, f"{db.upsert_macro(conn, sid, name, 'ecos', cyc, rows)}행")
        if not env("FRED_API_KEY"):
            print("[SKIP] FRED_API_KEY 없음 → FRED 지표 건너뜀")
        for sid, name, cyc in (macro.FRED_SERIES if env("FRED_API_KEY") else []):
            try:
                rows = macro.fetch_fred(s, sid, _macro_start(conn, sid, start).isoformat())
            except RuntimeError as exc:
                _fail(sid, str(exc))
                bad += 1
                continue
            _ok(sid, f"{db.upsert_macro(conn, sid, name, 'fred', cyc, rows)}행")
    return 1 if bad else 0


def cmd_etf(args) -> int:
    s = make_session()
    end, begin = date.today(), date.today() - timedelta(days=args.days)
    bad = 0
    with db.connect() as conn:
        for ticker, name in ETF_UNIVERSE:
            by_date = {}
            try:
                for a, b in date_windows(begin, end, 330):
                    for r in etf.fetch_etf(s, ticker, ymd(a), ymd(b)):
                        by_date[r["trade_date"]] = r
            except RuntimeError as exc:
                _fail(ticker, str(exc))
                bad += 1
                continue
            rows = drop_incomplete_today([by_date[d] for d in sorted(by_date)], "KR")
            n = db.upsert_etf_prices(conn, ticker, name, rows, "fsc_etf")
            _ok(f"ETF {ticker} {name}", f"{n}행")
    return 1 if bad else 0


def cmd_finnhub(args) -> int:
    """해외 종목 영문 뉴스 (Finnhub). 키가 없으면 건너뜁니다."""
    if not env("FINNHUB_API_KEY"):
        print("[SKIP] FINNHUB_API_KEY 없음 → Finnhub 뉴스 건너뜀")
        return 0
    s = make_session()
    since = date.today() - timedelta(days=args.days)
    bad = 0
    with db.connect() as conn:
        for cid, ticker, *_ in db.list_companies(conn, "US"):
            try:
                rows = finnhub.fetch_company_news(s, ticker, since, date.today())
            except RuntimeError as exc:
                _fail(f"US:{ticker}", str(exc))
                bad += 1
                continue
            _ok(f"US:{ticker}", f"{db.upsert_documents(conn, cid, 'finnhub_news', rows)}건")
    return 1 if bad else 0


def cmd_youtube(args) -> int:
    """유튜브 공개 데이터: 종목별 최근 영상(통계)과 공개 댓글. 키가 없으면 건너뜁니다."""
    if not env("YOUTUBE_API_KEY"):
        print("[SKIP] YOUTUBE_API_KEY 없음 → 유튜브 건너뜀")
        return 0
    s = make_session()
    since = date.today() - timedelta(days=args.days)
    bad = 0
    with db.connect() as conn:
        top_kr = getattr(args, "top_kr", None)
        caps = db.market_caps(conn) if top_kr else {}
        for market in ("KR", "US"):
            companies = db.list_companies(conn, market)
            if market == "KR" and top_kr:  # 할당량 때문에 영상이 많은 시총 상위 종목만
                companies = sorted(companies, key=lambda c: -caps.get(c[0], 0))[:top_kr]
            for cid, ticker, name, *_ in companies:
                term, must = news.terms_for(market, ticker, name)
                query = f"{term} 주가"
                try:
                    videos = youtube.search_videos(s, query, since, args.videos, getattr(args, "order", "viewCount"))
                    videos = [v for v in videos if any(m.lower() in f"{v['title']} {v['summary']}".lower() for m in must)]
                    stats = youtube.video_stats(s, [v["video_id"] for v in videos])
                    db.upsert_youtube_videos(conn, cid, query, videos, stats)
                    n_comments = 0
                    for v in videos:
                        if (stats.get(v["video_id"]) or {}).get("comment_count") == 0:
                            continue
                        rows = youtube.video_comments(s, v["video_id"], args.comments)
                        n_comments += db.upsert_documents(conn, cid, "youtube_comment", rows)
                except RuntimeError as exc:
                    _fail(f"{market}:{ticker}", str(exc))
                    bad += 1
                    continue
                _ok(f"{market}:{ticker}", f"영상 {len(videos)}개, 댓글 {n_comments}건 (검색어 '{query}')")
    return 1 if bad else 0


def cmd_investor_flows(args) -> int:
    """국내 종목 투자자별 일별 순매수 (네이버 증권 공개 API, 키 불필요)."""
    s = make_session()
    since = date.today() - timedelta(days=args.days)
    bad = 0
    with db.connect() as conn:
        for cid, ticker, *_ in db.list_companies(conn, "KR"):
            try:
                rows = drop_incomplete_today(investor_flow.fetch_flows(s, ticker, since), "KR")
            except RuntimeError as exc:
                _fail(f"KR:{ticker}", str(exc))
                bad += 1
                continue
            n = db.upsert_investor_flows(conn, cid, rows, "naver_trend")
            _ok(f"KR:{ticker}", f"{n}행" + (f" ({rows[0]['trade_date']}~{rows[-1]['trade_date']})" if rows else ""))
    return 1 if bad else 0


def cmd_lending(args) -> int:
    """코스피 종목별 일별 대차거래 (하루 1회 호출로 코스피 전 종목). 등록된 국내 종목만 저장합니다.

    최근 days 일 중 시세가 있는 거래일(core.price_daily)인데 대차 행이 없는 날만, 최근 날짜부터 max_days 일씩 받습니다.
    한 번 부를 때 약 3초라 1년치를 한 번에 받으면 십여 분 걸리므로 매시간 나눠 채웁니다.
    API 를 부르는 동안 DB 연결을 열어 두면 Neon 이 끊으므로, 읽기·쓰기 연결을 따로 엽니다.
    """
    s = make_session()
    today = date.today()
    since = today - timedelta(days=args.days)
    with db.connect() as conn:
        ids = {t: cid for cid, t, *_ in db.list_companies(conn, "KR")}
        if not ids:
            return 0
        have = db.lending_dates(conn, since)
        todo = [d for d in db.trading_dates(conn, "KR", since) if d < today and d not in have]
    todo = sorted(todo, reverse=True)[:args.max_days] if args.max_days else sorted(todo, reverse=True)
    if not todo:
        _ok("대차거래", "받을 날짜 없음 (모두 수집됨)")
        return 0
    by_cid: dict[int, list] = {}
    days = bad = 0
    for d in todo:
        try:
            rows = lending.fetch_day(s, d)
        except RuntimeError as exc:
            _fail(f"대차 {d}", str(exc))
            bad += 1
            continue
        days += bool(rows)
        for r in rows:
            if r["ticker"] in ids:
                by_cid.setdefault(ids[r["ticker"]], []).append(r)
    with db.connect() as conn:
        n = sum(db.upsert_lending(conn, cid, rows, "fsc_lending") for cid, rows in by_cid.items())
    # 최근 1~2 영업일은 아직 공개 전일 수 있음. 며칠 지난 날짜도 0건이면 승인·엔드포인트 문제로 보고 실패로 남김
    if n == 0 and not bad and min(todo) <= today - timedelta(days=4):
        _fail("대차거래", "0건: 활용신청 승인·엔드포인트를 확인하세요")
        return 1
    _ok("대차거래", f"{len(todo)}일 요청 중 {days}거래일 수신 ({min(todo)}~{max(todo)}), {len(by_cid)}/{len(ids)}종목, {n}행")
    return 1 if bad else 0


def cmd_probe_bulk(args) -> int:
    """KOSPI 200·일괄 조회 API 응답만 확인합니다 (DB 에 쓰지 않음, 키 값은 출력하지 않음)."""
    s = make_session()
    bad = 0
    try:
        members = kospi200.fetch_constituents(s)
        _ok("KOSPI 200", f"{len(members)}종목, 예: {members[:3]}")
    except RuntimeError as exc:
        _fail("KOSPI 200", str(exc)); bad += 1
    today = date.today()
    try:
        rows = fsc.fetch_all_prices(s, ymd(today - timedelta(days=5)), ymd(today))
        tickers = {r["ticker"] for r in rows}
        _ok("FSC 전 종목", f"{len(rows)}행, 종목 {len(tickers)}개, 005930 포함={'005930' in tickers}")
    except RuntimeError as exc:
        _fail("FSC 전 종목", str(exc)); bad += 1
    with db.connect() as conn:
        corps = [c[3] for c in db.list_companies(conn, "KR") if c[3]][:5]
    try:
        out = dart.fetch_multi(s, corps, today.year - 1)
        sample = next(iter(out.values()), [])
        _ok("DART 다중회사", f"{len(out)}/{len(corps)}개사, 키 예: {list(out)[:3]}, "
                          f"손익 계정: {sorted({(r['account_nm'], r['account_id']) for r in sample if r['sj_div'] == 'IS'})}")
    except RuntimeError as exc:
        _fail("DART 다중회사", str(exc)); bad += 1
    try:
        rows = dart.fetch_disclosures(s, None, ymd(today - timedelta(days=5)), ymd(today), "A", max_pages=3, corp_cls="Y")
        _ok("DART 시장 전체 공시", f"{len(rows)}건, corp_code 있음={all(r['corp_code'] for r in rows)}")
    except RuntimeError as exc:
        _fail("DART 시장 전체 공시", str(exc)); bad += 1
    return 1 if bad else 0


def cmd_sync_kospi200(args) -> int:
    """KOSPI 200 구성 종목을 core.company 에 등록하고 index_tags 에 'KOSPI200' 을 표시합니다."""
    s = make_session()
    from . import jobs

    members = kospi200.fetch_constituents(s)
    with db.connect() as conn:
        before = {t for _, t, *_ in db.list_companies(conn, "KR")}
        ids = [db.get_or_create_company(conn, "KR", t, n) for t, n in members]
        db.set_index_members(conn, "KOSPI200", ids, date.today())
        added = [t for t, _ in members if t not in before]
        if added:
            # 준비 작업(DART 고유번호·업종)이 오늘 이미 끝났어도 새 종목 기준으로 다시 돌게 함
            jobs.reset_backfills(conn, jobs.PREP_JOBS + jobs.PER_TICKER_BACKFILLS)
    _ok("KOSPI 200", f"{len(members)}종목 표시, 새 종목 {len(added)}개" + (" → 종목별 과거분 다시 받기" if added else ""))
    return 0


def cmd_company_info(args) -> int:
    """업종코드(DART KSIC / SEC SIC)와 발행주식수(국내 FSC 상장주식수 / 해외 SEC 표지)를 core.company 에 채웁니다."""
    s = make_session()
    with db.connect() as conn:
        companies = {m: db.list_companies(conn, m) for m in ("KR", "US")}
        for cid, ticker, _, corp_code, _ in companies["KR"]:
            if not corp_code:
                print(f"[WARN] {ticker} corp_code 없음 → sync-dart-codes 먼저")
                continue
            info = dart.fetch_company(s, corp_code)
            db.update_company_info(conn, cid, sector=info["induty_code"])
            sector = krx_sector.ksic_to_krx(info["induty_code"], ticker)
            db.set_krx_sector(conn, cid, sector)
            _ok(f"KR {ticker}", f"KSIC {info['induty_code']} → KRX 업종 {sector or '-'}")
            if getattr(args, "skip_kr_shares", False):
                continue  # 상장주식수는 국내 시세(전 종목 조회) 작업이 매번 갱신
            today = date.today()
            got = fsc.latest_shares(fsc.fetch_prices(s, ticker, ymd(today - timedelta(days=14)), ymd(today), 20))
            if got:
                db.update_company_info(conn, cid, shares=got[0], shares_asof=got[1])
                _ok(f"KR {ticker}", f"상장주식수 {got[0]:,} ({got[1]})")
            else:
                print(f"[WARN] {ticker} 상장주식수 없음 (FSC)")
        tmap = sec.fetch_ticker_map(s)
        for cid, ticker, name, _, cik in companies["US"]:
            cik = cik or tmap.get(ticker.upper())
            if not cik:
                print(f"[WARN] {ticker} CIK 없음")
                continue
            db.get_or_create_company(conn, "US", ticker, name, cik=cik)
            info = sec.fetch_submissions(s, cik)
            db.update_company_info(conn, cid, sector=info["sic"], sector_desc=info["sic_desc"])
            _ok(f"US {ticker}", f"SIC {info['sic']} {info['sic_desc']}")
            got = sec.fetch_shares_outstanding(s, cik, ticker)
            if got:
                db.update_company_info(conn, cid, shares=got[0], shares_asof=got[1])
                _ok(f"US {ticker}", f"발행주식수 {got[0]:,} ({got[1]})")
            else:
                print(f"[WARN] {ticker} 발행주식수 없음 (SEC dei)")
    return 0


def cmd_inspect_sec(args) -> int:
    """SEC companyfacts 구조 진단: 어떤 분류체계·태그·공시양식으로 공시했는지 출력."""
    from collections import Counter

    from .http_util import get_json

    s = make_session()
    ticker = args.ticker.upper()
    cik = sec.fetch_ticker_map(s).get(ticker)
    print(f"{ticker} CIK = {cik}")
    if not cik:
        return 1
    payload = get_json(s, "sec", sec.FACTS_URL.format(cik=cik), headers={"User-Agent": sec.user_agent()}, limiter=sec._limiter)
    facts = payload.get("facts", {})
    if args.tag:
        for tax in ("ifrs-full", "us-gaap"):
            node = facts.get(tax, {}).get(args.tag)
            if not node:
                continue
            for unit, rows in node.get("units", {}).items():
                print(f"[{tax}] {args.tag} 단위={unit} {len(rows)}건 (종료일 최신순 25건)")
                for f in sorted(rows, key=lambda r: r.get("end", ""), reverse=True)[:25]:
                    print(f"  end={f.get('end')} start={f.get('start')} fy={f.get('fy')} fp={f.get('fp')} "
                          f"form={f.get('form')} filed={f.get('filed')} val={f.get('val')}")
            return 0
        print(f"태그 {args.tag} 없음")
        return 1
    print("분류체계:", {k: len(v) for k, v in facts.items()})
    for tax, tags in facts.items():
        forms, fps = Counter(), Counter()
        for tag in tags.values():
            for unit_rows in tag.get("units", {}).values():
                for f in unit_rows:
                    forms[f.get("form")] += 1
                    fps[f.get("fp")] += 1
        print(f"[{tax}] form 분포: {dict(forms)}  fp 분포: {dict(fps)}")
    want = ("Revenue", "Profit", "Equity", "Assets", "Liabilities", "OperatingIncome")
    for tax, tags in facts.items():
        names = sorted(t for t in tags if any(w in t for w in want))[:40]
        print(f"[{tax}] 관련 태그(최대 40): {names}")
    return 0


def cmd_dart_q(args) -> int:
    s = make_session()
    with db.connect() as conn:
        for cid, ticker, _, corp_code, _ in db.list_companies(conn, "KR"):
            if not corp_code:
                continue
            for year in range(args.from_year, date.today().year + 1):
                n = 0
                for code in ("11013", "11012", "11014"):  # 1분기, 반기, 3분기
                    rows = dart.fetch_financials(s, corp_code, year, report_code=code)
                    n += db.upsert_facts(conn, cid, rows, "dart")
                _ok(f"{ticker} {year}", f"분기 {n}행")
    return 0


def cmd_sec_q(args) -> int:
    s = make_session()
    tmap = sec.fetch_ticker_map(s)
    with db.connect() as conn:
        for cid, ticker, _, _, cik in db.list_companies(conn, "US"):
            cik = cik or tmap.get(ticker.upper())
            if not cik:
                continue
            rows = sec.fetch_quarterly_facts(s, cik, args.from_year)
            _ok(ticker, f"{db.upsert_facts(conn, cid, rows, 'sec_edgar')}행" + ("" if rows else " (10-Q 없음: 외국 기업은 6-K 라 정상)"))
    return 0


def cmd_build_quarterly(args) -> int:
    with db.connect() as conn:
        for market, source, fn in (("KR", "dart", quarterly.derive_dart), ("US", "sec_edgar", quarterly.derive_sec)):
            for cid, ticker, *_ in db.list_companies(conn, market):
                rows = fn(db.fetch_facts(conn, cid, source))
                n = db.upsert_quarters(conn, cid, rows, source)
                _ok(f"{market} {ticker}", f"분기 지표 {n}행")
                gaps = quarterly.ttm_gaps(rows)
                if gaps:
                    print(f"[WARN] {ticker} 최근 4개 분기가 연속되지 않음: {', '.join(gaps)} → TTM 제외됨")
    return 0


def cmd_show_ttm(args) -> int:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT market, ticker, name, ttm_end, per_ttm, pbr, op_margin_ttm, roe_ttm, debt_ratio "
            "FROM core.v_ttm ORDER BY market, ticker"
        )
        print(f"{'시장':<3}{'티커':<8}{'이름':<18}{'TTM종료':<12}{'PER':>8}{'PBR':>7}{'영업이익률':>9}{'ROE':>8}{'부채비율':>9}")
        for m, t, n, te, per, pbr, om, roe, dr in cur.fetchall():
            f = lambda v: "-" if v is None else f"{float(v):.2f}"
            print(f"{m:<3}{t:<8}{(n or '')[:16]:<18}{str(te):<12}{f(per):>8}{f(pbr):>7}{f(om):>9}{f(roe):>8}{f(dr):>9}")
    return 0


def cmd_build_features(args) -> int:
    today = date.today()
    with db.connect() as conn:
        macro = db.fetch_macro_all(conn)
        # 해외 20-F 기업(ASML·TSMC)은 재무 통화(EUR·TWD)를 USD 로 환산해 연간 PER/PBR 채움 (collect-macro 의 FRED 환율 필요)
        snaps = {k: features.fill_fx_valuation(v, macro) for k, v in db.fetch_snapshots(conn).items()}
        theme_of = {}
        for spec in PEER_THEMES.values():
            for key in spec["members"]:
                theme_of[tuple(key)] = [tuple(k) for k in spec["members"]]
        for key, snap in sorted(snaps.items()):
            members = [m for m in theme_of.get(key, []) if m != key and m in snaps]
            peers = features.peer_medians([snaps[m] for m in members])
            cid = snap["company_id"]
            attention = {
                "search": trends.relative(db.fetch_search_trend(conn, f"{key[0]}:{key[1]}")),
                "reactions": db.fetch_reactions(conn, cid, today - timedelta(days=40)),
                "mentions": db.fetch_mention_daily(conn, cid),
                "today": today,
            }
            rows = features.compute_features(
                {**snap, "ccy": snap.get("ttm_currency") or snap.get("fin_currency")},
                db.fetch_prices(conn, cid), db.fetch_quarters(conn, cid), macro, peers, attention,
                db.fetch_investor_flows(conn, cid) if key[0] == "KR" else None,
                db.fetch_lending(conn, cid) if key[0] == "KR" else None)
            n = db.replace_features(conn, cid, today, rows)
            _ok(f"{key[0]} {key[1]}", f"지표 {n}개 (비교군 {peers['n']}개)")
    return 0


def cmd_show_features(args) -> int:
    with db.connect() as conn:
        data = db.read_features(conn)
    key = next((k for k in data if k[1].upper() == args.ticker.upper()), None)
    if key is None:
        print("해당 종목의 지표가 없습니다. build-features 를 먼저 실행하세요.")
        return 1
    d = data[key]
    print(f"{key[0]} {key[1]} {d['name']}  (as_of {d['as_of']})")
    for persp in ("valuation", "fundamentals", "supply_demand", "macro", "risk"):
        print(f"\n[{persp}]")
        for feat, v in d.get(persp, {}).items():
            val = "-" if v["value"] is None else f"{v['value']:.4g}"
            print(f"  {feat:<32}{val:>12} {v['unit'] or ''}  {('· ' + v['note']) if v['note'] else ''}")
    return 0


def cmd_export_features(args) -> int:
    import json

    with db.connect() as conn:
        data = db.read_features(conn)
    out = {f"{m}:{t}": v for (m, t), v in data.items()}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    _ok("내보내기", f"{args.out} ({len(out)}개 종목)")
    return 0


PEER_MAX = 15  # 업종 Peer Group 최대 인원 (시총 큰 순)


def cmd_build_peers(args) -> int:
    with db.connect() as conn:
        caps = db.market_caps(conn)
        # KOSPI 200 업종 Peer Group: 같은 KRX 업종 안의 다른 편입 종목 (시총 순)
        n_groups = 0
        for sector, members in db.index_members_by_sector(conn, "KOSPI200").items():
            for base_cid, _ in members:
                others = sorted((cid for cid, _ in members if cid != base_cid), key=lambda cid: -caps.get(cid, 0))
                rows = [(cid, i + 1, f"KOSPI 200 같은 KRX 업종: {sector}") for i, cid in enumerate(others[:PEER_MAX])]
                if rows:
                    db.create_peer_group(conn, base_cid, f"krx_sector:{sector}", rows)
                    n_groups += 1
        _ok("KOSPI 200 업종 Peer", f"{n_groups}개 그룹")
        for theme, spec in PEER_THEMES.items():
            ids = {}
            for market, ticker in spec["members"]:
                found = db.find_company(conn, market, ticker)
                if found:
                    ids[(market, ticker)] = found[0]
                else:
                    print(f"[WARN] {market} {ticker} 미등록 → seed-companies 먼저")
            for base in spec["bases"]:
                if base not in ids:
                    continue
                others = [cid for key, cid in ids.items() if key != base]
                others.sort(key=lambda cid: -caps.get(cid, 0))
                members = [(cid, i + 1, f"수동 선정 업종: {theme}") for i, cid in enumerate(others)]
                gid = db.create_peer_group(conn, ids[base], f"manual:{theme}", members)
                _ok(f"{base[0]} {base[1]}", f"peer_group {gid}: {len(members)}개 (시총 순, 시총 없으면 뒤로)")
    return 0


def cmd_show_valuation(args) -> int:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT market, ticker, name, fiscal_year, per, pbr, op_margin, roe, debt_ratio "
            "FROM core.v_valuation ORDER BY market, ticker"
        )
        print(f"{'시장':<3}{'티커':<8}{'이름':<18}{'FY':<6}{'PER':>8}{'PBR':>7}{'영업이익률':>9}{'ROE':>8}{'부채비율':>9}")
        for m, t, n, fy, per, pbr, om, roe, dr in cur.fetchall():
            f = lambda v: "-" if v is None else f"{float(v):.2f}"
            print(f"{m:<3}{t:<8}{(n or '')[:16]:<18}{fy or '-':<6}{f(per):>8}{f(pbr):>7}{f(om):>9}{f(roe):>8}{f(dr):>9}")
    return 0


def cmd_tick(args) -> int:
    """차례가 된 작업을 시간 예산 안에서 실행하고 결과표를 출력합니다 (실패가 있어도 0: 다음 실행에서 재시도)."""
    from . import jobs

    conn = db.connect()
    if jobs.ensure_schema(conn):
        _ok("DB 스키마", "처음 실행이라 sql/*.sql 적용")
    state = jobs.DbState(conn, connect=db.connect)
    try:
        if not state.try_lock(f"kic_jobs_tick:{args.lane}" if args.lane else "kic_jobs_tick"):
            print("다른 실행이 진행 중이라 건너뜁니다.")
            return 0
        try:
            result = jobs.tick(state, conn=conn, budget=args.budget, hard_limit=args.budget + 300, only=args.only,
                               lane=args.lane)
            state.prune()
            ov = jobs.overview(state)
            if args.lane:  # 실행 요약에는 이 줄의 작업만
                ov = [o for o in ov if jobs.lane_of(jobs.JOB_BY_NAME[o["name"]]) == args.lane]
        finally:
            state.unlock()
    finally:
        try:
            state.conn.close()
        except Exception:  # noqa: BLE001
            pass
    for r in result["ran"]:
        print(f"{r['status']:<8} {r['job']:<22} {r.get('seconds') or 0:>6}s  {r.get('detail') or ''}")
    if result["waiting"]:
        print("시간 예산 초과로 다음 실행에 넘김:", ", ".join(result["waiting"]))
    print(f"총 {result['seconds']}초, 실행 {len(result['ran'])}건")
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(jobs.markdown_summary(result, ov))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="kic_data")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check-db").set_defaults(fn=cmd_check_db)
    sub.add_parser("apply-schema").set_defaults(fn=cmd_apply_schema)
    sub.add_parser("check-sources").set_defaults(fn=cmd_check_sources)
    a = sub.add_parser("status"); a.add_argument("--json", default=None, help="결과를 JSON 파일로도 저장"); a.set_defaults(fn=cmd_status)
    sub.add_parser("seed-companies").set_defaults(fn=cmd_seed)
    sub.add_parser("sync-dart-codes").set_defaults(fn=cmd_sync_dart)
    a = sub.add_parser("collect-kr-prices"); a.add_argument("--days", type=int, default=365); a.set_defaults(fn=cmd_kr_prices)
    a = sub.add_parser("collect-us-prices"); a.add_argument("--days", type=int, default=365); a.set_defaults(fn=cmd_us_prices)
    a = sub.add_parser("collect-news"); a.add_argument("--days", type=int, default=30); a.add_argument("--max-items", type=int, default=300); a.set_defaults(fn=cmd_news)
    a = sub.add_parser("embed-docs"); a.add_argument("--source", default="naver_news"); a.add_argument("--model", default="bge-m3"); a.add_argument("--batch", type=int, default=64); a.add_argument("--limit", type=int, default=None); a.set_defaults(fn=cmd_embed)
    a = sub.add_parser("collect-indices"); a.add_argument("--days", type=int, default=365); a.set_defaults(fn=cmd_indices)
    a = sub.add_parser("collect-disclosures"); a.add_argument("--days", type=int, default=365); a.add_argument("--types", default="A,B,I")
    a.add_argument("--bulk", action="store_true", help="국내는 시장 전체 공시에서 대상 종목만 (3개월 이내)"); a.set_defaults(fn=cmd_disclosures)
    a = sub.add_parser("collect-customs"); a.add_argument("--from-ym", default="202101"); a.add_argument("--hs", default="854232,854231"); a.set_defaults(fn=cmd_customs)
    a = sub.add_parser("collect-trends"); a.add_argument("--days", type=int, default=90); a.set_defaults(fn=cmd_trends)
    sub.add_parser("daily-update").set_defaults(fn=cmd_daily)
    a = sub.add_parser("collect-dart"); a.add_argument("--from-year", type=int, default=date.today().year - 5); a.set_defaults(fn=cmd_dart)
    a = sub.add_parser("collect-sec"); a.add_argument("--from-year", type=int, default=date.today().year - 5); a.set_defaults(fn=cmd_sec)
    a = sub.add_parser("inspect-sec"); a.add_argument("ticker"); a.add_argument("--tag", default=None); a.set_defaults(fn=cmd_inspect_sec)
    a = sub.add_parser("collect-dart-q"); a.add_argument("--from-year", type=int, default=date.today().year - 2); a.set_defaults(fn=cmd_dart_q)
    a = sub.add_parser("collect-sec-q"); a.add_argument("--from-year", type=int, default=date.today().year - 2); a.set_defaults(fn=cmd_sec_q)
    sub.add_parser("build-quarterly").set_defaults(fn=cmd_build_quarterly)
    sub.add_parser("show-ttm").set_defaults(fn=cmd_show_ttm)
    sub.add_parser("build-features").set_defaults(fn=cmd_build_features)
    a = sub.add_parser("collect-community"); a.add_argument("--days", type=int, default=30)
    a.add_argument("--sources", default="naver_blog,naver_cafe"); a.add_argument("--pages", type=int, default=2)
    a.set_defaults(fn=cmd_community)
    a = sub.add_parser("pulse"); a.add_argument("--tickers", help="쉼표 구분 (생략 시 전체)")
    a.add_argument("--max-age", type=int, default=0, help="이 분 수 안에 갱신했으면 API 생략")
    a.add_argument("--market-hours", action="store_true", help="평일 08:50~15:40 KST 에만 실행")
    a.set_defaults(fn=cmd_pulse)
    a = sub.add_parser("show-pulse"); a.add_argument("ticker"); a.set_defaults(fn=cmd_show_pulse)
    a = sub.add_parser("score-sentiment"); a.add_argument("--method", choices=["lexicon", "finbert"])
    a.add_argument("--limit", type=int); a.add_argument("--rescore", action="store_true", help="기존 점수 지우고 다시 채점")
    a.set_defaults(fn=cmd_sentiment)
    a = sub.add_parser("show-features"); a.add_argument("ticker"); a.set_defaults(fn=cmd_show_features)
    a = sub.add_parser("export-features"); a.add_argument("--out", default="features.json"); a.set_defaults(fn=cmd_export_features)
    a = sub.add_parser("collect-company-info"); a.add_argument("--skip-kr-shares", action="store_true"); a.set_defaults(fn=cmd_company_info)
    sub.add_parser("sync-kospi200").set_defaults(fn=cmd_sync_kospi200)
    sub.add_parser("probe-bulk").set_defaults(fn=cmd_probe_bulk)
    a = sub.add_parser("collect-report-exposure", help="사업보고서 원문 → 환율 민감도·외화 자산부채·수출 비중·외환 파생")
    a.add_argument("--force", action="store_true", help="이미 읽은 원문도 다시")
    a.set_defaults(fn=cmd_report_exposure)
    a = sub.add_parser("probe-dart-doc", help="사업보고서 원문의 노출 관련 표 모양 출력 (DB에 쓰지 않음)")
    a.add_argument("--tickers", default="005930,005380,051910,017670,105560,000660,035420,068270")
    a.add_argument("--max-tables", type=int, default=25)
    a.add_argument("--max-rows", type=int, default=14)
    a.add_argument("--max-chars", type=int, default=1800)
    a.add_argument("--extract", action="store_true", help="추출기 결과와 실패 사례 출력")
    a.add_argument("--show-misses", action="store_true")
    a.add_argument("--limit", type=int, default=1000)
    a.set_defaults(fn=cmd_probe_dart_doc)
    a = sub.add_parser("collect-dart-multi"); a.add_argument("--from-year", type=int, default=date.today().year - 5)
    a.add_argument("--max-seconds", type=float, default=None, help="이 시간을 넘기면 멈추고 다음 실행에서 이어서")
    a.add_argument("--reports", default="11011", help="11011 사업, 11013 1분기, 11012 반기, 11014 3분기")
    a.set_defaults(fn=cmd_dart_multi)
    sub.add_parser("build-peer-groups").set_defaults(fn=cmd_build_peers)
    sub.add_parser("show-valuation").set_defaults(fn=cmd_show_valuation)
    a = sub.add_parser("collect-macro"); a.add_argument("--years", type=int, default=5); a.set_defaults(fn=cmd_macro)
    a = sub.add_parser("tick"); a.add_argument("--budget", type=float, default=1200, help="새 작업을 시작할 수 있는 시간(초)")
    a.add_argument("--only", default=None, help="이 묶음(group) 또는 작업 이름만"); a.add_argument("--summary", default=None, help="결과표(마크다운)를 덧붙일 파일")
    a.add_argument("--lane", default=None, help="병렬 실행 줄 하나만 (jobs.LANES: 준비, dart, public, naver, naver_etc, global, 계산)")
    a.set_defaults(fn=cmd_tick)
    a = sub.add_parser("collect-etf"); a.add_argument("--days", type=int, default=365); a.set_defaults(fn=cmd_etf)
    a = sub.add_parser("collect-finnhub"); a.add_argument("--days", type=int, default=7); a.set_defaults(fn=cmd_finnhub)
    a = sub.add_parser("collect-youtube"); a.add_argument("--days", type=int, default=7)
    a.add_argument("--videos", type=int, default=5); a.add_argument("--comments", type=int, default=100)
    a.add_argument("--top-kr", type=int, default=None, help="국내는 시총 상위 N종목만 (할당량)")
    a.add_argument("--order", default="viewCount", choices=["viewCount", "date", "relevance"],
                   help="영상 정렬 (기본 조회수 순: 댓글이 있는 영상 위주)")
    a.set_defaults(fn=cmd_youtube)
    a = sub.add_parser("collect-investor-flows"); a.add_argument("--days", type=int, default=10)
    a.set_defaults(fn=cmd_investor_flows)
    a = sub.add_parser("collect-lending"); a.add_argument("--days", type=int, default=10)
    a.add_argument("--max-days", type=int, default=None, help="한 번에 받을 최대 거래일 수 (최근 날짜부터)")
    a.set_defaults(fn=cmd_lending)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except RuntimeError as exc:
        print(f"[FAIL] {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
