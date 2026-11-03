"""감가상각·이자비용 항목 매핑 (#36). 계정 ID·이름은 2026-11-03 실응답(2025 사업보고서)에서 본 것."""
from decimal import Decimal
from pathlib import Path

import pytest

from kic_data import jobs
from kic_data.collectors import dart


@pytest.mark.parametrize("sj, aid, nm, item", [
    ("CF", "ifrs-full_AdjustmentsForDepreciationExpense", "감가상각비", "depreciation"),
    ("CF", "dart_AdjustmentsForDepreciationExpense", "감가상각비에 대한 조정", "depreciation"),
    ("CF", "ifrs-full_AdjustmentsForDepreciationExpense", "유형자산감가상각비", "depreciation"),
    ("CF", "-표준계정코드 미사용-", "감가상각비", "depreciation"),                 # ID 없이 이름만
    ("CF", "ifrs-full_AdjustmentsForAmortisationExpense", "무형자산상각비", "amortization"),
    ("CF", "dart_AdjustmentsForDepreciationRightofuseAssets", "사용권자산상각비", "rou_depreciation"),
    ("CF", "-표준계정코드 미사용-", "사용권자산 감가상각비", "rou_depreciation"),
    ("CF", "ifrs-full_DepreciationAndAmortisationExpense", "감가상각비 및 무형자산상각비", "dep_amort"),
    ("CF", "dart_AdjustmentsForInterestExpenses", "이자비용", "interest_expense"),
    ("CIS", "ifrs-full_InterestExpense", "이자비용", "interest_expense"),
    ("IS", "-표준계정코드 미사용-", "이자비용", "interest_expense"),
    ("CIS", "ifrs-full_FinanceCosts", "금융원가", "finance_costs"),
    ("CF", "ifrs-full_InterestPaidClassifiedAsOperatingActivities", "이자의 지급", "interest_paid"),
])
def test_known_accounts_map(sj, aid, nm, item):
    assert dart.fin_item(sj, aid, nm) == item


@pytest.mark.parametrize("sj, aid, nm", [
    ("CF", "-표준계정코드 미사용-", "상각후원가측정금융자산의 취득"),            # 이름만 비슷한 투자활동 항목
    ("CF", "dart_AdjustmentsForBadDebtExpenses", "대손상각비"),
    ("CF", "-표준계정코드 미사용-", "사용권자산의 취득"),
    ("BS", "ifrs-full_NoncurrentFinancialAssetsAtAmortisedCost", "상각후원가측정금융자산"),   # 재무상태표는 대상 아님
    ("CIS", "ifrs-full_ComprehensiveIncomeAttributableToNoncontrollingInterests", "비지배지분"),
    ("CF", "ifrs-full_InterestReceivedClassifiedAsOperatingActivities", "이자수취"),
    ("CF", "ifrs-full_InterestExpenseOnLeaseLiabilities", "리스부채이자비용"),     # 하위 구성항목은 합계로 오인하지 않음
])
def test_lookalikes_do_not_map(sj, aid, nm):
    assert dart.fin_item(sj, aid, nm) is None


def test_extract_keeps_only_items_with_amounts():
    rows = dart.parse_financials({"status": "000", "list": [
        {"sj_div": "CF", "account_id": "ifrs-full_AdjustmentsForDepreciationExpense", "account_nm": "감가상각비",
         "thstrm_amount": "1,234,000", "rcept_no": "20260310002820"},
        {"sj_div": "CF", "account_id": "ifrs-full_AdjustmentsForAmortisationExpense", "account_nm": "무형자산상각비", "thstrm_amount": "-"},
        {"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액", "thstrm_amount": "10"},
    ]}, 2025, "11011", "CFS")
    out = dart.extract_fin_items(rows)
    assert len(out) == 1 and out[0]["item"] == "depreciation" and out[0]["amount"] == Decimal("1234000")
    assert out[0]["fs_div"] == "CFS" and out[0]["fiscal_year"] == 2025 and out[0]["rcept_no"] == "20260310002820"


def test_job_registered_and_sql_additive():
    j = {x.name: x for x in jobs.JOBS}["financial-items"]
    assert j.command == "cmd_financial_items" and "DART_API_KEY" in j.needs and j.every == jobs.WEEK
    sql = (Path(__file__).resolve().parent.parent / "sql" / "033_financial_items.sql").read_text(encoding="utf-8")
    assert "core.financial_item" in sql and "v_ebitda_coverage" in sql
    assert "DROP " not in sql.upper() and "ALTER TABLE" not in sql.upper() and "UPDATE core.financial_fact" not in sql
