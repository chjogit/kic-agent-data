-- 030: 지분공시 정형화 (DART 주식등의 대량보유상황보고 · 최대주주 현황, 작업 ownership). 추가만, 기존 표 불변.
-- 원천 응답 한 행은 raw 에 그대로 두어, 필드 해석을 바꿔도 다시 받지 않고 고칠 수 있게 합니다.
CREATE TABLE IF NOT EXISTS core.ownership_report (
    company_id   bigint NOT NULL REFERENCES core.company (company_id),
    rcept_no     text   NOT NULL,           -- 접수번호 (core.disclosure.rcept_no 와 같음)
    repror       text   NOT NULL DEFAULT '',-- 보고자
    rcept_dt     date,                      -- 보고(접수)일
    report_tp    text,                      -- 보고 구분 (신규/변경 등 원문)
    report_resn  text,                      -- 보고 사유 (변동 사유)
    stkqy        numeric,                   -- 보유 주식 수
    stkrt        numeric,                   -- 보유 비율 (%)
    stkrt_irds   numeric,                   -- 직전 보고 대비 비율 증감 (%p)
    ctr_stkrt    numeric,                   -- 주요 계약 대상 포함 보유 비율 (%)
    raw          jsonb,
    collected_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, rcept_no, repror)
);
CREATE INDEX IF NOT EXISTS ix_ownership_report_dt ON core.ownership_report (company_id, rcept_dt DESC);

CREATE TABLE IF NOT EXISTS core.largest_holder (
    company_id   bigint NOT NULL REFERENCES core.company (company_id),
    fiscal_year  int    NOT NULL,
    report_code  text   NOT NULL,           -- 11011 사업보고서
    holder       text   NOT NULL,           -- 최대주주 및 특수관계인 성명
    stock_kind   text   NOT NULL DEFAULT '',-- 주식 종류 (보통주/우선주)
    relate       text,                      -- 관계
    end_qty      numeric,                   -- 기말 보유 주식 수
    stake_rt     numeric,                   -- 기말 지분율 (%)
    begin_rt     numeric,                   -- 기초 지분율 (%)
    settle_dt    date,
    rcept_no     text,
    raw          jsonb,
    collected_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, fiscal_year, report_code, holder, stock_kind)
);

-- 회사·연도별 최대주주 측 합계 지분율 (보통주 기준, 읽지 못한 값은 합계에서 제외)
CREATE OR REPLACE VIEW core.v_largest_holder_total AS
SELECT c.market, c.ticker, c.name, h.fiscal_year,
       sum(h.stake_rt) FILTER (WHERE h.stock_kind LIKE '%보통%') AS common_stake_rt,
       count(*) AS n_holders, max(h.settle_dt) AS settle_dt
FROM core.largest_holder h JOIN core.company c USING (company_id)
GROUP BY c.market, c.ticker, c.name, h.fiscal_year;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kic_readonly') THEN
        GRANT SELECT ON core.ownership_report, core.largest_holder, core.v_largest_holder_total TO kic_readonly;
    END IF;
END $$;
