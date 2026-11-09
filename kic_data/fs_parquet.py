"""DART 전체 재무제표(fnlttSinglAcntAll) 숫자를 Parquet 으로 만들어 Object Storage `financials/` 에 둔다 (#77).

파일: financials/fs/year=YYYY/report=RRRRR/part-0000.parquet (연도·보고서별, 모든 회사·연결+별도) + financials/_meta.json
열은 COLUMNS 로 고정. 금액은 정수 원(int64), 값이 없으면 null (0 으로 채우지 않음). 주석·서술 문장은 넣지 않는다.
업로드는 `financials/` 접두사만 허용한다(assert_prefix). 다른 접두사(dart/·sentiment/)는 건드리지 않는다.
pyarrow 는 함수 안에서만 불러온다.
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from .collectors.dart import BASE, STATUS
from .config import env
from .http_util import RateLimiter, get_json

PREFIX = "financials/"
REPORT_CODES = ("11011", "11012", "11013", "11014")   # 사업·반기·1분기·3분기
FS_DIVS = ("CFS", "OFS")
MIN_INTERVAL = 1.1                                    # 초당 1회 이하
_limiter = RateLimiter(MIN_INTERVAL)

# (열 이름, 종류, 설명). 종류: str | int32 | int64(금액)
COLUMNS = [
    ("corp_code", "str", "DART 고유번호 8자리"),
    ("stock_code", "str", "종목코드 (없으면 null)"),
    ("bsns_year", "int32", "사업연도"),
    ("reprt_code", "str", "보고서 코드 11011 사업·11012 반기·11013 1분기·11014 3분기"),
    ("rcept_no", "str", "접수번호"),
    ("fs_div", "str", "CFS 연결 / OFS 별도"),
    ("sj_div", "str", "재무제표 구분 BS·IS·CIS·CF·SCE"),
    ("sj_nm", "str", "재무제표 이름"),
    ("account_id", "str", "계정 ID (표준계정코드 미사용 계정은 DART 값 그대로)"),
    ("account_nm", "str", "계정 이름"),
    ("account_detail", "str", "계정 상세(자본변동표 구성요소 등, 없으면 null)"),
    ("ord", "int32", "DART 표시 순서"),
    ("thstrm_amount", "int64", "당기 금액(원)"),
    ("thstrm_add_amount", "int64", "당기 누적 금액(원), 반기·3분기 손익·현금흐름"),
    ("frmtrm_amount", "int64", "전기 금액(원)"),
    ("frmtrm_q_amount", "int64", "전기 분기 금액(원)"),
    ("frmtrm_add_amount", "int64", "전기 누적 금액(원)"),
    ("bfefrmtrm_amount", "int64", "전전기 금액(원)"),
    ("currency", "str", "통화 (KRW)"),
]
COLUMN_NAMES = [c[0] for c in COLUMNS]
AMOUNT_FIELDS = ("thstrm_amount", "thstrm_add_amount", "frmtrm_amount", "frmtrm_q_amount", "frmtrm_add_amount", "bfefrmtrm_amount")
_TEXT_FIELDS = ("rcept_no", "sj_div", "sj_nm", "account_id", "account_nm", "account_detail", "currency")


def assert_prefix(key: str) -> str:
    """업로드 키가 financials/ 아래의 정상 경로인지 확인 (다른 접두사·상위 경로 이동 거부)."""
    if not key.startswith(PREFIX) or ".." in key.split("/") or "//" in key:
        raise ValueError(f"financials/ 밖의 경로는 쓰지 않습니다: {key.split('/')[0]}/...")
    return key


def parse_amount(value) -> int | None:
    """'1,234'·'-1234' → int, 빈칸·'-'·숫자 아님 → None (0 으로 채우지 않음). 소수는 가까운 정수로 반올림."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in ("", "-", "."):
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return int(number.to_integral_value())


def _text(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def parse_rows(payload: dict, corp_code: str, stock_code: str | None, year: int, report_code: str, fs_div: str) -> list[dict]:
    """응답 → 고정 열의 행. 013(자료 없음)은 빈 목록, 그 밖의 비정상 상태는 RuntimeError."""
    status = payload.get("status")
    if status == "013":
        return []
    if status != "000":
        raise RuntimeError(f"[dart] {status} {STATUS.get(status, payload.get('message', ''))}")
    rows = []
    for it in payload.get("list") or []:
        row = {"corp_code": corp_code, "stock_code": stock_code or None, "bsns_year": year,
               "reprt_code": report_code, "fs_div": fs_div}
        for f in _TEXT_FIELDS:
            row[f] = _text(it.get(f))
        row["ord"] = parse_amount(it.get("ord"))
        for f in AMOUNT_FIELDS:
            row[f] = parse_amount(it.get(f))
        rows.append({c: row.get(c) for c in COLUMN_NAMES})
    return rows


def fetch(session, corp_code: str, year: int, report_code: str, fs_div: str) -> dict:
    params = {"crtfc_key": env("DART_API_KEY", required=True), "corp_code": corp_code,
              "bsns_year": str(year), "reprt_code": report_code, "fs_div": fs_div}
    return get_json(session, "dart", f"{BASE}/fnlttSinglAcntAll.json", params=params, limiter=_limiter)


def schema():
    import pyarrow as pa

    kinds = {"str": pa.string(), "int32": pa.int32(), "int64": pa.int64()}
    return pa.schema([pa.field(n, kinds[k]) for n, k, _ in COLUMNS])


def to_parquet_bytes(rows: list[dict]) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    sch = schema()
    table = pa.Table.from_pylist(rows, schema=sch) if rows else sch.empty_table()
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd")
    return sink.getvalue().to_pybytes()


def part_key(year: int, report_code: str) -> str:
    return assert_prefix(f"{PREFIX}fs/year={year}/report={report_code}/part-0000.parquet")


def collect(session, companies, years, reports=REPORT_CODES, fs_divs=FS_DIVS, fetcher=None, progress=None, done=None, on_part=None):
    """companies: [(corp_code, stock_code, name)]. 반환: (rows_by_part, missing, calls).
    rows_by_part: {(year, report): [행]}, missing: 013 은 reason='013', 실패는 오류 종류."""
    fetcher = fetcher or fetch
    parts: dict[tuple[int, str], list[dict]] = {}
    missing: list[dict] = []
    calls = 0
    for year in years:
        for code in reports:
            if done and (year, code) in done:   # 이어하기: 이미 끝낸 (연도, 보고서) 는 건너뜀
                rows_d, miss_d, calls_d = done[(year, code)]
                parts[(year, code)] = rows_d
                missing.extend(miss_d)
                calls += calls_d
                continue
            parts.setdefault((year, code), [])
            m0, c0 = len(missing), calls
            for corp_code, stock_code, _name in companies:
                for fs_div in fs_divs:
                    calls += 1
                    base = {"corp_code": corp_code, "year": year, "report": code, "fs_div": fs_div}
                    try:
                        payload = fetcher(session, corp_code, year, code, fs_div)
                        rows = parse_rows(payload, corp_code, stock_code, year, code, fs_div)
                    except Exception as exc:  # 한 건 실패가 전체를 멈추지 않음 (메시지에 URL·키는 들어가지 않음)
                        missing.append({**base, "reason": f"error: {type(exc).__name__}: {str(exc)[:120]}"})
                        continue
                    if not rows:
                        missing.append({**base, "reason": "013"})
                    parts[(year, code)].extend(rows)
            if on_part:
                on_part(year, code, parts[(year, code)], missing[m0:], calls - c0)
            if progress:
                progress(year, code, len(parts[(year, code)]), calls)
    return parts, missing, calls


def build_meta(parts: dict, missing: list[dict], companies, years, calls: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "source": "OpenDART fnlttSinglAcntAll",
        "years": sorted(years),
        "report_codes": sorted({r for _, r in parts}),
        "fs_divs": list(FS_DIVS),
        "company_count": len(companies),
        "api_calls": calls,
        "row_count": sum(len(v) for v in parts.values()),
        "files": {part_key(y, r): len(v) for (y, r), v in sorted(parts.items())},
        "missing_count": len(missing),
        "missing": missing,
        "columns": [{"name": n, "type": k, "description": d} for n, k, d in COLUMNS],
        "notes": "금액은 정수 원, 값 없음은 null. 013(자료 없음)·실패는 missing.",
    }


def build_files(parts: dict, meta: dict) -> dict[str, bytes]:
    files = {part_key(y, r): to_parquet_bytes(rows) for (y, r), rows in sorted(parts.items())}
    if len(meta.get("missing", [])) > 300:   # 누락 목록이 크면 CSV 로 분리
        import csv

        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=["corp_code", "year", "report", "fs_div", "reason"], lineterminator="\n")
        w.writeheader()
        w.writerows(meta["missing"])
        files[assert_prefix(f"{PREFIX}_missing.csv")] = buf.getvalue().encode("utf-8")
        meta = {**meta, "missing": [], "missing_file": "financials/_missing.csv"}
    files[assert_prefix(f"{PREFIX}_meta.json")] = json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8")
    return files


def upload(store, files: dict[str, bytes]) -> int:
    """모든 키가 financials/ 아래인지 먼저 검사한 뒤에만 올린다. 반환: 올린 파일 수."""
    for key in files:
        assert_prefix(key)
    for key, data in files.items():
        ctype = "text/csv" if key.endswith(".csv") else "application/json" if key.endswith(".json") else "application/vnd.apache.parquet"
        store.put(key, data, content_type=ctype)
    return len(files)


def read_rows(path) -> list[dict]:
    import pyarrow.parquet as pq

    return pq.read_table(path).to_pylist()
