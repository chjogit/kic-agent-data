"""지분공시 정형화 (#34): 대량보유상황보고·최대주주 현황 파서, 작업·SQL 등록. 필드 이름은 DART OpenAPI 개발가이드 기준(실제 응답은 probe-ownership 으로 확인)."""
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from kic_data import jobs
from kic_data.collectors import dart

MAJOR = {"status": "000", "list": [
    {"rcept_no": "20261020000111", "rcept_dt": "20261020", "corp_code": "00126380", "report_tp": "일반", "repror": "국민연금공단",
     "stkqy": "300,000,000", "stkqy_irds": "-1,000", "stkrt": "5.02", "stkrt_irds": "-0.12", "ctr_stkqy": "-", "ctr_stkrt": "-",
     "report_resn": "장내매도"},
    {"rcept_no": "20261021000222", "rcept_dt": "2026-10-21", "repror": "", "stkrt": "-"},
    {"rcept_dt": "20261022", "repror": "접수번호 없음"},
]}
HOLDER = {"status": "000", "list": [
    {"rcept_no": "20260310000333", "nm": "이재용", "relate": "본인", "stock_knd": "보통주", "bsis_posesn_stock_co": "97,414,196",
     "bsis_posesn_stock_qota_rt": "1.63", "trmend_posesn_stock_co": "97,414,196", "trmend_posesn_stock_qota_rt": "1.63",
     "stlm_dt": "2025-12-31"},
    {"nm": "계", "relate": "-", "stock_knd": "보통주", "trmend_posesn_stock_qota_rt": "20.76"},
    {"nm": " ", "stock_knd": "보통주"},
]}


def test_parse_major_stock_values_and_blanks():
    rows = dart.parse_major_stock(MAJOR)
    assert [r["rcept_no"] for r in rows] == ["20261020000111", "20261021000222"]      # 접수번호 없는 행은 버림
    a, b = rows
    assert a["rcept_dt"] == "2026-10-20" and a["repror"] == "국민연금공단" and a["report_resn"] == "장내매도"
    assert a["stkrt"] == Decimal("5.02") and a["stkrt_irds"] == Decimal("-0.12") and a["stkqy"] == Decimal("300000000")
    assert a["ctr_stkrt"] is None and a["raw"]["report_tp"] == "일반"
    assert b["stkrt"] is None and b["rcept_dt"] == "2026-10-21" and b["repror"] == "" and b["report_resn"] is None


def test_parse_largest_holder():
    rows = dart.parse_largest_holder(HOLDER, 2025, "11011")
    assert [r["holder"] for r in rows] == ["이재용", "계"]                           # 이름 없는 행은 버림
    a = rows[0]
    assert a["fiscal_year"] == 2025 and a["report_code"] == "11011" and a["stake_rt"] == Decimal("1.63")
    assert a["end_qty"] == Decimal("97414196") and a["settle_dt"] == "2025-12-31" and a["rcept_no"] == "20260310000333"
    assert rows[1]["stake_rt"] == Decimal("20.76") and rows[1]["end_qty"] is None


def test_no_data_and_error_status():
    assert dart.parse_major_stock({"status": "013", "message": "조회된 데이터가 없습니다."}) == []
    assert dart.parse_largest_holder({"status": "013"}, 2025, "11011") == []
    with pytest.raises(RuntimeError, match="020"):
        dart.parse_major_stock({"status": "020", "message": "limit"})


def test_event_type_for_ownership_reports_and_existing_unchanged():
    assert dart.classify_event("주식등의대량보유상황보고서(일반)") == "major_holding_report"
    assert dart.classify_event("임원ㆍ주요주주특정증권등소유상황보고서") is None      # 기존 동작 불변
    assert dart.classify_event("자기주식취득결정") == "buyback"
    assert dart.DISCLOSURE_TYPES == ("A", "B", "I")


def test_jobs_registered_without_touching_existing():
    by = {j.name: j for j in jobs.JOBS}
    assert by["disclosures"].kwargs["types"] == "A,B,I" and by["backfill-disclosures"].kwargs["types"] == "A,B,I"
    d = by["disclosures-ownership"]
    assert d.kwargs["types"] == "D" and d.kwargs["bulk"] and "SEC_USER_AGENT" not in d.needs
    assert by["backfill-disclosures-ownership"].kwargs["types"] == "D"
    o = by["ownership"]
    assert o.command == "cmd_ownership" and "DART_API_KEY" in o.needs


def test_sql_adds_tables_only():
    sql = (Path(__file__).resolve().parent.parent / "sql" / "030_ownership.sql").read_text(encoding="utf-8")
    assert "core.ownership_report" in sql and "core.largest_holder" in sql
    assert "DROP " not in sql.upper() and "ALTER TABLE" not in sql.upper()
