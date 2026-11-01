"""DART A(정기공시)·B(주요사항보고) 원문 보관: 접수번호 기준으로 Object Storage 에 올리고 DB 에는 메타만 남깁니다.

- 대상: 이미 수집된 core.disclosure 중 국내 종목의 category A·B. 최근 접수일부터 올립니다.
- 멱등: ops.dart_archive 에 접수번호가 있으면 다시 올리지 않습니다 (저장소에 파일만 있고 메타가 없으면 메타만 채움).
- 용량: 올리기 전에 대상 건수·예상 용량을 내고, 누적 크기가 상한(기본 4GB)에 닿으면 멈추고 못 올린 건수를 기록합니다.
- 재사용: fetch_files() 는 보관본이 있으면 DART 를 다시 부르지 않고 원문을 돌려줍니다 (report-exposure 파서 등).
"""
from __future__ import annotations

from . import db
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


def estimate(candidates: list[tuple], avg: dict[str, float] | None = None) -> int:
    avg = {**DEFAULT_AVG, **(avg or {})}
    return int(sum(avg.get(c[3], avg["B"]) for c in candidates))


def run(conn, session, store, since: str | None = None, limit: int | None = None, cap_gb: float | None = None,
        dry_run: bool = False, fetch_raw=None) -> dict:
    """보관 실행. 결과 dict 의 키: targets, estimate_bytes, used_bytes, cap_bytes, uploaded, uploaded_bytes,
    skipped_cap(상한으로 못 올린 건수), failed[(접수번호, 이유)], skipped(저장소 설정 없음 등 사유 또는 None)."""
    if fetch_raw is None:
        from .collectors import dart_doc

        fetch_raw = dart_doc.fetch_document_raw
    cands = db.archive_candidates(conn, list(CATEGORIES), since, limit)
    used = db.archive_used_bytes(conn)
    cap = cap_bytes(cap_gb)
    res = {"targets": len(cands), "estimate_bytes": estimate(cands, db.archive_avg_bytes(conn)), "used_bytes": used,
           "cap_bytes": cap, "uploaded": 0, "uploaded_bytes": 0, "skipped_cap": 0, "failed": [], "skipped": None}
    if dry_run:
        return res
    if store is None:
        res["skipped"] = "Object Storage 접근 정보 없음 (건너뜀)"
        return res
    for i, (rcept_no, company_id, ticker, category, rcept_dt) in enumerate(cands):
        if used + MAX_SINGLE > cap:
            res["skipped_cap"] = len(cands) - i  # 최근순이라 남은 것은 모두 더 오래된 공시
            break
        key = object_key(rcept_no, category)
        try:
            data_size = store.size(key)  # 이미 올라가 있으면 다시 받지 않고 메타만 채움
            if data_size is None:
                data = fetch_raw(session, rcept_no)
                store.put(key, data)
                data_size = len(data)
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
    from .collectors import dart_doc

    raw = load_original(conn, store, rcept_no)
    if raw is not None:
        return dart_doc.parse_zip(raw)
    return dart_doc.fetch_document(session, rcept_no)
