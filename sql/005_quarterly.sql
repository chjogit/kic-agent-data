-- 005: 분기 재무(정규화)와 TTM 뷰
ALTER TABLE core.financial_fact ADD COLUMN IF NOT EXISTS amount_ytd numeric;  -- DART 누적값
ALTER TABLE core.financial_fact ADD COLUMN IF NOT EXISTS period_end date;     -- SEC 기간 종료일

-- 분기 단독 값 (흐름: 매출·영업이익·순이익 / 잔액: 자산·부채·자본)
CREATE TABLE IF NOT EXISTS core.financial_quarter (
    company_id  bigint  NOT NULL REFERENCES core.company (company_id),
    period_end  date    NOT NULL,
    fiscal_year int     NOT NULL,
    fiscal_q    int     NOT NULL CHECK (fiscal_q BETWEEN 1 AND 4),
    metric      text    NOT NULL,
    amount      numeric,
    currency    text,
    source      text    NOT NULL,
    PRIMARY KEY (company_id, period_end, metric)
);

CREATE OR REPLACE VIEW core.v_ttm AS
WITH q AS (
  SELECT *, row_number() OVER (PARTITION BY company_id, metric ORDER BY period_end DESC) AS rn
  FROM core.financial_quarter
), flow AS (
  SELECT company_id, metric, sum(amount) AS ttm, max(period_end) AS last_end, max(currency) AS currency
  FROM q
  WHERE rn <= 4 AND metric IN ('revenue','operating_income','net_income')
  GROUP BY company_id, metric
  HAVING count(*) = 4 AND max(period_end) - min(period_end) <= 290   -- 연속한 4개 분기만
), t AS (
  SELECT company_id, max(last_end) AS ttm_end, max(currency) AS currency,
    max(ttm) FILTER (WHERE metric = 'revenue')          AS revenue_ttm,
    max(ttm) FILTER (WHERE metric = 'operating_income') AS operating_income_ttm,
    max(ttm) FILTER (WHERE metric = 'net_income')       AS net_income_ttm
  FROM flow GROUP BY company_id
), bs AS (
  SELECT company_id, max(period_end) AS bs_end,
    max(amount) FILTER (WHERE metric = 'assets')      AS assets,
    max(amount) FILTER (WHERE metric = 'liabilities') AS liabilities,
    max(amount) FILTER (WHERE metric = 'equity')      AS equity
  FROM q WHERE rn = 1 AND metric IN ('assets','liabilities','equity') GROUP BY company_id
), px AS (
  SELECT DISTINCT ON (company_id) company_id, close
  FROM core.price_daily ORDER BY company_id, trade_date DESC
)
SELECT c.market, c.ticker, c.name, t.ttm_end, bs.bs_end,
       t.revenue_ttm, t.operating_income_ttm, t.net_income_ttm, bs.assets, bs.liabilities, bs.equity,
       CASE WHEN t.net_income_ttm > 0 AND t.currency IN ('USD','KRW')
            THEN round(px.close * c.shares_outstanding / t.net_income_ttm, 2) END AS per_ttm,
       CASE WHEN bs.equity > 0 AND t.currency IN ('USD','KRW')
            THEN round(px.close * c.shares_outstanding / bs.equity, 2) END        AS pbr,
       round(t.operating_income_ttm / NULLIF(t.revenue_ttm, 0), 4)                AS op_margin_ttm,
       round(t.net_income_ttm / NULLIF(bs.equity, 0), 4)                          AS roe_ttm,
       round(bs.liabilities / NULLIF(bs.equity, 0), 4)                            AS debt_ratio,
       t.currency
FROM core.company c
JOIN t USING (company_id)
LEFT JOIN bs USING (company_id)
LEFT JOIN px USING (company_id);
