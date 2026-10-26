"""웹 자동 실행기(kic_data/jobs.py): DB 없이 메모리 저장소와 가짜 명령으로 판단·분할·시간 예산을 확인합니다."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from kic_data import db, jobs
from kic_data.collectors import etf, finnhub


class MemState:
    def __init__(self):
        self.rows, self.runs = {}, []

    def all(self):
        return {k: dict(v) for k, v in self.rows.items()}

    def mark_running(self, job, started):
        r = self.rows.setdefault(job, {"cursor": 0})
        r.update(running_since=started, last_started=started)

    def finish(self, job, *, status, cursor, complete, seconds, detail, finished, trigger, tickers, log, started):
        r = self.rows[job]
        r.update(cursor=cursor, last_status=status, last_finished=finished, last_seconds=seconds,
                 running_since=None, detail=detail)
        if complete:
            r["last_complete"] = finished
        self.runs.append({"job": job, "status": status, "tickers": tickers, "log": log})


def env_all(k):
    return "x"


def env_none(k):
    return None


NOW = datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc)
HOURLY = jobs.Job("t", "t", "cmd_x")
ONCE = jobs.Job("o", "o", "cmd_x", every=jobs.ONCE)


def test_due_reason_rules():
    assert jobs.due_reason(HOURLY, None, NOW, []) == "처음"
    done = {"last_complete": NOW - timedelta(minutes=30), "last_status": "ok"}
    assert jobs.due_reason(HOURLY, done, NOW, []) is None
    done["last_complete"] = NOW - timedelta(minutes=56)  # 5분 여유: 매 정각 tick 에서 다시 실행
    assert jobs.due_reason(HOURLY, done, NOW, []) == "주기"
    assert jobs.due_reason(ONCE, {"last_complete": NOW - timedelta(days=30)}, NOW, []) is None
    assert jobs.due_reason(HOURLY, {"cursor": 6, "last_complete": NOW}, NOW, []) == "이어서"
    # 실행 중이면 건너뛰고, 6분 넘게 running 이 남아 있으면 끊긴 것으로 보고 다시
    assert jobs.due_reason(HOURLY, {"running_since": NOW - timedelta(minutes=2)}, NOW, []) is None
    assert jobs.due_reason(HOURLY, {"running_since": NOW - timedelta(minutes=10)}, NOW, []) == "이전 실행이 끊김"
    # 실패 직후는 30분 쉬었다가
    failed = {"last_status": "failed", "last_finished": NOW - timedelta(minutes=10)}
    assert jobs.due_reason(HOURLY, failed, NOW, []) is None
    # 키 없음: 한 번 기록하고 1시간 뒤 다시 확인
    assert jobs.due_reason(HOURLY, None, NOW, ["FSC_API_KEY"]) == "키 확인"
    skipped = {"last_status": "skipped", "last_finished": NOW - timedelta(minutes=20)}
    assert jobs.due_reason(HOURLY, skipped, NOW, ["FSC_API_KEY"]) is None


def test_missing_env():
    j = jobs.Job("t", "t", "cmd_x", needs=("A", "B"), needs_any=("C", "D"))
    assert j.missing_env({"A": "1"}.get) == ["B", "C 또는 D"]
    assert j.missing_env({"A": "1", "B": "1", "D": "1"}.get) == []


def test_run_job_split_chunks_and_filter():
    seen = []

    def cmd_x(ns):
        seen.append(sorted(db.ONLY_TICKERS.get()))
        print("[OK]   하나")
        return 0

    job = jobs.Job("s", "s", "cmd_x", split=("KR",), chunk=2)
    uni = [("KR", "A"), ("KR", "B"), ("KR", "C")]
    st = MemState()
    cmds = SimpleNamespace(cmd_x=cmd_x)
    r1 = jobs.run_job(job, st, commands=cmds, getenv=env_all, universe=uni)
    assert r1["status"] == "partial" and st.rows["s"]["cursor"] == 2 and "last_complete" not in st.rows["s"]
    r2 = jobs.run_job(job, st, commands=cmds, getenv=env_all, universe=uni)
    assert r2["status"] == "ok" and st.rows["s"]["cursor"] == 0 and st.rows["s"]["last_complete"]
    assert seen == [[("KR", "A"), ("KR", "B")], [("KR", "C")]]
    assert db.ONLY_TICKERS.get() is None  # 실행 뒤 필터 해제


def test_run_job_failure_keeps_cursor_and_records_log():
    def cmd_x(ns):
        raise ValueError("boom")

    job = jobs.Job("f", "f", "cmd_x", split=("KR",), chunk=1)
    st = MemState()
    st.rows["f"] = {"cursor": 1}
    r = jobs.run_job(job, st, commands=SimpleNamespace(cmd_x=cmd_x), getenv=env_all,
                     universe=[("KR", "A"), ("KR", "B"), ("KR", "C")])
    assert r["status"] == "failed" and st.rows["f"]["cursor"] == 1
    assert "boom" in st.runs[-1]["log"]


def test_run_job_rc1_is_warn_and_missing_is_skipped():
    st = MemState()
    job = jobs.Job("w", "w", "cmd_x")
    r = jobs.run_job(job, st, commands=SimpleNamespace(cmd_x=lambda ns: 1), getenv=env_all)
    assert r["status"] == "warn" and st.rows["w"].get("last_complete")
    job2 = jobs.Job("k", "k", "cmd_x", needs=("FSC_API_KEY",))
    r2 = jobs.run_job(job2, st, commands=SimpleNamespace(cmd_x=lambda ns: 0), getenv=env_none)
    assert r2["status"] == "skipped" and "FSC_API_KEY" in r2["detail"]


def test_tick_respects_budget(monkeypatch):
    calls = []
    fake = [jobs.Job(f"j{i}", f"j{i}", "cmd_x") for i in range(5)]
    monkeypatch.setattr(jobs, "JOBS", fake)
    clock = {"t": 0.0}

    def cmd_x(ns):
        calls.append(1)
        clock["t"] += 100  # 작업마다 100초 걸린다고 가정

    st = MemState()
    res = jobs.tick(st, budget=150, hard_limit=280, commands=SimpleNamespace(cmd_x=cmd_x), getenv=env_all,
                    clock=lambda: clock["t"])
    # 0초·100초에 시작한 2개만 실행, 200초 시점엔 예산 초과로 남김
    assert len(calls) == 2 and res["waiting"] == ["j2", "j3", "j4"]


def test_job_table_is_consistent():
    from kic_data import cli

    names = [j.name for j in jobs.JOBS]
    assert len(names) == len(set(names))
    for j in jobs.JOBS:
        assert hasattr(cli, j.command), j.command
        j.namespace()  # 날짜 계산 함수가 오류 없이 풀리는지
    # 지표 재계산은 수집 뒤에 와야 함
    assert names.index("build-features") > names.index("news") > names.index("seed-companies")


def test_parse_etf_and_finnhub():
    payload = {"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": [
        {"basDt": "20261023", "srtnCd": "091160", "itmsNm": "KODEX 반도체", "clpr": "45,000", "nav": "45010.5",
         "trqu": "1000", "trPrc": "45000000", "mrktTotAmt": "1", "nPptTotAmt": "2", "bssIdxIdxNm": "KRX 반도체"}]}}}}
    rows = etf.parse_etf(payload)
    assert rows[0]["ticker"] == "091160" and float(rows[0]["nav"]) == 45010.5 and rows[0]["close"] == 45000
    news = finnhub.parse_company_news([
        {"headline": "Nvidia beats", "url": "https://x/1", "datetime": 1761400000, "summary": "s"},
        {"headline": "dup", "url": "https://x/1", "datetime": 1761400000},
        {"headline": "no url", "url": ""}])
    assert len(news) == 1 and news[0]["published_at"].year == 2025
