"""웹 자동 실행기: cli.py 의 수집 명령을 시간표대로 나눠 실행합니다 (Vercel 서버리스용).

서버리스 함수는 한 번 호출에 최대 300초라 '전체 수집을 한 번에' 돌릴 수 없습니다. 그래서
  - 작업(Job)마다 주기를 두고, ops.job_state 에 마지막 완료 시각을 남깁니다.
  - /api/cron/tick 을 한 번 부를 때마다 '지금 할 차례인' 작업을 시간 예산(기본 240초) 안에서 차례로 실행합니다.
  - 종목이 많은 작업은 chunk 개씩 나눠 처리하고(cursor), 다음 tick 에서 이어서 합니다.
tick 을 10~15분마다 불러 주면(GitHub Actions 또는 외부 크론) 모든 작업이 주기대로 돌아갑니다.

키(환경변수)가 없는 작업은 실패가 아니라 '건너뜀' 으로 기록하고, 키를 넣으면 다음 tick 부터 실행됩니다.
"""
from __future__ import annotations

import io
import os
import time
import traceback
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from typing import Callable

from . import db, quality

HOUR = timedelta(hours=1)
EVERY_RUN = timedelta(0)  # 매 tick 실행 (기본값 HOUR 면 1시간 안의 두 번째 실행에서 건너뜀 → 새 sql 열 없이 수집이 돌아 실패)
DAY = timedelta(days=1)
WEEK = timedelta(days=7)
SIX_HOURS = timedelta(hours=6)
ONCE = None  # 한 번 끝나면 다시 하지 않음 (대시보드에서 수동 재실행 가능)

LOG_LIMIT = 20000  # job_run.log 에 남길 최대 글자 수
STALE_RUNNING = timedelta(minutes=6)  # 함수가 시간 초과로 끊겨 running 이 남은 경우
RETRY_AFTER_FAIL = timedelta(minutes=30)
RETRY_AFTER_SKIP = timedelta(hours=1)
RETRY_ONCE_WARN = timedelta(hours=6)  # 1회 작업이 일부 실패로 끝나면 이 간격으로 다시 (키 승인 대기 등)


@dataclass
class Job:
    name: str
    label: str
    command: str  # cli.py 의 함수 이름 (cmd_*)
    kwargs: dict = field(default_factory=dict)  # argparse Namespace 로 넘길 값 (date 는 호출 때 계산하도록 callable 허용)
    every: timedelta | None = HOUR
    needs: tuple[str, ...] = ()  # 모두 있어야 하는 환경변수
    needs_any: tuple[str, ...] = ()  # 하나 이상 있어야 하는 환경변수
    split: tuple[str, ...] = ()  # 종목을 나눠 처리할 시장 ("KR", "US")
    chunk: int = 6
    group: str = "매시간"
    after_kst: dtime | None = None  # 하루 한 번, 한국시간 이 시각 이후에만 (장 마감 뒤 확정되는 자료용)

    def namespace(self) -> Namespace:
        return Namespace(**{k: (v() if callable(v) else v) for k, v in self.kwargs.items()})

    def missing_env(self, getenv: Callable[[str], str | None]) -> list[str]:
        miss = [k for k in self.needs if not getenv(k)]
        if self.needs_any and not any(getenv(k) for k in self.needs_any):
            miss.append(" 또는 ".join(self.needs_any))
        return miss


def _years_ago(n: int):
    return lambda: date.today().year - n


def _ym_ago(months: int):
    """months 개월 전 달을 'YYYYMM' 으로 (호출 때 계산)."""
    def calc():
        t = date.today()
        idx = t.year * 12 + t.month - 1 - months
        return f"{idx // 12}{idx % 12 + 1:02d}"
    return calc


DB = ("DATABASE_URL",)
NAVER = ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET")
DATA_GO = ("DATA_GO_KR_KEY", "FSC_API_KEY")  # 공공데이터포털 일반 인증키 (둘 중 하나)
HS_SEMI = "854232,854231"  # 반도체 메모리, 프로세서·컨트롤러

Q_REPORTS = "11013,11012,11014"  # 1분기, 반기, 3분기
NEWS_KEYS = ("NCP_APIHUB_KEY_ID", "NAVER_CLIENT_ID")

# 실행 순서 = 목록 순서. 준비 → 과거분(1회) → 매시간 수집 → 6시간 수집 → 감성·언급량 → 재무 → 지표 재계산
# 대상: KOSPI 200 전 종목(+ universe.py 의 반도체 비교군, 해외 종목). 종목이 많아 시세·공시·재무는 전 종목/다중회사
# 조회로 몇 번만 부르고, 종목마다 불러야 하는 네이버 뉴스·블로그·카페·언급량·검색 관심도는 하루 한도 때문에 6시간마다.
JOBS: list[Job] = [
    # 준비
    Job("apply-schema", "DB 스키마 적용", "cmd_apply_schema", every=EVERY_RUN, needs=DB, group="준비"),  # 매 실행 (몇 초, 새 sql 파일이 바로 반영되게)
    Job("seed-companies", "종목 등록 (universe.py)", "cmd_seed", every=DAY, needs=DB, group="준비"),
    Job("sync-kospi200", "KOSPI 200 종목 마스터", "cmd_sync_kospi200", every=DAY, needs=DB, group="준비"),
    Job("sync-dart-codes", "DART 고유번호 연결", "cmd_sync_dart", every=DAY, needs=DB + ("DART_API_KEY",), group="준비"),
    Job("company-info", "업종(KSIC→KRX)·발행주식수", "cmd_company_info", {"skip_kr_shares": True}, every=WEEK,
        needs=DB + ("DART_API_KEY", "SEC_USER_AGENT"), split=("KR", "US"), chunk=25, group="준비"),
    # 과거분 (한 번만): 시계열·학습용. 새 종목이 들어오면 sync-kospi200 이 다시 하도록 되돌립니다.
    Job("backfill-kr-prices", "국내 시세 5년", "cmd_kr_prices", {"days": 1825}, every=ONCE,
        needs=DB + ("FSC_API_KEY",), split=("KR",), chunk=10, group="과거분(1회)"),
    Job("backfill-us-prices", "해외 시세 5년", "cmd_us_prices", {"days": 1825}, every=ONCE,
        needs=DB, split=("US",), chunk=6, group="과거분(1회)"),
    Job("backfill-indices", "시장 지수 5년", "cmd_indices", {"days": 1825}, every=ONCE, needs=DB, group="과거분(1회)"),
    Job("backfill-etf", "ETF 시세 5년", "cmd_etf", {"days": 1825}, every=ONCE, needs=DB + ("FSC_API_KEY",), group="과거분(1회)"),
    Job("backfill-macro", "거시지표 5년", "cmd_macro", {"years": 5}, every=ONCE, needs=DB,
        needs_any=("ECOS_API_KEY", "FRED_API_KEY"), group="과거분(1회)"),
    Job("backfill-disclosures", "공시 목록 1년", "cmd_disclosures", {"days": 365, "types": "A,B,I", "bulk": False},
        every=ONCE, needs=DB + ("DART_API_KEY", "SEC_USER_AGENT"), split=("KR", "US"), chunk=10, group="과거분(1회)"),
    Job("backfill-news", "뉴스 30일", "cmd_news", {"days": 30, "max_items": 300}, every=ONCE,
        needs=DB + NAVER, split=("KR", "US"), chunk=10, group="과거분(1회)"),
    Job("backfill-community", "블로그·카페 30일", "cmd_community",
        {"days": 30, "sources": "naver_blog,naver_cafe", "pages": 2}, every=ONCE,
        needs=DB + NAVER, split=("KR", "US"), chunk=10, group="과거분(1회)"),
    Job("backfill-blog-deep", "블로그 30일 깊게(검색어당 최대 1,000건)", "cmd_community",
        {"days": 30, "sources": "naver_blog", "pages": 10}, every=ONCE,
        needs=DB + NAVER, split=("KR", "US"), chunk=10, group="과거분(1회)"),
    # ↑ backfill-community(2페이지)는 글이 많은 대형주에서 며칠치뿐이라 감성 변화의 직전 기간(8~30일 전)이 비었음.
    #   블로그만 작성일이 있어 깊게 받음 (카페는 날짜가 없어 과거분이 의미 없음). 30일보다 오래된 페이지에 닿으면 멈춤
    Job("backfill-news-gaps", "뉴스 30일(뉴스가 없는 종목만)", "cmd_news",
        {"days": 30, "max_items": 300, "only_missing": True}, every=ONCE, needs=DB + NAVER, group="과거분(1회)"),
    Job("backfill-community-gaps", "블로그·카페 30일(글이 없는 종목만)", "cmd_community",
        {"days": 30, "sources": "naver_blog,naver_cafe", "pages": 10, "only_missing": True}, every=ONCE,
        needs=DB + NAVER, group="과거분(1회)"),
    # ↑ 검색어 별칭·종목명 단독 검색·광고어 필터를 고친 뒤(10-29) 0건이던 종목(SKIET·코스모화학·DB손해보험 등)만 다시 받기
    Job("backfill-trends", "검색 관심도 1년", "cmd_trends", {"days": 365}, every=ONCE, needs=DB,
        needs_any=NEWS_KEYS, group="과거분(1회)"),
    Job("backfill-customs", "관세청 반도체 수출입 5년", "cmd_customs", {"from_ym": _ym_ago(60), "hs": HS_SEMI}, every=ONCE,
        needs=DB, needs_any=DATA_GO, group="과거분(1회)"),
    Job("backfill-investor-flows", "투자자별 순매수 1년", "cmd_investor_flows", {"days": 365}, every=ONCE,
        needs=DB, split=("KR",), chunk=20, group="과거분(1회)"),
    Job("backfill-dart-annual", "국내 연간 재무 5년(다중회사)", "cmd_dart_multi",
        {"from_year": _years_ago(5), "reports": "11011", "max_seconds": 300}, every=ONCE, needs=DB + ("DART_API_KEY",), group="과거분(1회)"),
    Job("backfill-dart-quarterly", "국내 분기 재무 2년(다중회사)", "cmd_dart_multi",
        {"from_year": _years_ago(2), "reports": Q_REPORTS, "max_seconds": 300}, every=ONCE, needs=DB + ("DART_API_KEY",), group="과거분(1회)"),
    # 10-28: 에이전트 1 제작 기준(P01) 의 분기 매출 기준선·순차평가에 독립 분기가 4개뿐이라 5년으로 늘림.
    # 이미 받은 연도·보고서(묶음의 90% 이상)는 건너뛰고, 300초마다 끊어 다음 실행에서 이어 받음.
    Job("backfill-dart-quarterly-5y", "국내 분기 재무 5년(다중회사)", "cmd_dart_multi",
        {"from_year": _years_ago(5), "reports": Q_REPORTS, "max_seconds": 300}, every=ONCE, needs=DB + ("DART_API_KEY",),
        group="과거분(1회)"),
    # 매시간: 전 종목/시장 전체 조회라 종목 수와 거의 무관
    Job("kr-prices", "국내 시세(전 종목 조회)", "cmd_kr_prices", {"days": 10}, needs=DB + ("FSC_API_KEY",)),
    Job("us-prices", "해외 시세", "cmd_us_prices", {"days": 10}, needs=DB, split=("US",), chunk=10),
    Job("indices", "시장 지수", "cmd_indices", {"days": 10}, needs=DB),
    Job("etf-prices", "ETF 시세", "cmd_etf", {"days": 10}, needs=DB + ("FSC_API_KEY",)),
    Job("us-etf-prices", "해외 ETF 시세(SPY·VOO·QQQ)", "cmd_us_etf", {"days": 10}, needs=DB),
    Job("macro", "거시·업황 지표", "cmd_macro", {"years": 1}, needs=DB, needs_any=("ECOS_API_KEY", "FRED_API_KEY")),
    Job("disclosures", "공시 목록(시장 전체 조회)", "cmd_disclosures", {"days": 3, "types": "A,B,I", "bulk": True},
        needs=DB + ("DART_API_KEY", "SEC_USER_AGENT")),
    Job("finnhub-news", "해외 영문 뉴스", "cmd_finnhub", {"days": 3}, needs=DB + ("FINNHUB_API_KEY",), split=("US",), chunk=20),
    # 6시간마다: 종목마다 부르는 네이버 API (검색 하루 25,000회, 데이터랩 1,000회 한도)
    Job("news", "네이버 뉴스", "cmd_news", {"days": 2, "max_items": 300}, every=SIX_HOURS,
        needs=DB + NAVER, split=("KR", "US"), chunk=25),
    Job("community", "블로그·카페 반응", "cmd_community", {"days": 2, "sources": "naver_blog,naver_cafe", "pages": 1},
        every=SIX_HOURS, needs=DB + NAVER, split=("KR", "US"), chunk=25),
    Job("trends", "검색 관심도", "cmd_trends", {"days": 14}, every=SIX_HOURS, needs=DB, needs_any=NEWS_KEYS),
    Job("youtube", "유튜브 공개 영상·댓글(대형주)", "cmd_youtube", {"days": 7, "videos": 5, "comments": 100, "top_kr": 40,
                                                                    "order": "viewCount"},
        every=DAY, needs=DB + ("YOUTUBE_API_KEY",)),
    # ↑ 검색 1회 100단위(하루 10,000)라 영상이 많은 국내 시총 상위 40종목 + 해외 종목만 매일 (약 5,700 + 댓글 수백)
    Job("youtube-rest", "유튜브 나머지 국내 종목(하루 25개씩 순환)", "cmd_youtube",
        {"days": 14, "videos": 3, "comments": 50, "rotate_rest": (40, 25), "order": "viewCount"},
        every=DAY, needs=DB + ("YOUTUBE_API_KEY",)),
    # ↑ 시총 상위 40 밖의 국내 약 170종목을 약 1주에 한 번씩 (검색 25회 × 100단위 ≈ 2,500 + 통계·댓글 ≈ 하루 2,600)
    Job("investor-flows", "투자자별 순매수(외국인·기관·개인)", "cmd_investor_flows", {"days": 10}, every=DAY,
        needs=DB, split=("KR",), chunk=50, after_kst=dtime(18, 30)),
    # ↑ 네이버 비공식 API 라 종목당 하루 1회만. 투자자별 수치는 장 마감 뒤 확정되므로 한국시간 18:30 이후에 돌려
    #   그날 수급까지 받음 (이전에는 마지막 완료 + 24시간이라 오전에 돌면 전 거래일까지만 들어와 가격보다 하루 늦었음)
    Job("lending", "대차거래(코스피 전 종목, 공매도 대용)", "cmd_lending", {"days": 365, "max_days": 40},
        needs=DB, needs_any=DATA_GO),  # 빠진 거래일만 최근부터 40일씩 (1년치는 몇 시간에 걸쳐 채움, 이후엔 하루 1~2회 호출)
    Job("bond-issue", "회사채 발행 종목(표면이율·고정/변동·만기, 금융위원회)", "cmd_bond_issue", {"bas_dt": None},
        every=DAY, needs=DB + ("DART_API_KEY",), needs_any=DATA_GO, group="재무", after_kst=dtime(13, 30)),
    # ↑ 회사당 1회(약 210회/일, 키 10,000회/일). 원천은 기준일 다음 영업일 13시 이후 갱신
    Job("customs", "관세청 반도체 수출입", "cmd_customs", {"from_ym": _ym_ago(13), "hs": HS_SEMI}, every=DAY,
        needs=DB, needs_any=DATA_GO),  # 월 단위 자료(매월 중순 전월분 갱신)라 하루 1회
    Job("sentiment", "감성 점수", "cmd_sentiment", {"method": None, "limit": None, "rescore": False}, needs=DB),
    Job("mentions", "언급량 스냅샷", "cmd_pulse", {"tickers": None, "max_age": 0, "market_hours": False},
        every=SIX_HOURS, needs=DB + NAVER, split=("KR", "US"), chunk=25),
    # 재무: 국내는 다중회사 주요계정(100개사씩)이라 몇 번의 호출로 끝남
    Job("debt-terms", "차입 조건(사채·CP 만기·이자율)·매출채권·총차입금", "cmd_debt_terms", {"apis": None, "force": False},
        every=DAY, needs=DB + ("DART_API_KEY",), split=("KR",), chunk=10, group="재무"),
    # ↑ 회사당 보고서 1~2개 × API 5개(매출채권·총차입금은 같은 응답 1회). 이미 받은 보고서는 건너뛰어 새 정기보고서가 나올 때만 호출
    Job("report-exposure", "사업보고서 원문 노출(환율·이자율 민감도·수출 비중)", "cmd_report_exposure", {"force": False},
        every=DAY, needs=DB + ("DART_API_KEY",), split=("KR",), chunk=8, group="재무"),
    # ↑ 회사마다 원문 zip(약 10MB) 1개. 이미 읽은 접수번호는 건너뛰어 평소에는 거의 호출 없음
    Job("dart-annual", "국내 연간 재무(최근 2년)", "cmd_dart_multi", {"from_year": _years_ago(2), "reports": "11011"},
        every=DAY, needs=DB + ("DART_API_KEY",), group="재무"),
    Job("dart-quarterly", "국내 분기 재무(올해)", "cmd_dart_multi", {"from_year": _years_ago(0), "reports": Q_REPORTS},
        every=DAY, needs=DB + ("DART_API_KEY",), group="재무"),
    Job("sec-annual", "해외 연간 재무", "cmd_sec", {"from_year": _years_ago(5)}, every=DAY,
        needs=DB + ("SEC_USER_AGENT",), split=("US",), chunk=5, group="재무"),
    Job("sec-quarterly", "해외 분기 재무", "cmd_sec_q", {"from_year": _years_ago(2)}, every=DAY,
        needs=DB + ("SEC_USER_AGENT",), split=("US",), chunk=5, group="재무"),
    Job("build-quarterly", "분기 단독 값·TTM", "cmd_build_quarterly", every=DAY, needs=DB, group="재무"),
    Job("build-peer-groups", "Peer Group(KRX 업종·반도체)", "cmd_build_peers", every=DAY, needs=DB, group="재무"),
    Job("build-features", "5개 관점 지표 재계산", "cmd_build_features", needs=DB),
]
# 새 종목이 들어오면 다시 해야 하는 과거분 (종목마다 받는 것)
PER_TICKER_BACKFILLS = ("backfill-kr-prices", "backfill-disclosures", "backfill-news", "backfill-community",
                        "backfill-news-gaps", "backfill-community-gaps",
                        "backfill-blog-deep",
                        "backfill-trends", "backfill-investor-flows", "backfill-dart-annual", "backfill-dart-quarterly")
# 종목 마스터가 바뀌면 과거분보다 먼저 다시 돌려야 하는 준비 작업 (DART 고유번호·업종이 있어야 재무·공시·Peer 를 만듦)
PREP_JOBS = ("sync-dart-codes", "company-info")
# DART 고유번호가 새로 연결된 종목이 생기면 다시 받아야 하는 작업
DART_FOLLOWUPS = ("company-info", "backfill-disclosures", "backfill-dart-annual", "backfill-dart-quarterly")
JOB_BY_NAME = {j.name: j for j in JOBS}

# 병렬 실행 줄(lane): GitHub Actions 가 줄마다 따로 tick 을 돌려 같은 시간에 함께 수집합니다.
# 같은 원천 API 는 한 줄에만 두어 동시 호출로 한도·차단 위험이 커지지 않게 합니다 (원천은 quality.API_OF_JOB).
# 준비(스키마·종목 마스터)는 다른 줄보다 먼저, 계산(감성·TTM·Peer·지표)은 수집 줄이 끝난 뒤에 돕니다.
PREP_LANE, CALC_LANE = "준비", "계산"
PREP_LANE_JOBS = ("apply-schema", "seed-companies", "sync-kospi200")
LANE_OF_API = {
    "dart": "dart", "sec": "dart",                      # 공시·재무
    "fsc": "public", "data.go.kr": "public",            # 공공데이터포털 (같은 게이트웨이)
    "naver": "naver",                                   # 네이버 검색 API (뉴스·블로그·카페·언급량)
    "naver_datalab": "naver_etc", "naver_finance": "naver_etc",
    "yfinance": "global", "ecos·fred": "global", "finnhub": "global", "youtube": "global",
}
COLLECT_LANES = ("dart", "public", "naver", "naver_etc", "global")
LANES = (PREP_LANE, *COLLECT_LANES, CALC_LANE)


def lane_of(job: Job) -> str:
    if job.name in PREP_LANE_JOBS:
        return PREP_LANE
    api = quality.API_OF_JOB.get(job.name)
    return LANE_OF_API[api] if api else CALC_LANE


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- 상태 저장소

class DbState:
    """ops.job_state / ops.job_run (sql/013). 테스트에서는 같은 메서드를 가진 메모리 저장소를 씁니다.

    긴 작업(과거분 5년치 등)이 도는 동안 이 연결은 쉬고 있어서 Neon 이 끊을 수 있습니다.
    connect 를 넘기면 끊긴 연결을 다시 열어 한 번 더 시도합니다.
    """

    def __init__(self, conn, connect: Callable | None = None):
        self.conn = conn
        self._connect = connect

    def _run(self, fn):
        try:
            return fn(self.conn)
        except Exception as exc:  # noqa: BLE001
            if self._connect is None or not _is_disconnect(exc, self.conn):
                raise
            try:
                self.conn.close()
            except Exception:  # noqa: BLE001
                pass
            self.conn = self._connect()
            return fn(self.conn)

    def all(self) -> dict[str, dict]:
        def q(conn):
            with conn.cursor() as cur:
                cur.execute("SELECT job, cursor, last_status, last_started, last_finished, last_complete, last_seconds, "
                            "running_since, detail FROM ops.job_state")
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
            conn.commit()
            return {r[0]: dict(zip(cols, r)) for r in rows}
        return self._run(q)

    def mark_running(self, job: str, started: datetime) -> None:
        def q(conn):
            with conn.cursor() as cur:
                cur.execute("INSERT INTO ops.job_state (job, running_since, last_started) VALUES (%s, %s, %s) "
                            "ON CONFLICT (job) DO UPDATE SET running_since = EXCLUDED.running_since, "
                            "last_started = EXCLUDED.last_started", [job, started, started])
            conn.commit()
        self._run(q)

    def finish(self, job: str, *, status: str, cursor: int, complete: bool, seconds: float, detail: str,
               finished: datetime, trigger: str, tickers: str | None, log: str, started: datetime) -> None:
        def q(conn):
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ops.job_state SET cursor = %s, last_status = %s, last_finished = %s, "
                    "last_complete = CASE WHEN %s THEN %s ELSE last_complete END, last_seconds = %s, "
                    "running_since = NULL, detail = %s WHERE job = %s",
                    [cursor, status, finished, complete, finished, round(seconds, 1), detail, job])
                cur.execute(
                    "INSERT INTO ops.job_run (job, trigger, tickers, started_at, finished_at, status, seconds, log) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    [job, trigger, tickers, started, finished, status, round(seconds, 1), log[-LOG_LIMIT:]])
            conn.commit()
        self._run(q)
        try:  # 품질 기록(sql/017)은 실패해도 수집 기록·다음 작업에 영향을 주지 않게
            self._run(lambda conn: quality.record_job(conn, job, status=status, seconds=seconds, detail=detail,
                                                      log=log, finished=finished))
            self._run(lambda conn: quality.resolve_stale(conn, JOB_BY_NAME))
        except Exception:  # noqa: BLE001
            try:
                self.conn.rollback()
            except Exception:  # noqa: BLE001
                pass

    def reset(self, job: str) -> None:
        def q(conn):
            with conn.cursor() as cur:
                cur.execute("UPDATE ops.job_state SET cursor = 0, last_complete = NULL, running_since = NULL "
                            "WHERE job = %s", [job])
            conn.commit()
        self._run(q)

    def prune(self, days: int = 14) -> None:
        def q(conn):
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ops.job_run WHERE started_at < now() - make_interval(days => %s)", [days])
            conn.commit()
        self._run(q)

    def recent_runs(self, limit: int = 60) -> list[dict]:
        def q(conn):
            with conn.cursor() as cur:
                cur.execute("SELECT run_id, job, trigger, tickers, started_at, finished_at, status, seconds, log "
                            "FROM ops.job_run ORDER BY started_at DESC LIMIT %s", [limit])
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
            conn.commit()
            return [dict(zip(cols, r)) for r in rows]
        return self._run(q)

    def try_lock(self, key: str = "kic_jobs_tick") -> bool:
        # Neon 풀러(-pooler) 주소는 트랜잭션 단위로 연결을 바꿔 써서 세션 잠금이 보장되지 않습니다.
        # 이때는 잠금을 생략하고 GitHub Actions 의 concurrency 설정(동시 실행 1개)에 맡깁니다.
        # 병렬 실행 줄은 key 를 줄마다 달리 줘서 서로 막지 않습니다 (한 작업은 한 줄에만 있음).
        if "-pooler" in (os.environ.get("DATABASE_URL") or ""):
            return True
        with self.conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", [key])
            ok = cur.fetchone()[0]
        self.conn.commit()
        self._lock_key = key
        return bool(ok)

    def unlock(self) -> None:
        if "-pooler" in (os.environ.get("DATABASE_URL") or ""):
            return
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", [getattr(self, "_lock_key", "kic_jobs_tick")])
            self.conn.commit()
        except Exception:  # noqa: BLE001  연결이 끊겼으면 잠금도 이미 풀림
            pass


def reset_backfills(conn, names=PER_TICKER_BACKFILLS) -> None:
    """새 종목이 들어왔을 때 종목별 과거분 작업을 처음부터 다시 하게 합니다 (이미 받은 행은 upsert 로 덮어씀)."""
    with conn.cursor() as cur:
        cur.execute("UPDATE ops.job_state SET cursor = 0, last_complete = NULL WHERE job = ANY(%s)", [list(names)])
    conn.commit()


def _is_disconnect(exc: Exception, conn) -> bool:
    """연결이 끊겨서 난 오류인지 (SQL 오류는 다시 시도하지 않음)."""
    try:
        import psycopg
    except ImportError:
        return False
    if isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)):
        return True
    return bool(getattr(conn, "closed", False) or getattr(conn, "broken", False))


def ensure_schema(conn) -> bool:
    """ops 테이블이 없으면 sql/*.sql 전체를 적용합니다 (처음 배포 직후 첫 tick). 적용했으면 True."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('ops.job_state') IS NOT NULL")
        exists = cur.fetchone()[0]
    conn.commit()
    if exists:
        return False
    db.apply_schema(conn)
    return True


# ---------------------------------------------------------------- 판단

KST = timezone(timedelta(hours=9))


def last_cutoff(after: dtime, now: datetime) -> datetime:
    """now 이전(포함) 가장 최근의 '한국시간 after' 시각 (UTC aware)."""
    local = now.astimezone(KST)
    cut = datetime.combine(local.date(), after, tzinfo=KST)
    if cut > local:
        cut -= DAY
    return cut.astimezone(timezone.utc)


def due_reason(job: Job, st: dict | None, now: datetime, missing: list[str]) -> str | None:
    """실행할 차례면 이유 문자열, 아니면 None."""
    st = st or {}
    running = st.get("running_since")
    if running and now - running < STALE_RUNNING:
        return None  # 다른 tick 이 실행 중
    last_status, finished = st.get("last_status"), st.get("last_finished")
    if missing:
        if last_status == "skipped" and finished and now - finished < RETRY_AFTER_SKIP:
            return None
        return "키 확인"
    if running:  # 시간 초과로 끊긴 것으로 보고 다시
        return "이전 실행이 끊김"
    if last_status == "failed" and finished and now - finished < RETRY_AFTER_FAIL:
        return None
    if st.get("cursor"):
        return "이어서"
    complete = st.get("last_complete")
    if job.every is ONCE and last_status == "warn" and not st.get("cursor"):
        # 1회 작업이 일부 실패로 끝났으면 완료로 보지 않고 6시간 뒤 다시 (예: ETF 활용신청 승인 전 403)
        return "일부 실패 재시도" if not finished or now - finished >= RETRY_ONCE_WARN else None
    if complete is None:
        return "처음"
    if job.every is ONCE:
        return None
    if job.after_kst is not None:  # 그날 기준 시각 이후 아직 끝내지 않았으면
        return "기준 시각 이후" if complete < last_cutoff(job.after_kst, now) else None
    return "주기" if now - complete >= job.every - timedelta(minutes=5) else None


def next_due(job: Job, st: dict | None) -> datetime | None:
    st = st or {}
    if st.get("cursor") or not st.get("last_complete"):
        return None  # 바로
    if job.every is ONCE:
        return None
    if job.after_kst is not None:
        return last_cutoff(job.after_kst, st["last_complete"]) + DAY
    return st["last_complete"] + job.every


def ticker_universe(conn, markets: tuple[str, ...]) -> list[tuple[str, str]]:
    token = db.ONLY_TICKERS.set(None)
    try:
        return [(m, r[1]) for m in markets for r in db.list_companies(conn, m)]
    finally:
        db.ONLY_TICKERS.reset(token)


# ---------------------------------------------------------------- 실행

def _prepare_runtime() -> None:
    """서버리스 환경은 /tmp 만 쓸 수 있어 yfinance 캐시 위치를 옮깁니다."""
    os.environ.setdefault("XDG_CACHE_HOME", "/tmp/.cache")
    try:
        import yfinance as yf

        yf.set_tz_cache_location("/tmp/.cache/yfinance")
    except Exception:  # yfinance 미설치 또는 구버전
        pass


def run_job(job: Job, state, *, conn=None, trigger: str = "tick", commands=None, getenv=None,
            universe: list[tuple[str, str]] | None = None) -> dict:
    """작업 하나(나눠 처리하는 작업이면 한 묶음)를 실행하고 결과를 기록합니다."""
    from . import cli

    commands = commands or cli
    getenv = getenv or (lambda k: os.environ.get(k))
    started = _now()
    t0 = time.monotonic()
    st = state.all().get(job.name) or {}
    missing = job.missing_env(getenv)
    if missing:
        detail = "키 없음: " + ", ".join(missing)
        state.mark_running(job.name, started)
        state.finish(job.name, status="skipped", cursor=st.get("cursor") or 0, complete=False, seconds=0.0,
                     detail=detail, finished=_now(), trigger=trigger, tickers=None, log=detail, started=started)
        return {"job": job.name, "status": "skipped", "detail": detail}

    cursor = st.get("cursor") or 0
    chunk_keys, next_cursor, complete = None, 0, True
    if job.split:
        if universe is not None:
            keys = universe
        elif hasattr(state, "_run"):  # 끊긴 연결이면 다시 열어서 읽기
            def q(c):
                out = ticker_universe(c, job.split)
                c.commit()  # 읽기 트랜잭션을 열어 둔 채로 긴 작업에 들어가지 않게
                return out
            keys = state._run(q)
        else:
            keys = ticker_universe(conn, job.split)
        if cursor >= len(keys):
            cursor = 0
        chunk_keys = keys[cursor:cursor + job.chunk]
        next_cursor = cursor + job.chunk
        complete = next_cursor >= len(keys)
        if complete:
            next_cursor = 0
    tickers = None if chunk_keys is None else ",".join(f"{m}:{t}" for m, t in chunk_keys)

    state.mark_running(job.name, started)
    buf = io.StringIO()
    token = db.ONLY_TICKERS.set(None if chunk_keys is None else frozenset(chunk_keys))
    status = "ok"
    try:
        _prepare_runtime()
        fn = getattr(commands, job.command)
        with redirect_stdout(buf), redirect_stderr(buf):
            rc = fn(job.namespace())
        if rc == MORE:
            next_cursor, complete = cursor, False  # 남은 일은 다음 실행에서
        elif rc not in (0, None):
            status = "warn"  # 일부 종목·시리즈만 실패: 로그 확인. 반복 실행으로 막히지 않게 진행은 계속
    except Exception as exc:  # noqa: BLE001  작업 하나가 실패해도 다음 작업은 실행
        status = "failed"
        buf.write(f"\n[FAIL] {type(exc).__name__}: {exc}\n")
        buf.write("".join(traceback.format_exception(exc)[-6:]))
        next_cursor, complete = cursor, False  # 같은 묶음을 나중에 다시
    finally:
        db.ONLY_TICKERS.reset(token)
    seconds = time.monotonic() - t0
    if status != "failed" and not complete:
        status = "partial" if status == "ok" else status
    log = buf.getvalue()
    detail = _summary(log, status, tickers, complete)
    state.finish(job.name, status=status, cursor=next_cursor, complete=complete and status != "failed",
                 seconds=seconds, detail=detail, finished=_now(), trigger=trigger, tickers=tickers, log=log,
                 started=started)
    return {"job": job.name, "status": status, "seconds": round(seconds, 1), "tickers": tickers, "detail": detail,
            "more": status != "failed" and not complete}


def _summary(log: str, status: str, tickers: str | None, complete: bool) -> str:
    lines = [ln for ln in log.splitlines() if ln.strip()]
    fails = [ln for ln in lines if ln.startswith("[FAIL]")]
    oks = sum(ln.startswith("[OK]") for ln in lines)
    parts = [f"OK {oks}"]
    if fails:
        parts.append(f"FAIL {len(fails)}: {fails[0][7:120]}")
    if tickers and not complete:
        parts.append("다음 묶음 남음")
    return " · ".join(parts)


MORE = 3  # 명령이 이 값을 돌려주면 '아직 남음': 다음 실행에서 이어서 (예: cmd_dart_multi 시간 예산)
BACKFILL_SHARE = 0.5  # 과거분이 예산을 다 쓰면 매시간 시세·공시가 밀리므로 절반까지만


def tick(state, *, conn=None, budget: float | None = None, hard_limit: float = 280.0, only: str | None = None,
         lane: str | None = None, commands=None, getenv=None, clock=time.monotonic, universe_for=None) -> dict:
    """차례가 된 작업을 시간 예산 안에서 순서대로 실행합니다. lane 을 주면 그 줄의 작업만."""
    getenv = getenv or (lambda k: os.environ.get(k))
    budget = budget if budget is not None else float(getenv("TICK_BUDGET_SECONDS") or 240)
    t0 = clock()
    ran, waiting = [], []
    states = state.all()
    for job in JOBS:
        if only and job.group != only and job.name != only:
            continue
        if lane and lane_of(job) != lane:
            continue
        st = states.get(job.name)
        reason = due_reason(job, st, _now(), job.missing_env(getenv))
        if reason is None:
            continue
        elapsed = clock() - t0
        expected = float((st or {}).get("last_seconds") or 20) * 1.2
        limit = budget * BACKFILL_SHARE if job.group.startswith("과거분") else budget
        if elapsed > limit or elapsed + expected > hard_limit:
            waiting.append(job.name)
            continue
        universe = universe_for(job) if universe_for else None
        while True:  # 나눠 처리하는 작업은 시간이 남는 동안 다음 묶음도 이어서
            res = run_job(job, state, conn=conn, commands=commands, getenv=getenv, universe=universe)
            res["reason"] = reason
            ran.append(res)
            if not res.get("more"):
                break
            elapsed = clock() - t0
            if elapsed + max(res.get("seconds") or 0, 5) * 1.2 > min(limit, hard_limit):
                waiting.append(job.name)
                break
            reason = "이어서"
        if job.name == "sync-kospi200":  # 새 종목이 들어오면 과거분 작업을 되돌리므로 같은 실행에서 바로 반영
            states = state.all()
    return {"ran": ran, "waiting": waiting, "seconds": round(clock() - t0, 1)}


def overview(state, getenv=None) -> list[dict]:
    """대시보드용: 작업별 상태 + 다음 예정 시각."""
    getenv = getenv or (lambda k: os.environ.get(k))
    states = state.all()
    now = _now()
    out = []
    for job in JOBS:
        st = states.get(job.name) or {}
        missing = job.missing_env(getenv)
        out.append({
            "name": job.name, "label": job.label, "group": job.group,
            "every": "1회" if job.every is ONCE else {EVERY_RUN: "매 실행", HOUR: "매시간", SIX_HOURS: "6시간", DAY: "매일", WEEK: "매주"}.get(job.every, str(job.every)),
            "status": st.get("last_status"), "detail": st.get("detail"),
            "last_finished": st.get("last_finished"), "last_complete": st.get("last_complete"),
            "seconds": None if st.get("last_seconds") is None else float(st["last_seconds"]),
            "running": bool(st.get("running_since") and now - st["running_since"] < STALE_RUNNING),
            "cursor": st.get("cursor") or 0, "missing": missing,
            "due": due_reason(job, st, now, missing) is not None, "next": next_due(job, st),
        })
    return out


STATUS_KO = {"ok": "정상", "warn": "일부 실패", "failed": "실패", "skipped": "건너뜀", "partial": "진행 중"}


def markdown_summary(result: dict, ov: list[dict]) -> str:
    """GitHub Actions 실행 요약($GITHUB_STEP_SUMMARY)용 표."""
    from zoneinfo import ZoneInfo

    kst = ZoneInfo("Asia/Seoul")
    fmt = lambda ts: ts.astimezone(kst).strftime("%m-%d %H:%M") if ts else "-"  # noqa: E731
    lines = [f"### 이번 실행: {len(result['ran'])}건, {result['seconds']}초", "",
             "| 작업 | 결과 | 초 | 내용 |", "|---|---|---|---|"]
    for r in result["ran"]:
        lines.append(f"| {r['job']} | {STATUS_KO.get(r['status'], r['status'])} | {r.get('seconds') or 0} | "
                     f"{(r.get('detail') or '').replace('|', '/')[:120]} |")
    if result["waiting"]:
        lines += ["", "다음 실행으로 넘긴 작업: " + ", ".join(result["waiting"])]
    lines += ["", "### 전체 작업 상태", "", "| 묶음 | 작업 | 주기 | 상태 | 마지막 완료(KST) | 비고 |", "|---|---|---|---|---|---|"]
    for j in ov:
        note = ("필요: " + ", ".join(j["missing"])) if j["missing"] else (j["detail"] or "")
        lines.append(f"| {j['group']} | {j['label']} | {j['every']} | {STATUS_KO.get(j['status'], j['status'] or '대기')} | "
                     f"{fmt(j['last_complete'])} | {note.replace('|', '/')[:100]} |")
    return "\n".join(lines) + "\n"
