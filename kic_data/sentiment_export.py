"""감성 집계 내보내기 (#71, specs/sentiment-export.md): 종목 × 출처 × 기간 창(14·7·3·1일) 집계를 만들어
kic-opinion Object Storage 의 `sentiment/latest/` 에 덮어쓴다.

- 집계는 DB 값을 그대로 센 것이다(추정·보정 없음). 문서 0건이어도 행을 남긴다.
- 원문·주소·작성자는 넣지 않는다(건수·비율·평균 점수·날짜뿐).
- 만들기(집계·직렬화)는 모두 메모리에서 끝낸 뒤에야 업로드한다. 업로드는 `sentiment/latest/` 밖을 절대 건드리지 않는다.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
WINDOWS = (14, 7, 3, 1)
PREFIX = "sentiment/latest/"
TOTAL = "합계"
LABELS = ("positive", "neutral", "negative", "none")
METHOD_NOTE = "키워드 사전(lexicon) 기준선 — 사람 라벨 정확도 56.6%. 'none'(무관)은 사전에 걸리는 감성 표현이 없는 문서."
COLUMNS = ("ticker", "name", "market", "source", "window_days", "as_of_date", "n_docs", "n_labeled", "n_unlabeled",
           "positive", "neutral", "negative", "none", "positive_ratio", "neutral_ratio", "negative_ratio", "none_ratio",
           "avg_score", "n_scored", "prev_avg_score", "change", "change_status", "last_doc_date", "last_collected_at")

# 기간 창 하나(kind=cur: 기준일 포함 N일, prev: 그 직전 같은 길이)를 한 번에 센다. published_at 은 날짜(date)라 KST 달력일 기준.
AGG_SQL = """
SELECT d.company_id, d.source, w.win, w.kind,
       count(*) AS n_docs, count(s.doc_id) AS n_labeled,
       count(*) FILTER (WHERE s.label = 'positive') AS positive, count(*) FILTER (WHERE s.label = 'neutral') AS neutral,
       count(*) FILTER (WHERE s.label = 'negative') AS negative, count(*) FILTER (WHERE s.label = 'none') AS none,
       count(s.score) AS n_scored, coalesce(sum(s.score), 0) AS sum_score,
       max(d.published_at) AS last_doc_date, max(d.created_at) AS last_collected_at
FROM rag.document d
JOIN unnest(%(win)s::int[], %(kind)s::text[], %(lo)s::date[], %(hi)s::date[]) AS w(win, kind, lo, hi)
  ON d.published_at > w.lo AND d.published_at <= w.hi
LEFT JOIN core.doc_sentiment s ON s.doc_id = d.doc_id
WHERE d.source = ANY(%(sources)s)
GROUP BY d.company_id, d.source, w.win, w.kind
"""
SOURCES_SQL = "SELECT DISTINCT d.source FROM rag.document d JOIN core.doc_sentiment s ON s.doc_id = d.doc_id"
COMPANIES_SQL = "SELECT company_id, ticker, name, market FROM core.company ORDER BY company_id"


@contextmanager
def opinion_reader(main_conn):
    """kic-opinion 읽기 전용 연결(없으면 메인 연결 그대로). db.opinion() 과 달리 종목·시퀀스 동기화(쓰기)를 하지 않는다."""
    from . import db

    conn = db.connect_opinion(read_only=True)
    try:
        yield conn if conn is not None else main_conn
    finally:
        if conn is not None:
            conn.close()


def window_specs(as_of: date) -> dict[str, list]:
    """AGG_SQL 인자: 창마다 cur = (as_of-N, as_of], prev = (as_of-2N, as_of-N]."""
    win, kind, lo, hi = [], [], [], []
    for n in WINDOWS:
        for k, a, b in (("cur", as_of - timedelta(days=n), as_of), ("prev", as_of - timedelta(days=2 * n), as_of - timedelta(days=n))):
            win.append(n), kind.append(k), lo.append(a), hi.append(b)
    return {"win": win, "kind": kind, "lo": lo, "hi": hi}


def fetch(conn, sources: list[str], as_of: date) -> list[dict]:
    """한 연결에서 (company_id, source, win, kind) 별 집계 행."""
    if not sources:
        return []
    with conn.cursor() as cur:
        cur.execute(AGG_SQL, {**window_specs(as_of), "sources": list(sources)})
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.commit()
    return rows


def observed_sources(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(SOURCES_SQL)
        out = [r[0] for r in cur.fetchall()]
    conn.commit()
    return out


def _empty() -> dict:
    return {"n_docs": 0, "n_labeled": 0, "positive": 0, "neutral": 0, "negative": 0, "none": 0, "n_scored": 0, "sum_score": 0.0,
            "last_doc_date": None, "last_collected_at": None}


def _merge(a: dict, b: dict) -> dict:
    out = {k: a[k] + b[k] for k in ("n_docs", "n_labeled", "positive", "neutral", "negative", "none", "n_scored")}
    out["sum_score"] = float(a["sum_score"]) + float(b["sum_score"])
    for k in ("last_doc_date", "last_collected_at"):
        vals = [v for v in (a[k], b[k]) if v is not None]
        out[k] = max(vals) if vals else None
    return out


def _ratio(n: int, total: int) -> float | None:
    return round(n / total, 4) if total else None


def build_rows(companies: list[dict], sources: list[str], agg: list[dict], as_of: date) -> list[dict]:
    """종목 × (출처들 + 합계) × 창 → 행. 비율은 감성 결과가 있는 문서(n_labeled) 기준, 평균 점수는 점수가 있는 문서(n_scored) 기준.
    직전 같은 길이 기간의 점수가 없으면 변화는 '비교 불가'(예: 14일 창은 보관 기간이 14일이라 직전 기간 자료가 없다)."""
    cell: dict[tuple, dict] = {}
    for r in agg:
        cell[(r["company_id"], r["source"], r["win"], r["kind"])] = {k: r[k] for k in _empty()}
    rows = []
    for c in companies:
        for n in WINDOWS:
            per_src = {}
            for src in sources:
                per_src[src] = {k: cell.get((c["company_id"], src, n, k), _empty()) for k in ("cur", "prev")}
            total = {k: _empty() for k in ("cur", "prev")}
            for src in sources:
                for k in ("cur", "prev"):
                    total[k] = _merge(total[k], per_src[src][k])
            for src, parts in [*per_src.items(), (TOTAL, total)]:
                rows.append(_row(c, src, n, parts["cur"], parts["prev"], as_of))
    return rows


def _row(c: dict, src: str, n: int, cur: dict, prev: dict, as_of: date) -> dict:
    avg = round(float(cur["sum_score"]) / cur["n_scored"], 4) if cur["n_scored"] else None
    pavg = round(float(prev["sum_score"]) / prev["n_scored"], 4) if prev["n_scored"] else None
    if avg is None:
        change, status = None, "비교 불가(이번 기간 점수 없음)"
    elif pavg is None:
        change, status = None, "비교 불가(직전 기간 자료 없음)"
    else:
        change, status = round(avg - pavg, 4), "비교 가능"
    lab = cur["n_labeled"]
    last = cur["last_doc_date"]
    got = cur["last_collected_at"]
    return {"ticker": c["ticker"], "name": c["name"], "market": c["market"], "source": src, "window_days": n, "as_of_date": as_of.isoformat(),
            "n_docs": cur["n_docs"], "n_labeled": lab, "n_unlabeled": cur["n_docs"] - lab,
            "positive": cur["positive"], "neutral": cur["neutral"], "negative": cur["negative"], "none": cur["none"],
            "positive_ratio": _ratio(cur["positive"], lab), "neutral_ratio": _ratio(cur["neutral"], lab),
            "negative_ratio": _ratio(cur["negative"], lab), "none_ratio": _ratio(cur["none"], lab),
            "avg_score": avg, "n_scored": cur["n_scored"], "prev_avg_score": pavg, "change": change, "change_status": status,
            "last_doc_date": last.isoformat() if last else None,
            "last_collected_at": got.astimezone(KST).isoformat(timespec="seconds") if got else None}


# ---------------------------------------------------------------- 파일 만들기 (모두 메모리에서)

def make_files(rows: list[dict], as_of: datetime, generated_at: datetime, run: dict) -> dict[str, bytes]:
    """{경로(prefix 포함): 바이트}. 창마다 .json + .csv, 마지막에 _meta.json(파일 목록·크기·sha256 포함)."""
    files: dict[str, bytes] = {}
    for n in WINDOWS:
        part = [r for r in rows if r["window_days"] == n]
        body = {"as_of": as_of.isoformat(timespec="seconds"), "window_days": n, "generated_at": generated_at.isoformat(timespec="seconds"),
                "columns": list(COLUMNS), "rows": part}
        files[f"{PREFIX}window={n}d.json"] = json.dumps(body, ensure_ascii=False, indent=1).encode("utf-8")
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(part)
        files[f"{PREFIX}window={n}d.csv"] = ("﻿" + buf.getvalue()).encode("utf-8")           # BOM: Excel 한글
    meta = {"as_of": as_of.isoformat(timespec="seconds"), "generated_at": generated_at.isoformat(timespec="seconds"), "complete": True,
            "windows": list(WINDOWS), "window_rule": "기준일(KST 달력일) 포함 N일. 문서 날짜(published_at)는 날짜 단위라 시각 단위 창은 아님.",
            "compare_rule": "변화 = 이번 기간 평균 점수 − 직전 같은 길이 기간 평균 점수. 직전 기간 자료가 없으면 '비교 불가'(뉴스·댓글은 보관 14일이라 14일 창은 대부분 비교 불가).",
            "ratio_rule": "비율 = 해당 라벨 건수 / 감성 결과가 있는 문서 수(n_labeled). 평균 점수 = 점수가 있는 문서(n_scored) 평균, -1~+1.",
            "sentiment_method": METHOD_NOTE, "labels": {"positive": "긍정", "neutral": "중립", "negative": "부정", "none": "무관(감성 표현 없음)"},
            "privacy": "원문·제목·주소·작성자는 포함하지 않음(건수·비율·평균 점수·날짜만).",
            "run": run,
            "files": {k: {"bytes": len(v), "sha256": hashlib.sha256(v).hexdigest()} for k, v in sorted(files.items())}}
    files[f"{PREFIX}_meta.json"] = json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8")
    return files


# ---------------------------------------------------------------- 업로드 (덮어쓰기)

def _guard(key: str, prefix: str = PREFIX) -> str:
    if not key.startswith(prefix) or ".." in key:
        raise ValueError(f"덮어쓰기 범위({prefix}) 밖 경로는 건드리지 않습니다: {key}")
    return key


def upload(store, files: dict[str, bytes], prefix: str = PREFIX) -> dict:
    """새 파일을 모두 쓴 뒤(_meta.json 마지막) `prefix` 아래의 남은 옛 파일만 지운다. 하나라도 쓰기에 실패하면 지우지 않고 예외.
    store: put(key, data, content_type) · list(prefix) · delete(key) · size(key)."""
    for k in files:
        _guard(k, prefix)
    order = sorted(files, key=lambda k: (k.endswith("_meta.json"), k))
    for k in order:
        store.put(k, files[k], "application/json" if k.endswith(".json") else "text/csv; charset=utf-8")
    for k in files:
        got = store.size(k)
        if got != len(files[k]):
            raise RuntimeError(f"업로드 크기 불일치: {k} 올린 {len(files[k])} / 저장 {got}")
    stale = [k for k in store.list(prefix) if k not in files]
    for k in stale:
        store.delete(_guard(k, prefix))
    return {"uploaded": len(files), "bytes": sum(len(v) for v in files.values()), "deleted": sorted(stale)}


def run_id(files: dict[str, bytes]) -> str:
    return hashlib.sha256(b"".join(files[k] for k in sorted(files))).hexdigest()[:12]
