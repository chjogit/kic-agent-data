"""신용공여 잔고 (#40): 파서·인증 오류 건너뜀·작업 등록. 필드 이름은 11/4 첫 응답 전이라 모의 데이터(이름이 달라도 동작해야 함)."""
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from kic_data import cli, jobs
from kic_data.collectors import kofia_credit as kc


def _payload(items, total=None, code="00", msg="NORMAL SERVICE."):
    return {"response": {"header": {"resultCode": code, "resultMsg": msg},
                         "body": {"numOfRows": 10, "pageNo": 1, "totalCount": total if total is not None else len(items),
                                  "items": {"item": items}}}}


def test_parse_items_keeps_all_numbers_and_picks_loan():
    rows, total = kc.parse_items(_payload([
        {"basDt": "20261102", "crdTrFingScrs": "28,417,900,000,000", "crdTrLndrScrs": "250000000000", "note": "-"},
        {"basDt": "2026-11-03", "x": "1.5"},
        {"basDt": "-", "y": "3"},
    ]))
    assert total == 3 and [r["bas_dt"] for r in rows] == [date(2026, 11, 2), date(2026, 11, 3)]   # 날짜 못 읽는 행은 버림
    a, b = rows
    assert a["loan_total"] == Decimal("28417900000000") and set(a["values"]) == {"crdTrFingScrs", "crdTrLndrScrs"}
    assert b["loan_total"] is None and b["values"] == {"x": "1.5"} and a["raw"]["note"] == "-"


def test_single_item_dict_and_empty():
    rows, _ = kc.parse_items(_payload({"basDt": "20261102", "a": "1"}))
    assert len(rows) == 1
    assert kc.parse_items({"response": {"header": {"resultCode": "00"}, "body": {"items": ""}}}) == ([], 0)


def test_auth_error_is_skippable_and_other_errors_raise():
    with pytest.raises(kc.AuthSkipped):
        kc.parse_items(_payload([], code="30", msg="SERVICE KEY IS NOT REGISTERED ERROR."))
    with pytest.raises(RuntimeError) as exc:
        kc.parse_items(_payload([], code="99", msg="UNKNOWN"))
    assert not isinstance(exc.value, kc.AuthSkipped)
    with pytest.raises(RuntimeError):
        kc.parse_items({"error": "x"})


def test_command_skips_on_auth_error_without_db_write(monkeypatch, capsys):
    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def commit(self):
            pass

    monkeypatch.setattr(cli.db, "connect", lambda: Conn())
    monkeypatch.setattr(cli.db, "credit_balance_last", lambda conn: None)
    monkeypatch.setattr(cli, "make_session", lambda: object())
    saved = []
    monkeypatch.setattr(cli.db, "save_credit_balance", lambda conn, rows: saved.append(rows))

    def boom(*a, **k):
        raise kc.AuthSkipped("[kofia_credit] 인증 오류 30: SERVICE KEY IS NOT REGISTERED")

    monkeypatch.setattr(kc, "fetch_range", boom)
    assert cli.cmd_credit_balance(SimpleNamespace(days=10)) == 0 and not saved
    assert "건너뜀" in capsys.readouterr().out
    monkeypatch.setattr(kc, "fetch_range", lambda *a, **k: [{"bas_dt": date(2026, 11, 2), "loan_total": None, "values": {}, "raw": {}}])
    monkeypatch.setattr(cli.db, "save_credit_balance", lambda conn, rows: len(rows))
    assert cli.cmd_credit_balance(SimpleNamespace(days=10)) == 0
    monkeypatch.setattr(kc, "fetch_range", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("[kofia_credit] 오류 99")))
    assert cli.cmd_credit_balance(SimpleNamespace(days=10)) == 1    # 다른 오류는 실패로 기록


def test_job_registered_daily_with_data_go_key():
    j = {x.name: x for x in jobs.JOBS}["credit-balance"]
    assert j.command == "cmd_credit_balance" and j.every == jobs.DAY and "DATA_GO_KR_KEY" in j.needs_any


def test_sql_adds_table_only():
    sql = (Path(__file__).resolve().parent.parent / "sql" / "031_credit_balance.sql").read_text(encoding="utf-8")
    assert "core.credit_balance" in sql and "DROP " not in sql.upper() and "ALTER TABLE" not in sql.upper()
