"""DART A(정기공시)·B(주요사항보고) 원문 보관: 접수번호 기준으로 Object Storage 에 올리고 DB 에는 메타만 남깁니다.

- 대상: 이미 수집된 core.disclosure 중 국내 종목의 category A·B. 최근 접수일부터 올립니다.
- 멱등: ops.dart_archive 에 접수번호가 있으면 다시 올리지 않습니다 (저장소에 파일만 있고 메타가 없으면 메타만 채움).
- 원문 없음: DART 가 014(파일 없음)로 답한 공시는 ops.dart_archive_skip 에 기록해 다시 시도하지 않고 실패로 세지 않습니다.
- 용량: 올리기 전에 대상 건수·예상 용량을 내고, 누적 크기가 상한(기본 4GB)에 닿으면 멈추고 못 올린 건수를 기록합니다.
- 시간 예산: max_seconds 를 넘기면 다음 파일을 받지 않고 멈춥니다 (남은 건수 기록, 다음 실행이 이어 올림).
- 진행: progress 콜백으로 every 건마다 진행 상황을 넘깁니다 (끊겨도 그때까지의 진행 줄이 로그에 남도록).
- 재사용: fetch_files() 는 보관본이 있으면 DART 를 다시 부르지 않고 원문을 돌려줍니다 (report-exposure 파서 등).
"""
from __future__ import annotations

import time

from . import db
from .collectors import dart_doc
from .config import env

CATEGORIES = ("A", "B")
DEFAULT_CAP_GB = 4.0
# 올리기 전 예상 크기 (바이트). 보관본이 쌓이면 유형별 평균으로 대체. 정기공시 원문 약 10MB, 주요사항보고는 작음.
DEFAULT_AVG = {"A": 8 * 1024 * 1024, "B": 300 * 1024}
MAX_SINGLE = 25 * 1024 * 1024  # 파일 하나의 상한 추정: 누적 + 이만큼이 상한을 넘으면 다음 파일을 받지 않음


def object_key(rcept_no: str, category: str) -> str:
    return f"dart/{category}/{rcept_no[:4]}/{rcept_no}.zip"


def cap_bytes(cap_gb: float | None = None) -> int:
    try:
        gb = cap_gb if cap_gb is not None else float(env("DART_ARCHIVE_CAP_GB") or DEFAULT_CAP_GB)
    except ValueError:
        gb = DEFAULT_CAP_GB
    return int(gb * 1024 ** 3)


PROGRESS_EVERY = 25


def estimate(candidates: list[tuple], avg: dict[str, float] | None = None) -> int:
    avg = {**DEFAULT_AVG, **(avg or {})}
    return int(sum(avg.get(c[3], avg["B"]) for c in candidates))


def avg_basis(measured: dict[str, float] | None) -> dict[str, tuple[float, str]]:
    """유형별 (예상 평균 바이트, 근거) — 보관 기록이 있으면 '실측', 없으면 '추정'(DEFAULT_AVG)."""
    measured = measured or {}
    return {c: (measured[c], "실측") if c in measured else (float(DEFAULT_AVG[c]), "추정") for c in CATEGORIES}


def run(conn, session, store, since: str | None = None, limit: int | None = None, cap_gb: float | None = None,
        dry_run: bool = False, fetch_raw=None, max_seconds: float | None = None, progress=None,
        every: int = PROGRESS_EVERY, clock=time.monotonic) -> dict:
    """보관 실행. 결과 dict 의 키: targets, estimate_bytes, avg(유형별 (평균 바이트, 근거)), used_bytes, cap_bytes,
    uploaded, uploaded_bytes, skipped_cap(상한으로 못 올린 건수), skipped_time(시간 예산으로 다음 실행에 넘긴 건수),
    no_document(원문 없음 014 로 영구 건너뜀한 접수번호), failed[(접수번호, 이유)], skipped(저장소 설정 없음 등 사유 또는 None).
    progress(done, res) 는 every 건마다 불립니다 (done = 처리한 대상 수)."""
    if fetch_raw is None:
        fetch_raw = dart_doc.fetch_document_raw
    cands = db.archive_candidates(conn, list(CATEGORIES), since, limit)
    used = db.archive_used_bytes(conn)
    cap = cap_bytes(cap_gb)
    measured = db.archive_avg_bytes(conn)
    res = {"targets": len(cands), "estimate_bytes": estimate(cands, measured), "avg": avg_basis(measured),
           "used_bytes": used, "cap_bytes": cap, "uploaded": 0, "uploaded_bytes": 0, "skipped_cap": 0,
           "skipped_time": 0, "no_document": [], "failed": [], "skipped": None}
    if dry_run:
        return res
    if store is None:
        res["skipped"] = "Object Storage 접근 정보 없음 (건너뜀)"
        return res
    start = clock()
    for i, (rcept_no, company_id, ticker, category, rcept_dt) in enumerate(cands):
        if progress and i and i % every == 0:
            progress(i, res)
        if used + MAX_SINGLE > cap:
            res["skipped_cap"] = len(cands) - i  # 최근순이라 남은 것은 모두 더 오래된 공시
            break
        if max_seconds is not None and clock() - start >= max_seconds:
            res["skipped_time"] = len(cands) - i  # 멱등이라 다음 실행이 여기서부터 이어 올림
            break
        key = object_key(rcept_no, category)
        try:
            data_size = store.size(key)  # 이미 올라가 있으면 다시 받지 않고 메타만 채움
            if data_size is None:
                data = fetch_raw(session, rcept_no)
                store.put(key, data)
                data_size = len(data)
        except dart_doc.NoDocument as exc:  # 014: 영구 건너뜀으로 기록, 실패가 아님
            db.archive_mark_skip(conn, rcept_no, str(exc))
            res["no_document"].append(rcept_no)
            continue
        except Exception as exc:  # noqa: BLE001  원문 없음·업로드 오류는 기록하고 다음 건으로
            res["failed"].append((rcept_no, f"{type(exc).__name__}: {exc}"[:160]))
            continue
        db.archive_save(conn, rcept_no, company_id, ticker, category, key, data_size)
        used += data_size
        res["uploaded"] += 1
        res["uploaded_bytes"] += data_size
    return res


def load_original(conn, store, rcept_no: str) -> bytes | None:
    """보관본 원문 zip. 보관 기록이 없거나 저장소를 쓸 수 없으면 None (호출 쪽이 DART 에서 받음)."""
    if store is None:
        return None
    key = db.archive_key(conn, rcept_no)
    if not key:
        return None
    try:
        return store.get(key)
    except Exception:  # noqa: BLE001  보관본을 못 읽으면 DART 로 대체
        return None


def fetch_files(conn, store, session, rcept_no: str) -> dict[str, str]:
    """{파일명: 본문} — 보관본이 있으면 DART 를 부르지 않습니다."""
    raw = load_original(conn, store, rcept_no)
    if raw is not None:
        return dart_doc.parse_zip(raw)
    return dart_doc.fetch_document(session, rcept_no)
