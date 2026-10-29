-- 총차입금 (전체 재무제표 재무상태표의 차입 계정 합). 수집: collect-debt-terms --apis borrowing
-- 행은 core.financial_fact(source='dart_borrow'), 계정 고르는 규칙은 collectors/dart_periodic.py borrow_kind.
-- 단기차입금·유동성장기차입금·유동성사채·장기차입금·사채·전환사채 등. 리스부채·사채할인발행차금은 넣지 않습니다.
-- 유동·비유동 나눔은 회사마다 계정 이름이 섞여(예: '차입금및사채') 뷰에서는 합만 냅니다. 계정 목록은 accounts.
-- 금액 단위: 원(전체 재무제표 API 는 원 단위). 금융업의 '차입부채'는 영업 자금이라 따로 표시(bank_borrow).
-- 기준 보고서는 받은 기록(ops.dart_periodic_fetch, api='borrowing')의 가장 최근 보고서입니다. 그 보고서에 차입 계정이
-- 없으면(무차입) total_borrowings = 0 이고, 예전 보고서 값으로 넘어가지 않습니다.
CREATE OR REPLACE VIEW core.v_borrowings AS
WITH latest AS (
  SELECT DISTINCT ON (company_id) company_id, fiscal_year, reprt_code AS report_code, status, fetched_at
  FROM ops.dart_periodic_fetch
  WHERE api = 'borrowing'
  ORDER BY company_id, fiscal_year DESC,
           array_position(ARRAY['11013','11012','11014','11011'], reprt_code) DESC NULLS LAST  -- 같은 해면 사업보고서(연말)
), f AS (
  SELECT company_id, fiscal_year, report_code, fs_div, period_end, rcept_no, amount,
         replace(split_part(account_nm, '#', 1), ' ', '') AS nm
  FROM core.financial_fact
  WHERE source = 'dart_borrow' AND sj_div = 'BS'
)
SELECT c.market, c.ticker, c.name, l.fiscal_year, l.report_code, l.status,
       max(f.fs_div) AS fs_div,
       max(f.period_end) AS period_end,
       coalesce(sum(f.amount) FILTER (WHERE f.nm NOT LIKE '%차입부채%'), 0) AS total_borrowings,
       sum(f.amount) FILTER (WHERE f.nm LIKE '%차입부채%') AS bank_borrow,
       count(f.nm) AS n_accounts,
       string_agg(f.nm, ', ' ORDER BY f.amount DESC) AS accounts,
       max(f.rcept_no) AS rcept_no
FROM latest l
JOIN core.company c USING (company_id)
LEFT JOIN f USING (company_id, fiscal_year, report_code)
GROUP BY c.market, c.ticker, c.name, l.fiscal_year, l.report_code, l.status;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kic_readonly') THEN
        GRANT SELECT ON core.v_borrowings TO kic_readonly;
    END IF;
END $$;
