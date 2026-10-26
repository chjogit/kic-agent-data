"""웹 자동 실행기(kic_data/jobs.py): DB 없이 메모리 저장소와 가짜 명령으로 판단·분할·시간 예산을 확인합니다."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

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


class _FakeConn:
    def __init__(self, fail=False):
        self.fail, self.closed, self.commits = fail, False, 0

    def cursor(self):
        conn = self

        class Cur:
            description = [("job",)]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a):
                if conn.fail:
                    import psycopg
                    raise psycopg.OperationalError("SSL connection has been closed unexpectedly")

            def fetchall(self):
                return []

        return Cur()

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


def test_dbstate_reconnects_after_dropped_connection():
    pytest.importorskip("psycopg")
    dead = _FakeConn(fail=True)
    fresh = _FakeConn()
    state = jobs.DbState(dead, connect=lambda: fresh)
    assert state.all() == {}
    assert dead.closed and state.conn is fresh and fresh.commits == 1


def test_dbstate_without_connect_raises():
    pytest.importorskip("psycopg")
    import psycopg
    state = jobs.DbState(_FakeConn(fail=True))
    with pytest.raises(psycopg.OperationalError):
        state.all()


def test_once_job_retries_after_warn():
    job = jobs.JOB_BY_NAME["backfill-etf"]
    now = datetime(2026, 10, 26, 3, tzinfo=timezone.utc)
    st = {"last_status": "warn", "last_finished": now - timedelta(hours=1), "last_complete": now - timedelta(hours=1)}
    assert jobs.due_reason(job, st, now, []) is None
    st["last_finished"] = now - timedelta(hours=7)
    assert jobs.due_reason(job, st, now, []) == "일부 실패 재시도"
    st["last_status"] = "ok"
    assert jobs.due_reason(job, st, now, []) is None


# ---------------------------------------------------------------- KOSPI 200 확대

def test_parse_kospi200_entry_page():
    from kic_data.collectors import kospi200
    html = ('<td class="ctg"><a href="/item/main.naver?code=005930" target="_parent">삼성전자</a></td>'
            '<td><a href="/item/main.naver?code=000660" target="_parent">SK하이닉스</a></td>'
            '<a href="/item/main.naver?code=005930">삼성전자</a>')
    assert kospi200.parse_entry_page(html) == [("005930", "삼성전자"), ("000660", "SK하이닉스")]


def test_ksic_to_krx_sector():
    from kic_data import krx_sector
    assert len(krx_sector.KRX_SECTORS) == 26
    assert set(krx_sector._PREFIX.values()) <= set(krx_sector.KRX_SECTORS)
    assert krx_sector.ksic_to_krx("264") == "전기·전자"
    assert krx_sector.ksic_to_krx("64992") == "기타금융"  # 지주회사
    assert krx_sector.ksic_to_krx("64121") == "은행"
    assert krx_sector.ksic_to_krx("66121") == "증권"
    assert krx_sector.ksic_to_krx("63120") == "IT 서비스"
    assert krx_sector.ksic_to_krx("") is None


def test_fsc_parse_keeps_ticker_for_bulk():
    from kic_data.collectors import fsc
    payload = {"response": {"header": {"resultCode": "00"}, "body": {"totalCount": 2, "items": {"item": [
        {"basDt": "20261023", "srtnCd": "005930", "clpr": "70000", "lstgStCnt": "100"},
        {"basDt": "20261023", "srtnCd": "A000660", "clpr": "200000"}]}}}}
    rows = fsc.parse_prices(payload)
    assert [r["ticker"] for r in rows] == ["005930", "000660"]
    assert fsc.total_count(payload) == 2


def test_dart_parse_multi_maps_accounts():
    payload = {"status": "000", "list": [
        {"stock_code": "005930", "fs_div": "CFS", "sj_div": "IS", "account_nm": "매출액",
         "thstrm_amount": "1,000", "thstrm_add_amount": "3,000", "rcept_no": "1"},
        {"stock_code": "005930", "fs_div": "CFS", "sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "5,000"},
        {"stock_code": "000660", "fs_div": "OFS", "sj_div": "IS", "account_nm": "당기순이익(손실)", "thstrm_amount": "-10"},
    ]}
    from kic_data.collectors import dart
    out = dart.parse_multi(payload, 2025, "11012")
    assert set(out) == {"005930", "000660"}
    rev = out["005930"][0]
    assert rev["account_id"] == "ifrs-full_Revenue" and rev["amount_ytd"] is not None and rev["report_code"] == "11012"
    assert out["005930"][1]["account_id"] == "ifrs-full_Assets"
    assert out["000660"][0]["account_id"] == "ifrs-full_ProfitLoss" and out["000660"][0]["fs_div"] == "OFS"
    assert dart.parse_multi({"status": "013"}, 2025, "11011") == {}


def test_spread_job_waits_between_chunks():
    job = jobs.JOB_BY_NAME["youtube"]
    now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    st = {"last_status": "partial", "cursor": 40, "last_finished": now - timedelta(hours=2), "last_complete": None}
    assert jobs.due_reason(job, st, now, []) is None
    st["last_finished"] = now - timedelta(days=1)
    assert jobs.due_reason(job, st, now, []) == "이어서"
    news = jobs.JOB_BY_NAME["news"]  # 보통 작업은 남은 묶음을 바로 이어서
    assert jobs.due_reason(news, {**st, "last_finished": now - timedelta(minutes=50)}, now, []) == "이어서"


def test_kospi200_jobs_wired():
    names = [j.name for j in jobs.JOBS]
    assert names.index("sync-kospi200") < names.index("sync-dart-codes")
    assert set(jobs.PER_TICKER_BACKFILLS) <= set(names)
    assert not jobs.JOB_BY_NAME["kr-prices"].split  # 전 종목 조회
    from kic_data import cli
    for j in jobs.JOBS:
        assert hasattr(cli, j.command), j.command
