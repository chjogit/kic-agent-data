-- 023: 연결재무제표(CFS)가 없는 회사(종속회사 없음: 리노공업·카카오뱅크 등)는 별도재무제표(OFS)로 연간 지표 계산.
-- 004 의 v_fundamentals_annual 과 같고 fs_div 조건만 바뀜 (열 순서 동일이라 v_valuation 은 그대로 따라감).

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
    AND ((f.source = 'dart' AND f.sj_div IN ('BS', 'IS', 'CIS')
          AND (f.fs_div = 'CFS' OR NOT EXISTS (   -- 종속회사가 없어 연결이 없으면 별도(OFS) 사용
                SELECT 1 FROM core.financial_fact x WHERE x.company_id = f.company_id AND x.source = 'dart'
                  AND x.fiscal_year = f.fiscal_year AND x.report_code = f.report_code AND x.fs_div = 'CFS')))
       OR f.source = 'sec_edgar')
  GROUP BY f.company_id, f.fiscal_year
) t;
