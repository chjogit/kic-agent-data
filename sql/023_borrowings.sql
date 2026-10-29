-- 총차입금 (전체 재무제표 재무상태표의 차입 계정 합). 수집: collect-debt-terms --apis borrowing
-- 행은 core.financial_fact(source='dart_borrow'), 계정 고르는 규칙은 collectors/dart_periodic.py borrow_kind.
-- 단기차입금·유동성장기차입금·유동성사채·장기차입금·사채·전환사채 등. 리스부채·사채할인발행차금은 넣지 않습니다.
-- 금액 단위: 원(전체 재무제표 API 는 원 단위). 금융업의 '차입부채'는 영업 자금이라 따로 표시(bank_borrow).
CREATE OR REPLACE VIEW core.v_borrowings AS
WITH f AS (
  SELECT company_id, fiscal_year, report_code, fs_div, period_end, rcept_no, amount,
         replace(split_part(account_nm, '#', 1), ' ', '') AS nm
  FROM core.financial_fact
  WHERE source = 'dart_borrow' AND sj_div = 'BS'
), latest AS (
  SELECT DISTINCT ON (company_id) company_id, fiscal_year, report_code, fs_div
  FROM f
  ORDER BY company_id, period_end DESC, (fs_div = 'CFS') DESC
)
SELECT c.market, c.ticker, c.name, l.fiscal_year, l.report_code, l.fs_div,
       max(f.period_end) AS period_end,
       sum(f.amount) FILTER (WHERE f.nm NOT LIKE '%차입부채%') AS total_borrowings,
       sum(f.amount) FILTER (WHERE f.nm NOT LIKE '%차입부채%' AND f.nm ~ '^(단기|유동)') AS current_borrowings,
       sum(f.amount) FILTER (WHERE f.nm NOT LIKE '%차입부채%' AND f.nm !~ '차입금') AS bonds,
       sum(f.amount) FILTER (WHERE f.nm LIKE '%차입부채%') AS bank_borrow,
       count(*) AS n_accounts,
       string_agg(f.nm, ', ' ORDER BY f.amount DESC) AS accounts,
       max(f.rcept_no) AS rcept_no
FROM latest l
JOIN f USING (company_id, fiscal_year, report_code, fs_div)
JOIN core.company c USING (company_id)
GROUP BY c.market, c.ticker, c.name, l.fiscal_year, l.report_code, l.fs_div;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kic_readonly') THEN
        GRANT SELECT ON core.v_borrowings TO kic_readonly;
    END IF;
END $$;
