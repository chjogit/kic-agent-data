-- 002: 원본 저장소와 핵심 분석 테이블
-- 모든 수집 레코드에 source, collected_at 을 남겨 검증 단계에서 출처와 시점을 추적합니다.

CREATE TABLE IF NOT EXISTS raw.api_payload (
    id          bigserial PRIMARY KEY,
    source      text        NOT NULL,
    endpoint    text        NOT NULL,
    params      jsonb,                      -- 인증키는 저장하지 않음
    payload     jsonb       NOT NULL,
    fetched_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_api_payload_source ON raw.api_payload (source, fetched_at DESC);

CREATE TABLE IF NOT EXISTS core.company (
    company_id  bigserial PRIMARY KEY,
    market      text NOT NULL CHECK (market IN ('KR', 'US')),
    ticker      text NOT NULL,              -- KR: 6자리 단축코드, US: 티커
    name        text,
    isin        text,
    corp_code   text,                       -- OpenDART 고유번호 (KR)
    cik         text,                       -- SEC CIK (US, 10자리 0 패딩)
    sector      text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (market, ticker)
);

-- Peer Group 비교용
CREATE TABLE IF NOT EXISTS core.peer_group (
    peer_group_id   bigserial PRIMARY KEY,
    base_company_id bigint NOT NULL REFERENCES core.company (company_id),
    method          text,                   -- 선정 기준 (업종, 시총, 사업구조 등)
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.peer_member (
    peer_group_id bigint NOT NULL REFERENCES core.peer_group (peer_group_id) ON DELETE CASCADE,
    company_id    bigint NOT NULL REFERENCES core.company (company_id),
    rank          int,
    reason        text,
    PRIMARY KEY (peer_group_id, company_id)
);

CREATE TABLE IF NOT EXISTS core.price_daily (
    company_id   bigint  NOT NULL REFERENCES core.company (company_id),
    trade_date   date    NOT NULL,
    open         numeric,
    high         numeric,
    low          numeric,
    close        numeric,
    volume       bigint,
    trade_value  numeric,
    market_cap   numeric,
    source       text    NOT NULL,
    collected_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, trade_date)
);

CREATE TABLE IF NOT EXISTS core.financial_fact (
    fact_id      bigserial PRIMARY KEY,
    company_id   bigint NOT NULL REFERENCES core.company (company_id),
    source       text   NOT NULL,           -- dart | sec_edgar
    fiscal_year  int    NOT NULL,
    report_code  text   NOT NULL DEFAULT '', -- DART: 11011(사업보고서) 등, US: FY
    fs_div       text   NOT NULL DEFAULT '', -- CFS(연결) | OFS(별도)
    sj_div       text   NOT NULL DEFAULT '', -- BS | IS | CIS | CF | SCE
    account_id   text   NOT NULL DEFAULT '',
    account_nm   text   NOT NULL DEFAULT '',
    amount       numeric,
    currency     text,
    rcept_no     text,                      -- 공시 접수번호 / accession number
    collected_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, source, fiscal_year, report_code, fs_div, sj_div, account_id, account_nm)
);

CREATE TABLE IF NOT EXISTS core.disclosure (
    rcept_no     text PRIMARY KEY,
    company_id   bigint REFERENCES core.company (company_id),
    report_nm    text,
    rcept_dt     date,
    filer        text,
    source       text NOT NULL,
    collected_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_disclosure_company ON core.disclosure (company_id, rcept_dt DESC);

CREATE TABLE IF NOT EXISTS core.macro_series (
    series_id text PRIMARY KEY,
    name      text,
    source    text NOT NULL,                -- ecos | fred
    frequency text,
    unit      text
);

CREATE TABLE IF NOT EXISTS core.macro_obs (
    series_id    text NOT NULL REFERENCES core.macro_series (series_id),
    obs_date     date NOT NULL,
    value        numeric,
    source       text NOT NULL,
    collected_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (series_id, obs_date)
);
