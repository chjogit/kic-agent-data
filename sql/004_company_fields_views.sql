-- 004: 업종·발행주식수 컬럼, 공통 재무 뷰, 밸류에이션 뷰
ALTER TABLE core.company ADD COLUMN IF NOT EXISTS sector_desc        text;
ALTER TABLE core.company ADD COLUMN IF NOT EXISTS shares_outstanding numeric;
ALTER TABLE core.company ADD COLUMN IF NOT EXISTS shares_asof        date;

-- DART(연결) 와 SEC(10-K) 계정을 공통 항목으로 매핑 (계정 ID 는 첫 수집 후 검증 필요)
CREATE OR REPLACE VIEW core.v_fundamentals_annual AS
SELECT t.company_id, t.fiscal_year, t.revenue, t.operating_income, t.net_income, t.assets,
       COALESCE(t.liabilities, t.assets - t.equity) AS liabilities,   -- 총부채 태그가 없으면 자산 − 자본
       t.equity, t.currency
FROM (
  SELECT f.company_id, f.fiscal_year,
    max(f.amount) FILTER (WHERE f.account_id IN ('ifrs-full_Revenue','Revenues','RevenueFromContractWithCustomerExcludingAssessedTax')) AS revenue,
    max(f.amount) FILTER (WHERE f.account_id IN ('dart_OperatingIncomeLoss','OperatingIncomeLoss','ifrs-full_ProfitLossFromOperatingActivities'))                                      AS operating_income,
    COALESCE(max(f.amount) FILTER (WHERE f.account_id IN ('ifrs-full_ProfitLoss','NetIncomeLoss')),
             max(f.amount) FILTER (WHERE f.account_id = 'ProfitLoss'))                                                                   AS net_income,
    max(f.amount) FILTER (WHERE f.account_id IN ('ifrs-full_Assets','Assets'))                                                           AS assets,
    max(f.amount) FILTER (WHERE f.account_id IN ('ifrs-full_Liabilities','Liabilities'))                                                 AS liabilities,
    COALESCE(max(f.amount) FILTER (WHERE f.account_id IN ('ifrs-full_Equity','StockholdersEquity')),
             max(f.amount) FILTER (WHERE f.account_id = 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'))        AS equity,
    max(f.currency) AS currency
  FROM core.financial_fact f
  WHERE f.report_code IN ('11011', 'FY')   -- 연간 보고서만 (분기 값 혼입 방지)
    AND ((f.source = 'dart' AND f.fs_div = 'CFS' AND f.sj_div IN ('BS', 'IS', 'CIS')) OR f.source = 'sec_edgar')
  GROUP BY f.company_id, f.fiscal_year
) t;

CREATE OR REPLACE VIEW core.v_valuation AS
WITH last_px AS (
  SELECT DISTINCT ON (company_id) company_id, trade_date, close
  FROM core.price_daily ORDER BY company_id, trade_date DESC
), last_fy AS (
  SELECT DISTINCT ON (company_id) *
  FROM core.v_fundamentals_annual WHERE net_income IS NOT NULL
  ORDER BY company_id, fiscal_year DESC
)
SELECT c.market, c.ticker, c.name, c.sector, c.sector_desc,
       p.trade_date AS price_date, p.close,
       c.shares_outstanding,
       p.close * c.shares_outstanding AS market_cap,
       y.fiscal_year, y.revenue, y.operating_income, y.net_income, y.equity,
       CASE WHEN y.net_income > 0 AND y.currency IN ('USD','KRW') THEN round(p.close * c.shares_outstanding / y.net_income, 2) END AS per,  -- 적자면 PER 비움
       CASE WHEN y.equity > 0 AND y.currency IN ('USD','KRW') THEN round(p.close * c.shares_outstanding / y.equity, 2) END AS pbr,
       round(y.operating_income / NULLIF(y.revenue, 0), 4)                AS op_margin,
       round(y.net_income / NULLIF(y.equity, 0), 4)                       AS roe,
       round(y.liabilities / NULLIF(y.equity, 0), 4)                      AS debt_ratio,
       y.currency                                                         AS fin_currency  -- 시세와 통화가 다르면 PER/PBR 은 비움
FROM core.company c
JOIN last_px p USING (company_id)
LEFT JOIN last_fy y USING (company_id);
