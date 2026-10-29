"""금융위원회_채권발행정보 파서 (kic_data/collectors/fsc_bond.py). 필드 이름은 첫 응답 전이라 후보·값 모양으로 고르는지 봅니다."""
from datetime import date
from decimal import Decimal

import pytest

from kic_data import jobs
from kic_data.collectors import dart, fsc_bond as fb


def _payload(items, total=None):
    return {"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
                         "body": {"numOfRows": 10, "pageNo": 1, "totalCount": total if total is not None else len(items),
                                  "items": {"item": items}}}}


def test_parse_items_known_and_guessed_keys():
    items = [{"basDt": "20261029", "crno": "1301110006246", "bondIsurNm": "샘플", "isinCd": "KR6000001234",
              "isinCdNm": "샘플123", "bondIssuDt": "20240310", "bondExprDt": "20290310", "bondIssuAmt": "200000000000",
              "bondIssuCurCd": "KRW", "bondSrfcInrt": "3.25", "intrtChngClsfNm": "고정금리"},
             {"basDt": "20261029", "isinCd": "KR6000005678", "someIssuDt": "2025-01-05", "xExprDt": "99991231",
              "someIssuAmt": "1,000", "unknownField": "변동"}]
    rows, total = fb.parse_items(_payload(items))
    a, b = rows
    assert total == 2 and a["isin"] == "KR6000001234" and a["coupon_rate"] == Decimal("3.25")
    assert a["issue_date"] == date(2024, 3, 10) and a["maturity_date"] == date(2029, 3, 10) and a["rate_kind"] == "fixed"
    assert a["issue_amount"] == Decimal(200000000000) and a["raw"]["crno"] == "1301110006246"
    assert b["issue_date"] == date(2025, 1, 5) and b["maturity_date"] is None   # 9999… = 만기 없음 표기
    assert b["rate_kind"] == "floating" and b["coupon_rate"] is None


def test_parse_items_single_dict_empty_and_error():
    rows, _ = fb.parse_items(_payload({"isinCd": "KR1", "bondIssuDt": "-"}))
    assert rows[0]["isin"] == "KR1" and rows[0]["issue_date"] is None and rows[0]["rate_kind"] is None
    assert fb.parse_items({"response": {"header": {"resultCode": "00"}, "body": {"items": ""}}}) == ([], 0)
    with pytest.raises(RuntimeError):
        fb.parse_items({"response": {"header": {"resultCode": "30", "resultMsg": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR"}}})


def test_rate_kind_values():
    assert [fb.rate_kind(x) for x in ("고정", "고정금리", "변동금리", "FRN 변동", "", None, "기타")] == \
        ["fixed", "fixed", "floating", "floating", None, None, None]


def test_dart_company_jurir_no():
    info = dart.parse_company({"status": "000", "corp_name": "샘플(주)", "induty_code": "264", "jurir_no": "130111-0006246"})
    assert info["jurir_no"] == "1301110006246"
    assert dart.parse_company({"status": "000", "corp_name": "x", "jurir_no": ""})["jurir_no"] is None


def test_bond_issue_job_registered():
    j = {x.name: x for x in jobs.JOBS}["bond-issue"]
    assert j.command == "cmd_bond_issue" and "DATA_GO_KR_KEY" in j.needs_any
