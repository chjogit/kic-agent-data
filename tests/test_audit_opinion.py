"""감사의견 정형화 (#35): 분류·파서·작업 등록. 첫 행 모양은 2026-11-03 실제 응답(SK하이닉스·NAVER)."""
from pathlib import Path

import pytest

from kic_data import jobs
from kic_data.collectors import dart


def _row(**kw):
    base = {"rcept_no": "20260317000635", "corp_code": "00164779", "bsns_year": "제78기\n(당기)", "adtor": "삼정회계법인",
            "adt_opinion": "적정의견", "adt_reprt_spcmnt_matter": "-", "emphs_matter": "해당사항 없음",
            "core_adt_matter": "가. (별도재무제표) 기계장치의 감가상각개시시점에 대한 적정성 검토", "stlm_dt": "2025-12-31"}
    base.update(kw)
    return base


@pytest.mark.parametrize("text, cls", [
    ("적정의견", "unqualified"), ("적 정", "unqualified"), ("부적정의견", "adverse"), ("한정의견", "qualified"),
    ("의견거절", "disclaimer"), ("감사범위 제한으로 인한 한정", "qualified"), ("-", "unknown"), (None, "unknown"), ("", "unknown"),
])
def test_classify_opinion_checks_negative_first(text, cls):
    assert dart.classify_opinion(text) == cls


def test_parse_real_shape_unqualified_no_emphasis():
    rows = dart.parse_audit_opinion({"status": "000", "list": [_row(), _row(core_adt_matter="가. (연결재무제표) 투자 평가")]})
    a, b = rows
    assert (a["seq"], b["seq"]) == (0, 1) and a["fiscal_year"] == 2025 and a["settle_dt"] == "2025-12-31"
    assert a["period_label"] == "제78기(당기)" and a["auditor"] == "삼정회계법인" and a["opinion_class"] == "unqualified"
    assert a["emphasis"] == "" and a["has_emphasis"] is False and a["going_concern"] is False
    assert "연결" in b["core_matter"] and a["raw"]["adtor"] == "삼정회계법인"


def test_missing_emphasis_field_is_unknown_not_false():
    r = _row()
    del r["emphs_matter"]                      # NAVER 응답처럼 강조사항 필드가 아예 없는 경우 (특기사항 '-' 만 있음)
    a = dart.parse_audit_opinion({"status": "000", "list": [r]})[0]
    assert a["emphasis"] is None and a["has_emphasis"] is False       # 특기사항이 '-' 로 확인된 경우
    del r["adt_reprt_spcmnt_matter"]
    b = dart.parse_audit_opinion({"status": "000", "list": [r]})[0]
    assert b["has_emphasis"] is None and b["going_concern"] is None   # 둘 다 없으면 추정하지 않음


def test_going_concern_and_non_standard_opinion():
    rows = dart.parse_audit_opinion({"status": "000", "list": [
        _row(adt_opinion="한정의견", emphs_matter="계속기업 불확실성: 영업손실 및 유동부채 초과", adt_reprt_spcmnt_matter="-"),
        _row(adt_opinion="의견거절", emphs_matter="해당사항 없음", adt_reprt_spcmnt_matter="감사범위 제한", stlm_dt="2024-12-31"),
        _row(adt_opinion="이상한 값", stlm_dt="2023-12-31"),
        _row(rcept_no="", stlm_dt="2025-12-31"),            # 접수번호 없음 → 버림
        _row(stlm_dt="-"),                                  # 결산일 못 읽음 → 버림
    ]})
    assert [r["opinion_class"] for r in rows] == ["qualified", "disclaimer", "unknown"]
    assert rows[0]["has_emphasis"] and rows[0]["going_concern"] is True
    assert rows[1]["has_emphasis"] and rows[1]["going_concern"] is False and rows[1]["fiscal_year"] == 2024


def test_no_data_and_error_status():
    assert dart.parse_audit_opinion({"status": "013"}) == []
    with pytest.raises(RuntimeError, match="020"):
        dart.parse_audit_opinion({"status": "020"})


def test_job_registered_and_sql_additive():
    j = {x.name: x for x in jobs.JOBS}["audit-opinion"]
    assert j.command == "cmd_audit_opinion" and "DART_API_KEY" in j.needs and j.every == jobs.WEEK
    sql = (Path(__file__).resolve().parent.parent / "sql" / "032_audit_opinion.sql").read_text(encoding="utf-8")
    assert "core.audit_opinion" in sql and "DROP " not in sql.upper() and "ALTER TABLE" not in sql.upper()
