-- 033: 감가상각·이자비용 항목 매핑 결과 (작업 financial-items). 추가만 — core.financial_fact 는 건드리지 않습니다.
-- 전체 재무제표(현금흐름표·손익계산서)에서 계정 ID·계정명 변형으로 고른 항목만 담는 좁은 표(회사·연도당 약 10행).
-- 현금흐름표에 감가상각을 따로 적지 않는 회사(주석에만 있음)는 이 표에도 없습니다 → EBITDA '미확보'.
CREATE TABLE IF NOT EXISTS core.financial_item (
    company_id   bigint NOT NULL REFERENCES core.company (company_id),
    fiscal_year  int    NOT NULL,
    report_code  text   NOT NULL,           -- 11011 사업보고서
    fs_div       text   NOT NULL,           -- CFS 연결 / OFS 별도
    item         text   NOT NULL,           -- depreciation·rou_depreciation·amortization·dep_amort·interest_expense·finance_costs·interest_paid
    sj_div       text   NOT NULL,           -- IS·CIS·CF
    account_id   text   NOT NULL DEFAULT '',
    account_nm   text   NOT NULL,
    amount       numeric,
    rcept_no     text,
    collected_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, fiscal_year, report_code, fs_div, item, sj_div, account_nm)
);

-- 회사·연도별 EBITDA·이자보상배율. 입력이 있을 때만 값, 없으면 NULL(미확보).
--  · ebitda = 영업이익 + 감가상각 + 무형자산상각 + 사용권자산상각 (합산 계정이 있으면 그것). 감가상각(또는 합산)이 없으면 NULL.
--  · ebitda_complete: 무형자산상각까지 확인된 경우만 true (없는 회사는 무형자산이 없거나 주석에만 있는 것이라 하한 근사)
--  · interest_coverage = 영업이익 / 이자비용 (이자비용이 없으면 NULL; 금융비용은 외환손실 등을 포함하므로 대체하지 않고 따로 둠)
CREATE OR REPLACE VIEW core.v_ebitda_coverage AS
WITH oi AS (
    SELECT company_id, fiscal_year, fs_div, max(amount) AS operating_income
    FROM core.financial_fact
    WHERE report_code = '11011' AND sj_div IN ('IS', 'CIS')
      AND account_id IN ('dart_OperatingIncomeLoss', 'OperatingIncomeLoss', 'ifrs-full_ProfitLossFromOperatingActivities')
    GROUP BY 1, 2, 3
), it AS (
    SELECT company_id, fiscal_year, fs_div,
           max(abs(amount)) FILTER (WHERE item = 'depreciation' AND sj_div = 'CF') AS dep,
           max(abs(amount)) FILTER (WHERE item = 'amortization' AND sj_div = 'CF') AS amort,
           max(abs(amount)) FILTER (WHERE item = 'rou_depreciation' AND sj_div = 'CF') AS rou,
           max(abs(amount)) FILTER (WHERE item = 'dep_amort' AND sj_div = 'CF') AS dep_amort,
           coalesce(max(abs(amount)) FILTER (WHERE item = 'interest_expense' AND sj_div IN ('IS', 'CIS')),
                    max(abs(amount)) FILTER (WHERE item = 'interest_expense' AND sj_div = 'CF')) AS interest_expense,
           max(abs(amount)) FILTER (WHERE item = 'finance_costs' AND sj_div IN ('IS', 'CIS')) AS finance_costs,
           max(abs(amount)) FILTER (WHERE item = 'interest_paid' AND sj_div = 'CF') AS interest_paid
    FROM core.financial_item
    GROUP BY 1, 2, 3
)
SELECT c.market, c.ticker, c.name, it.fiscal_year, it.fs_div, oi.operating_income,
       it.dep, it.amort, it.rou, it.dep_amort, it.interest_expense, it.finance_costs, it.interest_paid,
       CASE WHEN oi.operating_income IS NOT NULL AND (it.dep_amort IS NOT NULL OR it.dep IS NOT NULL)
            THEN oi.operating_income + coalesce(it.dep_amort, coalesce(it.dep, 0) + coalesce(it.amort, 0) + coalesce(it.rou, 0)) END AS ebitda,
       (it.dep_amort IS NOT NULL OR (it.dep IS NOT NULL AND it.amort IS NOT NULL)) AS ebitda_complete,
       CASE WHEN oi.operating_income IS NOT NULL AND it.interest_expense > 0
            THEN round(oi.operating_income / it.interest_expense, 2) END AS interest_coverage
FROM it JOIN core.company c USING (company_id)
LEFT JOIN oi ON oi.company_id = it.company_id AND oi.fiscal_year = it.fiscal_year AND oi.fs_div = it.fs_div;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kic_readonly') THEN
        GRANT SELECT ON core.financial_item, core.v_ebitda_coverage TO kic_readonly;
    END IF;
END $$;
