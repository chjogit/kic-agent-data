-- 025: 회사채 발행 종목 (금융위원회_채권발행정보, 작업 bond-issue). 출처표시·상업적 이용금지(공공누리 2유형).
-- 사채·CP 미상환 잔액(core.debt_balance)이 비는 회사의 차입 조건(표면이율·고정/변동·만기)을 보충합니다.
ALTER TABLE core.company ADD COLUMN IF NOT EXISTS crno text;   -- 법인등록번호 (DART 기업개황 jurir_no)

CREATE TABLE IF NOT EXISTS core.bond_issue_fsc (
    company_id    bigint NOT NULL REFERENCES core.company (company_id),
    isin          text   NOT NULL,          -- 채권 종목코드
    bond_name     text,
    issuer        text,
    issue_date    date,
    maturity_date date,
    issue_amount  numeric,                  -- 원천 표기 그대로 (단위는 첫 응답으로 확인 후 문서화)
    currency      text,
    coupon_rate   numeric,                  -- 표면이율 (%)
    rate_type     text,                     -- 금리구분 원문
    rate_kind     text,                     -- fixed / floating / NULL(모름)
    bond_type     text,
    bas_dt        date,                     -- 원천 기준일자
    raw           jsonb,                    -- 원 응답 한 행 (필드 해석을 바꿀 때 다시 씀)
    collected_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, isin)
);

-- 회사별 요약: 만기 전 종목의 변동금리 비중(발행금액 기준)과 가중 표면이율.
CREATE OR REPLACE VIEW core.v_bond_issue_summary AS
SELECT c.market, c.ticker, c.name,
       count(*) AS n_bonds,
       sum(b.issue_amount) AS outstanding_amount,
       round(sum(b.issue_amount) FILTER (WHERE b.rate_kind = 'floating') / NULLIF(sum(b.issue_amount), 0), 4) AS floating_share,
       round(sum(b.issue_amount) FILTER (WHERE b.rate_kind IS NULL) / NULLIF(sum(b.issue_amount), 0), 4) AS unknown_share,
       round(sum(b.issue_amount * b.coupon_rate) / NULLIF(sum(b.issue_amount) FILTER (WHERE b.coupon_rate IS NOT NULL), 0), 3)
           AS wavg_coupon,
       min(b.maturity_date) AS next_maturity,
       max(b.bas_dt) AS bas_dt
FROM core.bond_issue_fsc b
JOIN core.company c USING (company_id)
WHERE b.maturity_date IS NULL OR b.maturity_date >= current_date
GROUP BY c.market, c.ticker, c.name;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kic_readonly') THEN
        GRANT SELECT ON core.bond_issue_fsc, core.v_bond_issue_summary TO kic_readonly;
    END IF;
END $$;
