"""DART 전체 재무제표 Parquet (#77) 오프라인 테스트: 네트워크·DB·실제 저장소 없음."""
import io
import json

import pytest

from kic_data import fs_parquet as fp

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")


def payload(items, status="000"):
    return {"status": status, "message": "", "list": items}


ITEM = {"rcept_no": "20250311001085", "reprt_code": "11011", "bsns_year": "2024", "corp_code": "00126380",
        "sj_div": "BS", "sj_nm": "재무상태표", "account_id": "ifrs-full_Assets", "account_nm": "자산총계",
        "account_detail": "-", "thstrm_nm": "제 56 기", "thstrm_amount": "514,531,948,000,000",
        "frmtrm_nm": "제 55 기", "frmtrm_amount": "455,905,980,000,000", "bfefrmtrm_nm": "제 54 기",
        "bfefrmtrm_amount": "448,424,507,000,000", "ord": "21", "currency": "KRW"}


def test_parse_amount_integer_and_null():
    assert fp.parse_amount("1,234") == 1234
    assert fp.parse_amount("-5000") == -5000
    assert fp.parse_amount("514,531,948,000,000") == 514531948000000
    assert fp.parse_amount("12.6") == 13
    for empty in (None, "", " ", "-", ".", "abc", "nan"):
        assert fp.parse_amount(empty) is None   # 0 으로 채우지 않음


def test_parse_rows_fixed_columns_and_nulls():
    rows = fp.parse_rows(payload([ITEM, {**ITEM, "account_nm": "매출", "thstrm_amount": "", "account_id": ""}]),
                         "00126380", "005930", 2024, "11011", "CFS")
    assert [list(r) for r in rows] == [fp.COLUMN_NAMES] * 2
    r = rows[0]
    assert r["thstrm_amount"] == 514531948000000 and r["frmtrm_amount"] == 455905980000000
    assert r["thstrm_add_amount"] is None and r["frmtrm_q_amount"] is None   # 응답에 없는 열은 null
    assert r["fs_div"] == "CFS" and r["bsns_year"] == 2024 and r["stock_code"] == "005930" and r["ord"] == 21
    assert rows[1]["thstrm_amount"] is None and rows[1]["account_id"] is None   # 빈 값은 null


def test_parse_rows_013_and_error():
    assert fp.parse_rows({"status": "013", "message": "x"}, "1", None, 2024, "11011", "OFS") == []
    with pytest.raises(RuntimeError):
        fp.parse_rows({"status": "020", "message": "x"}, "1", None, 2024, "11011", "OFS")


def test_parquet_column_types_and_null_roundtrip():
    rows = fp.parse_rows(payload([ITEM, {**ITEM, "thstrm_amount": "-", "account_detail": None}]),
                         "00126380", None, 2024, "11011", "CFS")
    table = pq.read_table(io.BytesIO(fp.to_parquet_bytes(rows)))
    assert table.column_names == fp.COLUMN_NAMES
    types = {f.name: str(f.type) for f in table.schema}
    assert types["bsns_year"] == "int32" and types["ord"] == "int32"
    for f in fp.AMOUNT_FIELDS:
        assert types[f] == "int64"
    assert types["corp_code"] == "string"
    col = table.column("thstrm_amount").to_pylist()
    assert col == [514531948000000, None]          # null 유지, 0 아님
    assert table.column("stock_code").to_pylist() == [None, None]


def test_empty_part_still_has_schema():
    table = pq.read_table(io.BytesIO(fp.to_parquet_bytes([])))
    assert table.num_rows == 0 and table.column_names == fp.COLUMN_NAMES


def fake_fetcher(calls):
    def f(session, corp, year, code, div):
        calls.append((corp, year, code, div))
        if corp == "B":
            return {"status": "013", "message": "no data"}
        if corp == "C":
            raise RuntimeError("[dart] 요청 실패: ReadTimeout")
        return payload([{**ITEM, "corp_code": corp}])
    return f


def test_collect_partitions_and_missing():
    calls = []
    comps = [("A", "000001", "가"), ("B", "000002", "나"), ("C", None, "다")]
    parts, missing, n = fp.collect(None, comps, [2024, 2023], reports=("11011", "11012"), fetcher=fake_fetcher(calls))
    assert n == 3 * 2 * 2 * 2 == len(calls)
    assert set(parts) == {(y, r) for y in (2024, 2023) for r in ("11011", "11012")}
    assert len(parts[(2024, "11011")]) == 2                     # A 의 CFS+OFS 만
    assert {m["reason"] == "013" for m in missing if m["corp_code"] == "B"} == {True}
    assert all(m["reason"].startswith("error") for m in missing if m["corp_code"] == "C")
    assert len(missing) == 2 * 2 * 2 * 2                         # B, C 모두 (연도2 x 보고서2 x 구분2)
    meta = fp.build_meta(parts, missing, comps, [2024, 2023], n)
    assert meta["row_count"] == 8 and meta["missing_count"] == len(missing) and meta["company_count"] == 3
    assert "financials/fs/year=2024/report=11011/part-0000.parquet" in meta["files"]
    assert [c["name"] for c in meta["columns"]] == fp.COLUMN_NAMES
    json.dumps(meta, ensure_ascii=False)


def test_build_files_keys_all_under_prefix():
    parts = {(2024, "11011"): fp.parse_rows(payload([ITEM]), "1", None, 2024, "11011", "CFS"), (2024, "11012"): []}
    files = fp.build_files(parts, {"a": 1})
    assert set(files) == {"financials/fs/year=2024/report=11011/part-0000.parquet",
                          "financials/fs/year=2024/report=11012/part-0000.parquet", "financials/_meta.json"}
    assert json.loads(files["financials/_meta.json"]) == {"a": 1}


class FakeStore:
    def __init__(self, existing=None):
        self.objects = dict(existing or {})
        self.puts = []

    def put(self, key, data, content_type=""):
        self.objects[key] = data
        self.puts.append(key)


@pytest.mark.parametrize("key", ["dart/x.zip", "sentiment/latest/a.json", "financials2/a", "/financials/a",
                                 "financials/../dart/a", "x/financials/a", "financials//a"])
def test_prefix_guard_rejects(key):
    with pytest.raises(ValueError):
        fp.assert_prefix(key)


def test_upload_only_financials_and_other_prefixes_untouched():
    other = {"dart/2024/a.zip": b"keep", "sentiment/latest/s.json": b"keep2"}
    store = FakeStore(other)
    files = fp.build_files({(2024, "11011"): fp.parse_rows(payload([ITEM]), "1", None, 2024, "11011", "CFS")}, {"m": 1})
    assert fp.upload(store, files) == 2
    assert all(k.startswith("financials/") for k in store.puts)
    assert {k: store.objects[k] for k in other} == other


def test_upload_rejects_whole_batch_if_any_key_outside():
    store = FakeStore({"dart/a": b"keep"})
    with pytest.raises(ValueError):
        fp.upload(store, {"financials/_meta.json": b"{}", "dart/a": b"overwrite"})
    assert store.puts == [] and store.objects == {"dart/a": b"keep"}   # 검사 먼저, 아무것도 올리지 않음
