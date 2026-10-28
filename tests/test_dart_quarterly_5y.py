"""분기 재무 5년 과거분 작업 (에이전트 1 P01 기준선·순차평가용)."""
from datetime import date

from kic_data import jobs


def test_quarterly_5y_job_exists_in_dart_lane():
    j = next(x for x in jobs.JOBS if x.name == "backfill-dart-quarterly-5y")
    ns = j.namespace()
    assert j.every is jobs.ONCE and ns.from_year <= date.today().year - 5
    assert ns.max_seconds                       # 끊어서 이어 받기
    assert jobs.lane_of(j) == "dart"


def test_every_dart_multi_job_runs_in_dart_lane():
    for j in jobs.JOBS:
        if j.command == "cmd_dart_multi":
            assert jobs.lane_of(j) == "dart", j.name


def test_past_year_chunks_with_no_data_companies_are_not_refetched(monkeypatch):
    """상장 전이라 자료가 없는 회사가 10% 넘는 묶음을 실행마다 다시 받던 문제 (10-28 과거분 실행)."""
    from contextlib import contextmanager
    from types import SimpleNamespace

    from kic_data import cli
    from kic_data.collectors import dart

    companies = [(i, f"{i:06d}", f"c{i}", f"C{i:07d}", None) for i in range(1, 101)]   # 한 묶음

    @contextmanager
    def fake_connect():
        yield None

    marked, calls = {}, []
    monkeypatch.setattr(cli.db, "connect", fake_connect)
    monkeypatch.setattr(cli.db, "list_companies", lambda conn, m: companies)
    monkeypatch.setattr(cli.db, "fact_companies", lambda conn, y, code, src: set(range(1, 71)))   # 70%만 자료 있음
    monkeypatch.setattr(cli.db, "upsert_facts", lambda conn, cid, rows, src: len(rows))
    monkeypatch.setattr(cli.db, "mark_multi_fetched", lambda conn, y, code, st: marked.setdefault((y, code), st))
    monkeypatch.setattr(cli.db, "multi_fetched", lambda conn, y, code: set(marked.get((y, code), {})))
    monkeypatch.setattr(cli, "make_session", lambda: None)
    monkeypatch.setattr(dart, "fetch_multi", lambda s, chunk, y, code: calls.append((y, code)) or {})
    old = date.today().year - 3
    args = SimpleNamespace(from_year=old, reports="11013", max_seconds=None)
    cli.cmd_dart_multi(args)
    first = [c for c in calls if c[0] == old]
    assert len(first) == 1 and marked[(old, "11013")][100] == 0          # 자료 없음도 기록
    calls.clear()
    cli.cmd_dart_multi(args)
    assert not [c for c in calls if c[0] == old]                          # 지난 연도는 다시 안 받음
    assert [c for c in calls if c[0] >= date.today().year - 1]            # 최근 연도는 늦은 제출 때문에 다시 받음
