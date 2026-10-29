-- 018: 사업보고서 원문에서 뽑은 기업 고유 거시 노출 (kic_data/exposure_parse.py, 작업 report-exposure)
-- A1 거시 에이전트가 '업종 기본값' 대신 회사 실제 값으로 노출 등급을 정할 때 씁니다.
-- metric: fx_sens(환율 change_pct% 상승 시 basis 영향, 원) · fx_asset / fx_liab(외화 화폐성 자산·부채, 원화 환산 원)
--         export_share(수출 매출 비중 %) · fx_derivative(외환 파생상품 사용 1/0)
--         ir_sens(이자율 change_pct%p 상승 시 basis 영향, 원)
--         (v2, 10-29) ir_sens_liab(이자율 상승 시 부채 쪽 행만의 영향, 원) · fx_hedge_acct(환율 위험회피회계 적용 1)

CREATE TABLE IF NOT EXISTS core.report_exposure (
    company_id   bigint NOT NULL REFERENCES core.company (company_id),
    fiscal_year  int NOT NULL,
    rcept_no     text NOT NULL,
    metric       text NOT NULL,
    currency     text NOT NULL DEFAULT 'ALL',
    value        numeric,
    change_pct   numeric,          -- fx_sens: 몇 % 변동 / ir_sens: 몇 %p 변동 기준인지 (100bp = 1)
    basis        text,             -- fx_sens: 세전이익 / 당기손익 / 자본
    unit_raw     text,             -- 원문 표의 단위 표기
    evidence     text,             -- 표 앞 문맥 (근거)
    file         text,
    table_index  int,
    extracted_at timestamptz DEFAULT now(),
    PRIMARY KEY (company_id, fiscal_year, metric, currency)
);

-- 회사·연도별로 원문을 이미 읽었는지 (찾은 지표가 없어도 기록해 같은 원문을 다시 받지 않음)
CREATE TABLE IF NOT EXISTS ops.report_parse (
    company_id   bigint NOT NULL REFERENCES core.company (company_id),
    fiscal_year  int NOT NULL,
    rcept_no     text NOT NULL,
    found        text[],
    status       text,
    parsed_at    timestamptz DEFAULT now(),
    PRIMARY KEY (company_id, fiscal_year)
);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kic_readonly') THEN
        GRANT SELECT ON core.report_exposure, ops.report_parse TO kic_readonly;
    END IF;
END $$;
