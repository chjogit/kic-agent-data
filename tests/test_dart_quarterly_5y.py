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
