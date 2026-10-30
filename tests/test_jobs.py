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


def test_tick_backfill_leaves_room_for_regular(monkeypatch):
    calls = []
    fake = [jobs.Job("bf", "bf", "cmd_bf", every=jobs.ONCE, split=("KR",), chunk=1, group="과거분(1회)"),
            jobs.Job("hourly", "hourly", "cmd_x")]
    monkeypatch.setattr(jobs, "JOBS", fake)
    clock = {"t": 0.0}

    def step(name):
        def fn(ns):
            calls.append(name)
            clock["t"] += 100
        return fn

    st = MemState()
    keys = [("KR", str(i)) for i in range(50)]
    res = jobs.tick(st, budget=1000, hard_limit=1300, commands=SimpleNamespace(cmd_bf=step("bf"), cmd_x=step("x")),
                    getenv=env_all, clock=lambda: clock["t"], universe_for=lambda j: keys if j.split else None)
    # 과거분은 예산 절반(500초) 안에서만 이어 가고, 매시간 작업은 그 뒤에 실행
    assert calls.count("bf") == 5 and calls[-1] == "x" and res["waiting"] == ["bf"]


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

def test_parse_kospi200_enroll():
    from kic_data.collectors import kospi200
    payload = [{"itemCode": "005930", "stockName": "삼성전자"}, {"itemCode": "000660", "stockName": "SK하이닉스"}, {"x": 1}]
    assert kospi200.parse_enroll(payload) == [("005930", "삼성전자"), ("000660", "SK하이닉스")]
    assert kospi200.parse_enroll({"error": 1}) == []


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


def test_kospi200_jobs_wired():
    names = [j.name for j in jobs.JOBS]
    assert names.index("sync-kospi200") < names.index("sync-dart-codes")
    assert set(jobs.PER_TICKER_BACKFILLS) <= set(names)
    assert not jobs.JOB_BY_NAME["kr-prices"].split  # 전 종목 조회
    from kic_data import cli
    for j in jobs.JOBS:
        assert hasattr(cli, j.command), j.command


def test_unreachable_source_fails_fast(monkeypatch):
    import requests
    from kic_data import http_util

    calls = []

    class S:
        def get(self, *a, **k):
            calls.append(k.get("timeout"))
            raise requests.ConnectTimeout("x")

    http_util._down.clear()
    with pytest.raises(RuntimeError):
        http_util.get(S(), "fsc", "https://example.invalid")
    with pytest.raises(RuntimeError, match="건너뜀"):
        http_util.get(S(), "fsc-etf", "https://example.invalid")  # 같은 서버는 바로 실패
    assert len(calls) == 1 and calls[0][0] == http_util.CONNECT_TIMEOUT
    http_util._down.clear()


def test_new_macro_series_backfill_start(monkeypatch):
    from datetime import date, timedelta

    from kic_data import cli, db
    from kic_data.collectors import macro

    ids = [r[0] for r in macro.ECOS_SERIES] + [r[0] for r in macro.FRED_SERIES]
    assert len(ids) == len(set(ids))
    assert {"ECOS_CPI", "ECOS_KTB3Y", "ECOS_KTB10Y", "DEXJPUS", "DEXCHUS", "DCOILWTICO"} <= set(ids)
    assert {"PCOPPUSDM", "PIORECRUSDM", "PNGASJPUSDM", "QKRN628BIS", "ECOS_CHN_EXPORT"} <= set(ids)
    start = date.today() - timedelta(days=365)
    monkeypatch.setattr(db, "has_macro", lambda conn, sid: sid == "OLD")
    assert cli._macro_start(None, "OLD", start) == start
    assert cli._macro_start(None, "NEW", start) <= date.today() - timedelta(days=365 * 5)

def test_sync_kospi200_resets_prep_jobs():
    names = [j.name for j in jobs.JOBS]
    assert set(jobs.PREP_JOBS) <= set(names) and set(jobs.DART_FOLLOWUPS) <= set(names)
    # 준비 작업이 과거분보다 앞에 있어야 같은 실행 안에서 고유번호가 먼저 채워짐
    for prep in jobs.PREP_JOBS:
        assert names.index(prep) < names.index("backfill-dart-annual")


def test_sync_dart_resets_followups_only_for_newly_linked(monkeypatch):
    from contextlib import contextmanager

    from kic_data import cli, db
    from kic_data.collectors import dart

    resets = []

    @contextmanager
    def fake_connect():
        yield object()

    monkeypatch.setattr(cli, "make_session", lambda: None)
    monkeypatch.setattr(dart, "fetch_corp_codes", lambda s: [{"stock_code": "005930", "corp_code": "00126380"},
                                                             {"stock_code": "000660", "corp_code": "00164779"}])
    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "get_or_create_company", lambda *a, **k: 1)
    monkeypatch.setattr(jobs, "reset_backfills", lambda conn, names: resets.append(tuple(names)))

    # 이미 연결된 종목만 있으면 다시 받지 않음
    monkeypatch.setattr(db, "list_companies", lambda conn, m: [(1, "005930", "삼성전자", "00126380", None)])
    assert cli.cmd_sync_dart(None) == 0
    assert resets == []

    # 고유번호가 없던 종목이 새로 연결되면 업종·공시·재무 과거분을 다시 받음
    monkeypatch.setattr(db, "list_companies", lambda conn, m: [(1, "005930", "삼성전자", "00126380", None),
                                                               (2, "000660", "SK하이닉스", None, None)])
    assert cli.cmd_sync_dart(None) == 0
    assert resets == [jobs.DART_FOLLOWUPS]

def test_etf_uses_v2_endpoint_and_inclusive_end(monkeypatch):
    calls = {}

    def fake_get_json(session, name, url, params=None, limiter=None):
        calls.update(url=url, params=params)
        return {"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": [
            {"basDt": "20261023", "srtnCd": "069500", "clpr": "40000"},
            {"basDt": "20261023", "srtnCd": "0695001", "clpr": "1"}]}}}}

    monkeypatch.setattr(etf, "get_json", fake_get_json)
    monkeypatch.setattr(etf, "data_go_key", lambda: "k")
    rows = etf.fetch_etf(None, "069500", "20261001", "20261023")
    assert calls["url"].endswith("/1160100/GetSecuritiesProductInfoService_V2/getETFPriceInfo_V2")
    assert calls["params"]["endBasDt"] == "20261024" and calls["params"]["beginBasDt"] == "20261001"
    assert [r["ticker"] for r in rows] == ["069500"]
    assert etf.next_ymd("20261231") == "20270101"


def test_run_job_more_keeps_job_due_until_done():
    st = MemState()
    job = jobs.Job("bf", "bf", "cmd_x", every=jobs.ONCE, group="과거분(1회)")
    rcs = iter([jobs.MORE, 0])
    r1 = jobs.run_job(job, st, commands=SimpleNamespace(cmd_x=lambda ns: next(rcs)), getenv=env_all)
    assert r1["status"] == "partial" and r1["more"] and not st.rows["bf"].get("last_complete")
    r2 = jobs.run_job(job, st, commands=SimpleNamespace(cmd_x=lambda ns: next(rcs)), getenv=env_all)
    assert r2["status"] == "ok" and st.rows["bf"].get("last_complete")


def test_report_due_dates():
    from datetime import date

    from kic_data import cli

    today = date(2026, 10, 27)
    assert cli.report_due(2026, "11012", today) and cli.report_due(2026, "11013", today)
    assert not cli.report_due(2026, "11014", today)          # 3분기 보고서는 11/14 기한
    assert cli.report_due(2025, "11011", today) and not cli.report_due(2026, "11011", today)


def test_dart_multi_skips_covered_and_stops_on_budget(monkeypatch):
    from contextlib import contextmanager

    from kic_data import cli
    from kic_data.collectors import dart

    companies = [(i, f"{i:06d}", f"c{i}", f"C{i:07d}", None) for i in range(1, 251)]  # 3묶음 (100, 100, 50)

    @contextmanager
    def fake_connect():
        yield None

    calls = []
    monkeypatch.setattr(cli.db, "connect", fake_connect)
    monkeypatch.setattr(cli.db, "list_companies", lambda conn, m: companies)
    monkeypatch.setattr(cli.db, "fact_companies", lambda conn, y, code, src: set(range(1, 101)))  # 첫 묶음은 이미 있음
    monkeypatch.setattr(cli.db, "upsert_facts", lambda conn, cid, rows, src: len(rows))
    monkeypatch.setattr(cli.db, "mark_multi_fetched", lambda conn, y, code, status: None)
    monkeypatch.setattr(cli.db, "multi_fetched", lambda conn, y, code: set())
    monkeypatch.setattr(cli, "make_session", lambda: None)

    def fetch(s, chunk, year, code):
        calls.append((year, code, chunk[0]))
        return {chunk[0]: [{"x": 1}]}

    monkeypatch.setattr(dart, "fetch_multi", fetch)
    args = SimpleNamespace(from_year=2025, reports="11013", max_seconds=None)
    assert cli.cmd_dart_multi(args) == 0
    assert [c[2] for c in calls if c[0] == 2025] == ["C0000101", "C0000201"]  # 첫 묶음 건너뜀
    calls.clear()
    ticks = iter([0.0, 0.0, 999.0, 999.0, 999.0])
    monkeypatch.setattr("time.monotonic", lambda: next(ticks))
    args = SimpleNamespace(from_year=2025, reports="11013", max_seconds=10)
    assert cli.cmd_dart_multi(args) == jobs.MORE
    assert len(calls) == 1


def test_every_job_has_one_lane_and_each_api_stays_in_one_lane():
    from kic_data import quality

    lane_of_api = {}
    for j in jobs.JOBS:
        lane = jobs.lane_of(j)
        assert lane in jobs.LANES, (j.name, lane)
        api = quality.API_OF_JOB.get(j.name)
        if api and lane != jobs.PREP_LANE:
            assert lane_of_api.setdefault(api, lane) == lane, api  # 같은 API 를 두 줄이 동시에 부르지 않음
    assert jobs.lane_of(jobs.JOB_BY_NAME["apply-schema"]) == jobs.PREP_LANE
    assert jobs.lane_of(jobs.JOB_BY_NAME["build-quarterly"]) == jobs.CALC_LANE
    assert jobs.lane_of(jobs.JOB_BY_NAME["youtube"]) == "global"
    assert jobs.lane_of(jobs.JOB_BY_NAME["backfill-dart-quarterly"]) == "dart"


def test_tick_lane_runs_only_that_lane(monkeypatch):
    ran = []
    fake = [jobs.JOB_BY_NAME[n] for n in ("apply-schema", "disclosures", "kr-prices", "youtube", "build-features")]
    monkeypatch.setattr(jobs, "JOBS", fake)
    cmds = SimpleNamespace(**{j.command: (lambda name: lambda ns: ran.append(name))(j.name) for j in fake})
    jobs.tick(MemState(), budget=1000, hard_limit=1300, commands=cmds, getenv=env_all, lane="global",
              universe_for=lambda j: None)
    assert ran == ["youtube"]


def test_workflow_matrix_matches_collect_lanes():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / ".github/workflows/collect.yml").read_text(encoding="utf-8")
    assert f"lane: [{', '.join(jobs.COLLECT_LANES)}]" in text
    assert f"--lane {jobs.PREP_LANE}" in text and f"--lane {jobs.CALC_LANE}" in text


def test_apply_schema_skips_locked_file_instead_of_waiting(tmp_path, monkeypatch):
    import psycopg

    (tmp_path / "001_a.sql").write_text("SELECT 1", encoding="utf-8")
    (tmp_path / "002_alter.sql").write_text("ALTER TABLE t ADD COLUMN IF NOT EXISTS x int", encoding="utf-8")
    monkeypatch.setattr(db, "SQL_DIR", tmp_path)
    sent, state = [], {"rolled_back": 0}

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql):
            sent.append(sql)
            if sql.startswith("ALTER"):  # 다른 연결이 테이블을 쓰고 있어 잠금을 못 잡음
                raise psycopg.errors.LockNotAvailable("lock timeout")

    conn = SimpleNamespace(cursor=Cur, commit=lambda: None,
                           rollback=lambda: state.__setitem__("rolled_back", state["rolled_back"] + 1))
    busy = []
    assert db.apply_schema(conn, lock_timeout="5s", busy=busy) == ["001_a.sql"]
    assert busy == ["002_alter.sql"] and state["rolled_back"] == 1
    assert "SET LOCAL lock_timeout = '5s'" in sent
    with pytest.raises(psycopg.errors.LockNotAvailable):  # busy 를 안 넘기면(처음 적용) 그대로 실패
        db.apply_schema(conn, lock_timeout="5s")


def test_after_kst_runs_once_per_day_after_close():
    """투자자별 수급: 한국시간 18:30 이후 하루 한 번 (장 마감 뒤 확정 자료)."""
    from datetime import time as dtime

    job = jobs.Job("f", "f", "cmd_x", every=jobs.DAY, after_kst=dtime(18, 30))
    kst = jobs.KST
    morning = datetime(2026, 10, 28, 10, 0, tzinfo=kst)
    evening = datetime(2026, 10, 28, 18, 45, tzinfo=kst)
    # 전날 저녁에 끝났으면 오늘 오전에는 돌지 않음 (예전 '24시간 뒤' 규칙이면 오전에 돌아 그날 수급을 놓침)
    done_last_night = {"last_complete": datetime(2026, 10, 27, 19, 0, tzinfo=kst)}
    assert jobs.due_reason(job, done_last_night, morning, []) is None
    assert jobs.due_reason(job, done_last_night, evening, []) == "기준 시각 이후"
    # 오늘 18:30 이후 이미 끝났으면 다시 돌지 않음
    done_tonight = {"last_complete": datetime(2026, 10, 28, 19, 0, tzinfo=kst)}
    assert jobs.due_reason(job, done_tonight, datetime(2026, 10, 28, 23, 0, tzinfo=kst), []) is None
    # 오늘 오전에 끝난 것은 오늘 몫이 아님 → 18:30 이후 다시
    done_morning = {"last_complete": datetime(2026, 10, 28, 9, 0, tzinfo=kst)}
    assert jobs.due_reason(job, done_morning, morning, []) is None
    assert jobs.due_reason(job, done_morning, evening, []) == "기준 시각 이후"
    # 처음·이어서는 그대로
    assert jobs.due_reason(job, None, morning, []) == "처음"
    assert jobs.due_reason(job, {"cursor": 50, "last_complete": done_last_night["last_complete"]}, morning, []) == "이어서"
    # 다음 예정: 완료 다음 18:30
    assert jobs.next_due(job, done_tonight) == datetime(2026, 10, 29, 18, 30, tzinfo=kst)
    assert jobs.next_due(job, done_morning) == datetime(2026, 10, 28, 18, 30, tzinfo=kst)


def test_investor_flows_job_is_after_close():
    job = next(j for j in jobs.JOBS if j.name == "investor-flows")
    assert job.after_kst is not None and (job.after_kst.hour, job.after_kst.minute) >= (18, 0)
    assert job.recheck_until_kst is not None and job.kwargs.get("only_lagging") is True


def test_after_kst_recheck_until_evening_cutoff():
    """네이버가 그날 수급을 늦게 올리므로(10-29 19:20 에도 미게시) 23:30 까지 1시간마다 다시 봄."""
    from datetime import time as dtime

    job = jobs.Job("f", "f", "cmd_x", every=jobs.DAY, after_kst=dtime(18, 30), recheck_until_kst=dtime(23, 30))
    kst = jobs.KST
    done = {"last_complete": datetime(2026, 10, 29, 19, 20, tzinfo=kst)}
    assert jobs.due_reason(job, done, datetime(2026, 10, 29, 19, 50, tzinfo=kst), []) is None
    assert jobs.due_reason(job, done, datetime(2026, 10, 29, 20, 20, tzinfo=kst), []) == "재확인"
    assert jobs.next_due(job, done) == datetime(2026, 10, 29, 20, 15, tzinfo=kst)
    # 마지막 재확인(23:00 완료) 뒤에는 다음 날 18:30 까지 쉼
    late = {"last_complete": datetime(2026, 10, 29, 23, 0, tzinfo=kst)}
    assert jobs.due_reason(job, late, datetime(2026, 10, 30, 0, 30, tzinfo=kst), []) is None
    assert jobs.due_reason(job, late, datetime(2026, 10, 30, 10, 0, tzinfo=kst), []) is None
    assert jobs.next_due(job, late) == datetime(2026, 10, 30, 18, 30, tzinfo=kst)
    assert jobs.due_reason(job, late, datetime(2026, 10, 30, 18, 40, tzinfo=kst), []) == "기준 시각 이후"
    # 오전에 수동으로 끝낸 것은 재확인 대상이 아님 (그날 18:30 에 한 번)
    morning = {"last_complete": datetime(2026, 10, 30, 9, 0, tzinfo=kst)}
    assert jobs.due_reason(job, morning, datetime(2026, 10, 30, 11, 0, tzinfo=kst), []) is None
