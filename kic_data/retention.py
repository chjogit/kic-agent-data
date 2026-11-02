"""문서 보관 기한(기본 14일): 기한을 넘긴 행을 지우되, 지우기 전에 일별 집계를 남깁니다.

- 대상: 블로그·카페(naver_blog·naver_cafe, kic-opinion) + 뉴스·댓글(naver_news·finnhub_news·youtube_comment, 메인).
  지우지 않음: naver_news_backfill(대주주·경영진 1년 자료)·dart_report 및 그 밖의 소스, core.youtube_video(영상 메타).
- 기준: 발행일(published_at, 없으면 수집일)이 오늘(KST) − 보관 일수보다 이전인 문서. 보관 일수는 RETENTION_DAYS 로 변경.
- 한 배치마다 한 트랜잭션: 집계 더하기 → 문서 삭제(core.doc_sentiment·rag.chunk 는 ON DELETE CASCADE 로 함께 지워짐).
  집계를 더한 문서는 같은 트랜잭션에서 지워지므로 다시 실행해도 같은 문서를 두 번 세지 않습니다.
- 집계는 에이전트 3 이 문서에서 직접 세는 것과 같은 조건(종목 있음·발행일 있음)인 문서만 남깁니다.
  종목이나 발행일이 없는 문서는 에이전트 3 이 쓰지 않으므로 집계 없이 지웁니다.
- 지운 공간은 파일 크기를 줄이지 않고 재사용됩니다. 일반 VACUUM(ANALYZE) 만 하고 VACUUM FULL 은 하지 않습니다.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from . import db
from .config import env

OPINION_SOURCES = tuple(db.OPINION_SOURCES)                          # kic-opinion (분리 전에는 메인)
NEWS_SOURCES = ("naver_news", "finnhub_news", "youtube_comment")     # 메인
SOURCES = OPINION_SOURCES + NEWS_SOURCES                              # 지울 수 있는 소스 전체 (이 밖은 지우지 않음)
DEFAULT_DAYS = 14
BATCH = 5000
VACUUM_TABLES = ("rag.document", "rag.chunk", "core.doc_sentiment", "core.doc_daily_agg")


def retention_days(days: int | None = None) -> int:
    if days:
        return max(1, int(days))
    try:
        return max(1, int(env("RETENTION_DAYS") or DEFAULT_DAYS))
    except ValueError:
        return DEFAULT_DAYS


def cutoff_date(today: date, days: int) -> date:
    """이 날짜보다 이전에 발행된 문서가 지울 대상."""
    return today - timedelta(days=days)


def aggregate(rows: list[tuple]) -> dict[tuple, tuple[int, int, float]]:
    """[(company_id, source, day, score|None), ...] → {(company_id, source, day): (n_docs, n_scored, score_sum)}.
    종목이나 발행일이 없는 행은 에이전트 3 이 쓰지 않으므로 집계하지 않습니다."""
    acc: dict[tuple, list] = defaultdict(lambda: [0, 0, 0.0])
    for company_id, source, day, score in rows:
        if company_id is None or day is None:
            continue
        a = acc[(company_id, source, day)]
        a[0] += 1
        if score is not None:
            a[1] += 1
            a[2] += float(score)
    return {k: (v[0], v[1], v[2]) for k, v in acc.items()}


def estimate(counts: dict[str, tuple[int, int, int]], row_bytes: dict[str, float]) -> dict[str, dict]:
    """소스별 (문서, 청크, 감성) 건수 × 표별 평균 행 크기(인덱스·TOAST 포함) → 소스별 예상 회수 바이트."""
    out = {}
    for src, (docs, chunks, sents) in counts.items():
        b = docs * row_bytes.get("doc", 0) + chunks * row_bytes.get("chunk", 0) + sents * row_bytes.get("sentiment", 0)
        out[src] = {"docs": docs, "chunks": chunks, "sentiments": sents, "est_bytes": int(b)}
    return out


def purge(conn, today: date, days: int | None = None, dry_run: bool = False, batch: int = BATCH,
          sources: tuple | list = OPINION_SOURCES, vacuum: bool = False) -> dict:
    """보관 기한이 지난 문서를 집계 후 삭제. 결과: {days, cutoff, sources, by_source{src: {docs, chunks, sentiments,
    est_bytes}}, targets, est_bytes, deleted, agg_rows, vacuumed}."""
    sources = [s for s in sources if s in SOURCES]          # 지우지 않는 소스(백필 뉴스·공시 등)는 어떤 경우에도 빠짐
    days = retention_days(days)
    cutoff = cutoff_date(today, days)
    by_source = estimate(db.retention_counts(conn, sources, cutoff), db.retention_row_bytes(conn)) if sources else {}
    res = {"days": days, "cutoff": cutoff.isoformat(), "sources": sources, "by_source": by_source,
           "targets": sum(v["docs"] for v in by_source.values()),
           "est_bytes": sum(v["est_bytes"] for v in by_source.values()),
           "deleted": 0, "agg_rows": 0, "vacuumed": False}
    if dry_run or not res["targets"]:
        return res
    while True:
        rows = db.retention_batch(conn, sources, cutoff, batch)   # [(doc_id, company_id, source, published_at, score)]
        if not rows:
            break
        agg = aggregate([(c, s, d, sc) for _, c, s, d, sc in rows])
        db.retention_apply(conn, agg, [r[0] for r in rows])       # 집계 더하기 + 삭제 (한 트랜잭션)
        res["deleted"] += len(rows)
        res["agg_rows"] += len(agg)
    if vacuum and res["deleted"]:
        db.vacuum_analyze(conn, VACUUM_TABLES)
        res["vacuumed"] = True
    return res
