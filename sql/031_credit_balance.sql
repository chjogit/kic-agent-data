-- 031: 신용공여 잔고 (시장 전체 합계, 종목별 아님). 공공데이터포털 금융투자협회종합통계정보, 작업 credit-balance. 추가만.
-- 필드 이름은 첫 실응답 확인 전이라 숫자 칸을 모두 fields(jsonb)에 두고, 신용거래융자로 보이는 칸은 loan_total 에 따로 둡니다.
CREATE TABLE IF NOT EXISTS core.credit_balance (
    bas_dt       date PRIMARY KEY,         -- 기준일자
    loan_total   numeric,                  -- 신용거래융자 잔고 (원천 단위 그대로, 단위는 첫 응답으로 확인 후 문서화)
    fields       jsonb,                    -- 숫자 칸 전부 {필드명: 값}
    raw          jsonb,
    collected_at timestamptz NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kic_readonly') THEN
        GRANT SELECT ON core.credit_balance TO kic_readonly;
    END IF;
END $$;
