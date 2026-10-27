-- 019: 차입 조건(OpenDART 정기보고서 주요정보) + 매출채권 회수기간(DSO)
-- 작업 debt-terms (kic_data/collectors/dart_periodic.py). A1 거시 에이전트의 '차입 조건·만기 구조', '매출채권 회수 기간' 전제용.

-- 회사채·CP·단기사채 미상환 잔액 (만기 구간별). 금액 단위는 원문 표를 따르며 API 가 알려 주지 않아 amount_raw 로 둡니다.
CREATE TABLE IF NOT EXISTS core.debt_balance (
    company_id  bigint NOT NULL REFERENCES core.company (company_id),
    fiscal_year int    NOT NULL,
    reprt_code  text   NOT NULL,          -- 11011 사업 · 11012 반기 · 11013 1분기 · 11014 3분기
    kind        text   NOT NULL,          -- bond 회사채 · cp 기업어음 · stb 단기사채
    label       text   NOT NULL,          -- 원문 행 이름 (예: '공모|합계', '사모', '합계')
    bucket      text   NOT NULL,          -- 1y · 1-2y · … · 10d · 10-30d · … · total · limit · limit_left
    amount_raw  numeric,
    stlm_dt     date,                     -- 결산기준일
    rcept_no    text,
    collected_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, fiscal_year, reprt_code, kind, label, bucket)
);

-- 채무증권 발행실적 (발행 건별 이자율·만기·신용등급)
CREATE TABLE IF NOT EXISTS core.debt_issue (
    company_id      bigint NOT NULL REFERENCES core.company (company_id),
    fiscal_year     int    NOT NULL,
    reprt_code      text   NOT NULL,
    seq             int    NOT NULL,      -- 응답 순서
    issuer          text,
    kind_nm         text,                 -- 증권종류 (회사채, 기업어음 등)
    method          text,                 -- 공모 / 사모
    issue_date      date,
    face_amount_raw numeric,              -- 권면총액 (원문 단위)
    rate            numeric,              -- 이자율 (%)
    rating          text,                 -- 평가등급(평가기관)
    maturity_date   date,
    repaid          text,                 -- 상환여부 (원문)
    underwriter     text,
    stlm_dt         date,
    rcept_no        text,
    collected_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, fiscal_year, reprt_code, seq)
);

-- 회사·API·보고서별로 이미 받았는지 (자료 없음도 기록해 같은 보고서를 다시 부르지 않음)
CREATE TABLE IF NOT EXISTS ops.dart_periodic_fetch (
    company_id  bigint NOT NULL REFERENCES core.company (company_id),
    api         text   NOT NULL,          -- bond · cp · stb · issue · receivable
    fiscal_year int    NOT NULL,
    reprt_code  text   NOT NULL,
    status      text   NOT NULL,          -- ok · no_data
    n_rows      int,
    fetched_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, api, fiscal_year, reprt_code)
);

-- 만기 구조: 종류별 합계 행에서 1년 안에 만기가 오는 비중 (단위와 무관한 비율)
CREATE OR REPLACE VIEW core.v_debt_maturity AS
WITH latest AS (
  SELECT DISTINCT ON (company_id, kind) company_id, kind, fiscal_year, reprt_code, label
  FROM core.debt_balance
  WHERE bucket = 'total' AND amount_raw > 0
  ORDER BY company_id, kind, fiscal_year DESC,
           array_position(ARRAY['11013','11012','11014','11011'], reprt_code) DESC NULLS LAST,  -- 같은 해면 사업보고서(연말)가 가장 최근
           (label ~ '합계') DESC, amount_raw DESC
)
SELECT c.market, c.ticker, c.name, l.kind, l.fiscal_year, l.reprt_code, l.label,
       max(b.stlm_dt) AS stlm_dt,
       max(b.amount_raw) FILTER (WHERE b.bucket = 'total') AS total_raw,
       sum(b.amount_raw) FILTER (WHERE b.bucket IN ('1y','10d','10-30d','30-90d','90-180d','180d-1y')) AS within_1y_raw,
       round(sum(b.amount_raw) FILTER (WHERE b.bucket IN ('1y','10d','10-30d','30-90d','90-180d','180d-1y'))
             / NULLIF(max(b.amount_raw) FILTER (WHERE b.bucket = 'total'), 0), 4) AS within_1y_share,
       max(b.rcept_no) AS rcept_no
FROM latest l
JOIN core.debt_balance b USING (company_id, kind, fiscal_year, reprt_code, label)
JOIN core.company c USING (company_id)
GROUP BY c.market, c.ticker, c.name, l.kind, l.fiscal_year, l.reprt_code, l.label;

-- 매출채권 회수기간 = 매출채권(기말) / 그 기말까지 4개 분기 매출 × 365.
-- 매출채권은 전체 재무제표(source='dart_full')에서, 매출은 분기 단독값(core.financial_quarter)에서 가져옵니다.
CREATE OR REPLACE VIEW core.v_dso AS
WITH r AS (
  SELECT DISTINCT ON (company_id, fiscal_year, report_code)
         company_id, fiscal_year, report_code, fs_div, account_nm, amount, period_end, rcept_no
  FROM core.financial_fact
  WHERE source = 'dart_full' AND sj_div = 'BS' AND period_end IS NOT NULL
  ORDER BY company_id, fiscal_year, report_code, (fs_div = 'CFS') DESC
), rev AS (
  SELECT r.company_id, r.period_end, sum(q.amount) AS revenue_4q, count(*) AS n_q
  FROM r JOIN core.financial_quarter q
    ON q.company_id = r.company_id AND q.metric = 'revenue'
   AND q.period_end <= r.period_end AND q.period_end > r.period_end - 360
  GROUP BY r.company_id, r.period_end
)
SELECT c.market, c.ticker, c.name, r.fiscal_year, r.report_code, r.period_end, r.fs_div,
       r.account_nm AS receivable_account, r.amount AS receivables, rev.revenue_4q,
       CASE WHEN rev.n_q = 4 AND rev.revenue_4q > 0 THEN round(r.amount / rev.revenue_4q * 365, 1) END AS dso_days,
       r.rcept_no
FROM r
JOIN core.company c USING (company_id)
LEFT JOIN rev ON rev.company_id = r.company_id AND rev.period_end = r.period_end;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kic_readonly') THEN
        GRANT SELECT ON core.debt_balance, core.debt_issue, ops.dart_periodic_fetch,
                        core.v_debt_maturity, core.v_dso TO kic_readonly;
    END IF;
END $$;
