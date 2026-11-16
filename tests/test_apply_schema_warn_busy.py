"""#86: 내보내기 워크플로는 스키마 잠금 대기 실패(busy)만 경고로 넘기고, 그 밖의 오류는 그대로 실패한다."""
from types import SimpleNamespace

import pytest

from kic_data import cli, db


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _setup(monkeypatch, busy_names):
    monkeypatch.setattr(db, "connect", lambda: _Conn())
    monkeypatch.setattr(db, "connect_opinion", lambda: None)

    def fake_apply(conn, lock_timeout=None, busy=None):
        busy.extend(busy_names)
        return ["001_a.sql"]

    monkeypatch.setattr(db, "apply_schema", fake_apply)


def test_busy_fails_by_default(monkeypatch, capsys):
    _setup(monkeypatch, ["004_x.sql"])
    assert cli.cmd_apply_schema(SimpleNamespace(warn_busy=False)) == 1
    assert "[FAIL] 004_x.sql" in capsys.readouterr().out


def test_busy_with_warn_busy_is_warning_only(monkeypatch, capsys):
    _setup(monkeypatch, ["004_x.sql", "005_y.sql"])
    assert cli.cmd_apply_schema(SimpleNamespace(warn_busy=True)) == 0
    out = capsys.readouterr().out
    assert "[WARN] 004_x.sql" in out and "[WARN] 005_y.sql" in out and "[FAIL]" not in out


def test_other_errors_still_fail_with_warn_busy(monkeypatch):
    monkeypatch.setattr(db, "connect", lambda: _Conn())
    monkeypatch.setattr(db, "connect_opinion", lambda: None)

    def boom(conn, **kw):
        raise RuntimeError("SQL 오류")

    monkeypatch.setattr(db, "apply_schema", boom)
    with pytest.raises(RuntimeError):
        cli.cmd_apply_schema(SimpleNamespace(warn_busy=True))


def test_cli_flag_parsed_and_default_off(monkeypatch):
    got = []
    monkeypatch.setattr(cli, "cmd_apply_schema", lambda args: got.append(args.warn_busy) or 0)
    assert cli.main(["apply-schema", "--warn-busy"]) == 0
    assert cli.main(["apply-schema"]) == 0
    assert got == [True, False]
