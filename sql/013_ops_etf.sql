-- 013: 웹 자동 실행기(Vercel) 작업 기록, ETF 시세

CREATE SCHEMA IF NOT EXISTS ops;

-- 작업별 현재 상태 (다음 실행 시점 판단용). cursor 는 종목을 나눠 처리하는 작업의 진행 위치입니다.
CREATE TABLE IF NOT EXISTS ops.job_state (
    job            text PRIMARY KEY,
    cursor         int         NOT NULL DEFAULT 0,
    last_status    text,                       -- ok / warn / failed / skipped / partial
    last_started   timestamptz,
    last_finished  timestamptz,
    last_complete  timestamptz,                -- 한 바퀴(전체 종목)를 끝낸 시각
    last_seconds   numeric,
    running_since  timestamptz,                -- 실행 중이면 시작 시각 (함수가 시간 초과로 끊기면 남음)
    detail         text
);

-- 실행 이력 (14일 보관)
CREATE TABLE IF NOT EXISTS ops.job_run (
    run_id      bigserial PRIMARY KEY,
    job         text        NOT NULL,
    trigger     text,                          -- tick / manual
    tickers     text,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status      text,
    seconds     numeric,
    log         text
);
CREATE INDEX IF NOT EXISTS ix_job_run_started ON ops.job_run (started_at DESC);
CREATE INDEX IF NOT EXISTS ix_job_run_job ON ops.job_run (job, started_at DESC);

-- 국내 ETF 일별 시세 (금융위원회 증권상품시세정보). 종목 테이블과 분리해 재무·공시 수집 대상에서 빠지게 합니다.
CREATE TABLE IF NOT EXISTS core.etf_price_daily (
    ticker       text    NOT NULL,
    trade_date   date    NOT NULL,
    name         text,
    close        numeric,
    nav          numeric,                      -- 순자산가치 → 괴리율 = close / nav - 1
    volume       bigint,
    trade_value  numeric,
    market_cap   numeric,
    net_assets   numeric,                      -- 순자산총액
    base_index   text,                         -- 기초지수명
    source       text    NOT NULL,
    collected_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, trade_date)
);
