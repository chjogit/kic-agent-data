"""블로그·카페 문서 보관 기한: 기한을 넘긴 행을 지우되, 지우기 전에 일별 집계를 남깁니다.

- 대상: naver_blog · naver_cafe 만 (뉴스·공시·댓글은 지우지 않음). 보관 일수 기본 30일(RETENTION_DAYS 로 변경).
- 기준: 발행일(published_at, 없으면 수집일)이 오늘(KST) − 보관 일수보다 이전인 문서.
- 한 배치마다 한 트랜잭션: 집계 더하기 → 문서 삭제(core.doc_sentiment 는 ON DELETE CASCADE 로 함께 지워짐).
  집계를 더한 문서는 같은 트랜잭션에서 지워지므로 다시 실행해도 같은 문서를 두 번 세지 않습니다.
- 지운 공간은 곧바로 파일 크기를 줄이지 않고 재사용됩니다 (VACUUM FULL 은 하지 않음).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from . import db
from .config import env

SOURCES = ("naver_blog", "naver_cafe")
DEFAULT_DAYS = 30
BATCH = 5000
# 문서 행 + 인덱스 + 감성 점수 행의 평균 크기 (10-30 측정: rag.document 약 0.70KB + doc_sentiment 약 0.09KB)
ROW_BYTES = 810


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
    """[(company_id, source, day, score|None), ...] → {(company_id, source, day): (n_docs, n_scored, score_sum)}"""
    acc: dict[tuple, list] = defaultdict(lambda: [0, 0, 0.0])
    for company_id, source, day, score in rows:
        a = acc[(company_id, source, day)]
        a[0] += 1
        if score is not None:
            a[1] += 1
            a[2] += float(score)
    return {k: (v[0], v[1], v[2]) for k, v in acc.items()}


def purge(conn, today: date, days: int | None = None, dry_run: bool = False, batch: int = BATCH) -> dict:
    """보관 기한이 지난 블로그·카페 문서를 집계 후 삭제. 결과: {cutoff, days, targets, deleted, agg_rows, est_bytes}."""
    days = retention_days(days)
    cutoff = cutoff_date(today, days)
    res = {"days": days, "cutoff": cutoff.isoformat(), "targets": db.retention_count(conn, list(SOURCES), cutoff),
           "deleted": 0, "agg_rows": 0, "est_bytes": 0}
    res["est_bytes"] = res["targets"] * ROW_BYTES
    if dry_run or not res["targets"]:
        return res
    while True:
        rows = db.retention_batch(conn, list(SOURCES), cutoff, batch)   # [(doc_id, company_id, source, day, score)]
        if not rows:
            break
        agg = aggregate([(c, s, d, sc) for _, c, s, d, sc in rows])
        db.retention_apply(conn, agg, [r[0] for r in rows])             # 집계 더하기 + 삭제 (한 트랜잭션)
        res["deleted"] += len(rows)
        res["agg_rows"] += len(agg)
    return res
