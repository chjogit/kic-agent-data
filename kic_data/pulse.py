"""장중 여론 펄스: 종목별로 공식 검색 API 를 가볍게 호출해 '오늘의 뉴스·블로그 톤' 을 갱신합니다.

- 종목당 호출 3회: 뉴스 최신순 100건, 블로그 '○○ 주가' 최신순 100건, 카페 '○○ 주가' 1건(total 만).
  톤은 작성일이 확실한 뉴스·블로그로, 언급량은 세 출처의 검색 결과 누적 수(total) 차이로 계산합니다.
  (큰 종목은 최신 100건이 전부 오늘 글이라 '오늘 글 수' 를 표본으로 세면 실제보다 작게·왜곡되어 잡힘)
- 사용 방식
  A) 정규장 15분 간격 일괄 갱신:  python -m kic_data.cli pulse        (scripts/pulse.bat + 작업 스케줄러)
  B) 화면에서 종목을 누를 때:      refresh_ticker(market, ticker)  → dict  (웹 백엔드에서 import 해서 호출)
     max_age_min 안에 이미 갱신했으면 API 를 다시 부르지 않고 마지막 값을 돌려줍니다 (시연 중 연타 대비).
- 네이버 검색 API 일 한도 25,000회 기준: 20종목 × 3회 × 26회(09:00~15:30, 15분) ≈ 1,560회.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import db, features, sentiment
from .collectors import community, news

KST = ZoneInfo("Asia/Seoul")
MIN_DOCS = 3


def kst_now() -> datetime:
    return datetime.now(KST)


def fetch_today(session, market: str, ticker: str, name: str | None, today: date) -> tuple[dict[str, list[dict]], dict]:
    """(오늘 날짜(KST) 글, {출처: (검색어, total)}). 필터는 일별 수집과 동일."""
    term, must = news.terms_for(market, ticker, name)
    seen: set[str] = set()
    news_rows = []
    news_items, news_total = news.fetch_news_page(session, term, display=100, sort="date")
    blog_q = f"{term} 주가"
    blog_items, blog_total = community.fetch_page(session, "naver_blog", blog_q)
    _, cafe_total = community.fetch_page(session, "naver_cafe", blog_q, display=1)
    totals = {"naver_news": (term, news_total), "naver_blog": (blog_q, blog_total), "naver_cafe": (blog_q, cafe_total)}
    for r in news_items:
        if r["published_at"] != today or not news.matches(r, must) or news.is_market_wrap(r["title"]):
            continue
        key = news._title_key(r["title"])
        if key in seen:
            continue
        seen.add(key)
        news_rows.append(r)
    blog_rows = []
    for r in blog_items:
        text = f"{r['title']} {r['summary']}"
        if r["published_at"] == today and any(m.lower() in text.lower() for m in must) and not community.is_spam(text, must):
            blog_rows.append(r)
    return {"naver_news": news_rows, "naver_blog": blog_rows}, totals


def new_since(prev: dict, totals: dict) -> int | None:
    """같은 날 직전 스냅샷 대비 새 글 수 (출처 합). 직전이 없으면 None. 음수(검색 색인 정리)는 0으로."""
    diffs = [max(0, t - prev[src]) for src, (_, t) in totals.items() if t is not None and src in prev]
    return sum(diffs) if diffs else None


def summarize(reactions: list[dict], today: date) -> dict:
    """fetch_reactions 결과(최근 30일) → 오늘 스냅샷 값. 순수 함수."""
    dated = ("naver_news", "naver_blog")
    todays = [r for r in reactions if r["source"] in dated and r["published_at"] == today]
    scored = [r["score"] for r in todays if r["score"] is not None]
    labels = [sentiment.label_of(s) for s in scored]
    return {
        "trade_date": today,
        "n_today": len(todays),
        "n_news": sum(1 for r in todays if r["source"] == "naver_news"),
        "n_blog": sum(1 for r in todays if r["source"] == "naver_blog"),
        "n_positive": labels.count("positive"),
        "n_negative": labels.count("negative"),
        "sentiment": (sum(scored) / len(scored)) if len(scored) >= MIN_DOCS else None,
        "mentions_vs_30d": None,  # refresh() 에서 누적 total 증가분으로 채움
    }


def refresh(conn, session, company_id: int, market: str, ticker: str, name: str | None,
            method: str = "lexicon", max_age_min: int = 0) -> dict:
    """한 종목 갱신 → 스냅샷 dict (cached=True 면 API 를 부르지 않은 값)."""
    now = kst_now()
    if max_age_min > 0:
        last = db.last_pulse(conn, company_id)
        if last and now - last["captured_at"] < timedelta(minutes=max_age_min):
            return {**last, "cached": True}
    today = now.date()
    new_docs = 0
    docs_by_src, totals = fetch_today(session, market, ticker, name, today)
    for src, rows in docs_by_src.items():
        new_docs += db.insert_new_documents(conn, company_id, src, rows)
    since_last = new_since(db.last_mention_totals(conn, company_id, today), totals)
    db.insert_mention_counts(conn, company_id, now, today, totals)
    docs = db.docs_without_sentiment(conn, method)
    if docs:
        scores = sentiment.score_texts([f"{t or ''} {s or ''}" for _, t, s in docs], method)
        db.upsert_sentiment(conn, [(d, sc, sentiment.label_of(sc), method) for (d, _, _), sc in zip(docs, scores)])
    snap = summarize(db.fetch_reactions(conn, company_id, today - timedelta(days=30)), today)
    snap["mentions_vs_30d"] = features.mention_ratios(db.fetch_mention_daily(conn, company_id),
                                                      ("naver_news", "naver_blog", "naver_cafe"), 1, 6)[0]
    snap.update(new_docs=new_docs, method=method, captured_at=now, new_since_last=since_last)
    db.insert_pulse(conn, company_id, snap)
    return {**snap, "cached": False}


def refresh_ticker(market: str, ticker: str, max_age_min: int = 5, method: str = "lexicon") -> dict:
    """웹 백엔드용 진입점: 종목 하나를 즉시 갱신(또는 5분 캐시)하고 오늘 추이까지 돌려줍니다."""
    from .http_util import make_session

    with db.connect() as conn:
        found = db.find_company(conn, market, ticker)
        if not found:
            raise ValueError(f"등록되지 않은 종목: {market}:{ticker}")
        cid, name = found
        snap = refresh(conn, make_session(), cid, market, ticker, name, method, max_age_min)
        snap["history"] = db.pulse_history(conn, cid, snap["trade_date"])
    return {"market": market, "ticker": ticker, "name": name, **snap}
