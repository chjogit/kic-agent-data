-- 032: 감사의견 구분·강조사항·계속기업 불확실성 (DART accnutAdtorNmNdAdtOpinion, 작업 audit-opinion). 추가만, 기존 표 불변.
-- 한 사업보고서(rcept_no)에 당기·전기(·전전기)와 연결·별도 행이 섞여 오므로 seq(응답 순서)로 구분합니다.
-- 읽지 못한 의견은 opinion_class='unknown'(미확인)으로 두고 추정하지 않습니다. has_emphasis·going_concern 은 해당 필드가 응답에 없으면 NULL.
CREATE TABLE IF NOT EXISTS core.audit_opinion (
    company_id    bigint NOT NULL REFERENCES core.company (company_id),
    rcept_no      text   NOT NULL,          -- 사업보고서 접수번호
    seq           int    NOT NULL,          -- 응답 안의 순서
    settle_dt     date   NOT NULL,          -- 결산일 (이 의견이 가리키는 사업연도 말)
    fiscal_year   int    NOT NULL,
    period_label  text,                     -- '제57기(당기)' 등 원문
    auditor       text,                     -- 감사인
    opinion_raw   text,                     -- 감사의견 원문 ('적정의견' 등)
    opinion_class text   NOT NULL,          -- unqualified 적정 / qualified 한정 / adverse 부적정 / disclaimer 의견거절 / unknown 미확인
    emphasis      text,                     -- 강조사항 (없음 확정이면 빈 문자열, 필드 없음이면 NULL)
    core_matter   text,                     -- 핵심감사사항
    has_emphasis  boolean,                  -- 강조사항 또는 감사보고서 특기사항이 있는가
    going_concern boolean,                  -- 그 문장에 '계속기업'이 있는가 (계속기업 불확실성 신호)
    raw           jsonb,
    collected_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, rcept_no, seq)
);
CREATE INDEX IF NOT EXISTS ix_audit_opinion_fy ON core.audit_opinion (company_id, fiscal_year DESC);

-- 회사·사업연도별 가장 나쁜 의견 (연결·별도 중 하나라도 비적정이면 그것). DART 가 자리표시 행('-')만 주는 회사는 unknown(미확인).
CREATE OR REPLACE VIEW core.v_audit_opinion_latest AS
SELECT c.market, c.ticker, c.name, a.fiscal_year,
       CASE WHEN bool_or(a.opinion_class = 'disclaimer') THEN 'disclaimer'
            WHEN bool_or(a.opinion_class = 'adverse') THEN 'adverse'
            WHEN bool_or(a.opinion_class = 'qualified') THEN 'qualified'
            WHEN bool_or(a.opinion_class = 'unqualified') THEN 'unqualified'
            ELSE 'unknown' END AS opinion_class,
       bool_or(a.has_emphasis) AS has_emphasis, bool_or(a.going_concern) AS going_concern,
       max(a.auditor) AS auditor, max(a.rcept_no) AS rcept_no
FROM core.audit_opinion a JOIN core.company c USING (company_id)
GROUP BY c.market, c.ticker, c.name, a.fiscal_year;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kic_readonly') THEN
        GRANT SELECT ON core.audit_opinion, core.v_audit_opinion_latest TO kic_readonly;
    END IF;
END $$;
