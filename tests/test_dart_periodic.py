"""OpenDART 정기보고서 주요정보(차입 조건)·매출채권 파서 (kic_data/collectors/dart_periodic.py)."""
from datetime import date
from decimal import Decimal

import pytest

from kic_data import cli
from kic_data.collectors import dart_periodic as dp

BOND = {"status": "000", "message": "정상", "list": [
    {"rcept_no": "20260814000123", "corp_cls": "Y", "corp_code": "00126380", "corp_name": "샘플",
     "remndr_exprtn1": "미상환 잔액", "remndr_exprtn2": "공모", "yy1_below": "300,000", "yy1_excess_yy2_below": "200,000",
     "yy2_excess_yy3_below": "-", "yy3_excess_yy4_below": "-", "yy4_excess_yy5_below": "100,000",
     "yy5_excess_yy10_below": "-", "yy10_excess": "-", "sm": "600,000", "stlm_dt": "2026-06-30"},
    {"rcept_no": "20260814000123", "corp_cls": "Y", "corp_code": "00126380", "corp_name": "샘플",
     "remndr_exprtn1": "미상환 잔액", "remndr_exprtn2": "합계", "yy1_below": "300,000", "yy1_excess_yy2_below": "200,000",
     "yy2_excess_yy3_below": "-", "yy3_excess_yy4_below": "-", "yy4_excess_yy5_below": "100,000",
     "yy5_excess_yy10_below": "-", "yy10_excess": "-", "sm": "600,000", "stlm_dt": "2026-06-30"}]}


def test_parse_bond_balance_buckets():
    rows = dp.parse_balance(BOND, "bond")
    total = [r for r in rows if r["label"] == "미상환 잔액|합계"]
    assert {r["bucket"]: r["amount_raw"] for r in total} == {
        "1y": Decimal(300000), "1-2y": Decimal(200000), "4-5y": Decimal(100000), "total": Decimal(600000)}
    assert total[0]["stlm_dt"] == date(2026, 6, 30)
    assert sum(r["amount_raw"] for r in total if r["bucket"] in dp.WITHIN_1Y) / Decimal(600000) == Decimal("0.5")


def test_parse_cp_and_no_data():
    cp = {"status": "000", "list": [{"remndr_exprtn1": "합계", "remndr_exprtn2": "", "de10_below": "10",
                                     "de30_excess_de90_below": "20", "sm": "30", "stlm_dt": "2026-06-30"}]}
    rows = dp.parse_balance(cp, "cp")
    assert {(r["label"], r["bucket"]) for r in rows} == {("합계", "10d"), ("합계", "30-90d"), ("합계", "total")}
    assert dp.parse_balance({"status": "013", "message": "조회된 데이타가 없습니다."}, "cp") == []
    with pytest.raises(RuntimeError):
        dp.parse_balance({"status": "020", "message": "요청 제한 초과"}, "bond")


def test_parse_issues_rate_and_dates():
    payload = {"status": "000", "list": [
        {"isu_cmpny": "샘플", "scrits_knd_nm": "회사채", "isu_mth_nm": "공모", "isu_de": "20260310",
         "facvalu_totamt": "200,000", "intrt": "3.25", "evl_grad_instt": "AA+(한신평)", "mtd": "20290310",
         "repy_at": "미상환", "mngt_cmpny": "-", "stlm_dt": "2026-06-30", "rcept_no": "1"},
        {"isu_cmpny": "샘플", "scrits_knd_nm": "기업어음", "isu_mth_nm": "사모", "isu_de": "2026.01.05", "facvalu_totamt": "-",
         "intrt": "-", "evl_grad_instt": "-", "mtd": "-", "repy_at": "-", "mngt_cmpny": "-", "stlm_dt": "-",
         "rcept_no": "1"},
        {"isu_cmpny": "합 계", "scrits_knd_nm": "-", "facvalu_totamt": "1,200", "intrt": "-", "rcept_no": "1"}]}
    a, b = dp.parse_issues(payload)
    assert a["rate"] == Decimal("3.25") and a["issue_date"] == date(2026, 3, 10) and a["maturity_date"] == date(2029, 3, 10)
    assert a["face_amount_raw"] == Decimal(200000) and a["rating"] == "AA+(한신평)"
    assert b["rate"] is None and b["issue_date"] == date(2026, 1, 5) and b["face_amount_raw"] is None
    assert b["rating"] is None and b["seq"] == 1   # '-' 는 값 없음, 합계 행은 뺌


def test_pick_receivable_prefers_pure_trade_receivables():
    rows = [{"sj_div": "BS", "account_nm": "매출채권 및 기타채권", "amount": Decimal(9)},
            {"sj_div": "IS", "account_nm": "매출채권", "amount": Decimal(1)},
            {"sj_div": "BS", "account_nm": "매출채권", "amount": Decimal(5)}]
    assert dp.pick_receivable(rows)["amount"] == 5
    assert dp.pick_receivable(rows[:1])["amount"] == 9
    assert dp.pick_receivable([{"sj_div": "BS", "account_nm": "현금", "amount": Decimal(1)}]) is None


def test_periodic_targets_latest_due_and_annual():
    assert cli.periodic_targets(date(2026, 10, 27)) == [(2026, "11012"), (2025, "11011")]
    assert cli.periodic_targets(date(2026, 11, 20)) == [(2026, "11014"), (2025, "11011")]
    assert cli.periodic_targets(date(2026, 4, 2)) == [(2025, "11011")]
    assert cli.periodic_targets(date(2026, 3, 1)) == [(2025, "11014"), (2024, "11011")]
