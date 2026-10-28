-- 020: DART 기업개황 정식 회사명 (작업 company-info, 주 1회)
-- 회사 마스터 이름(core.company.name)은 시세 원천의 약칭(예: '신한지주', 'NAVER')이라 공시 원문의 회사명과 다를 수 있습니다.
-- 회사채 발행실적의 발행사 이름처럼 공시 표기와 맞춰야 할 때 이 열을 함께 비교합니다 (OpenResearch exposure.TERMS_SQL).
ALTER TABLE core.company ADD COLUMN IF NOT EXISTS legal_name text;  -- DART company.json corp_name (예: '네이버(주)')
