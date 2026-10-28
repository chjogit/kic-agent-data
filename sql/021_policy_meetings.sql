-- 021: 통화정책 결정 회의 일정 (한국은행 금통위 통화정책방향 결정회의 · 미국 FOMC)
-- 에이전트 1 거시경제가 '다음 금리 결정까지 며칠'과 '직전 결정 후 며칠'을 근거로 쓰고, 다음 점검에 실제 날짜를 넣습니다.
-- 공식 API 가 없어 공표된 일정을 이 파일에 적어 둡니다. apply-schema 가 실행될 때마다 같은 내용으로 맞춥니다(ON CONFLICT).
-- 일정이 새로 나오거나 바뀌면 이 파일에 행을 더하거나 고치고, 임시 회의가 열리면 status='unscheduled' 로 더합니다.
--
-- 출처
--   FOMC: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm (2025~2027, 2026-10-28 확인)
--         성명 발표는 회의 둘째 날 14:00 미 동부시간 → 한국시간 다음 날 03:00(서머타임) 또는 04:00.
--         연준은 모든 일정이 '직전 회의에서 확정되기 전까지 잠정'이라고 밝히며, 2027년은 status='tentative' 로 둡니다.
--   한국은행: https://www.bok.or.kr/portal/singl/crncyPolicyDrcMtg/listYear.do?menuNo=200755 (표가 스크립트로 그려져
--         자동 확인이 안 돼 언론 보도와 교차 확인, 2026-10-28). 결정 발표는 회의 당일 오전 10시경(관례).
--         2027년 일정은 아직 공표 전입니다.

CREATE TABLE IF NOT EXISTS core.policy_meeting (
    bank          text    NOT NULL,          -- BOK 한국은행 금융통화위원회 · FOMC 미국 연방공개시장위원회
    decision_date date    NOT NULL,          -- 결정 발표일 (현지 날짜). FOMC 는 회의 둘째 날
    meeting_start date    NOT NULL,          -- 회의 시작일 (BOK 는 결정일과 같음)
    announce_at   timestamptz NOT NULL,      -- 결정 발표 시각
    projections   boolean NOT NULL DEFAULT false,  -- FOMC 경제전망(SEP·점도표) 발표 회의
    status        text    NOT NULL DEFAULT 'scheduled',  -- scheduled · tentative · unscheduled
    source_url    text    NOT NULL,
    note          text,
    PRIMARY KEY (bank, decision_date)
);

INSERT INTO core.policy_meeting (bank, decision_date, meeting_start, announce_at, projections, status, source_url, note)
SELECT bank, decision_date::date, meeting_start::date,
       (decision_date || ' ' || at_local)::timestamp AT TIME ZONE tz, projections, status, source_url, note
FROM (VALUES
    -- 한국은행 금통위 통화정책방향 결정회의 (발표 10:00 한국시간, 관례)
    ('BOK', '2025-02-25', '2025-02-25', '10:00', 'Asia/Seoul', false, 'scheduled', 'bok', NULL),
    ('BOK', '2025-04-17', '2025-04-17', '10:00', 'Asia/Seoul', false, 'scheduled', 'bok', NULL),
    ('BOK', '2025-05-29', '2025-05-29', '10:00', 'Asia/Seoul', false, 'scheduled', 'bok', NULL),
    ('BOK', '2025-07-10', '2025-07-10', '10:00', 'Asia/Seoul', false, 'scheduled', 'bok', NULL),
    ('BOK', '2025-08-28', '2025-08-28', '10:00', 'Asia/Seoul', false, 'scheduled', 'bok', NULL),
    ('BOK', '2025-10-23', '2025-10-23', '10:00', 'Asia/Seoul', false, 'scheduled', 'bok', NULL),
    ('BOK', '2025-11-27', '2025-11-27', '10:00', 'Asia/Seoul', false, 'scheduled', 'bok', NULL),
    ('BOK', '2026-01-15', '2026-01-15', '10:00', 'Asia/Seoul', false, 'scheduled', 'bok', NULL),
    ('BOK', '2026-02-26', '2026-02-26', '10:00', 'Asia/Seoul', false, 'scheduled', 'bok', NULL),
    ('BOK', '2026-04-10', '2026-04-10', '10:00', 'Asia/Seoul', false, 'scheduled', 'bok', NULL),
    ('BOK', '2026-05-28', '2026-05-28', '10:00', 'Asia/Seoul', false, 'scheduled', 'bok', NULL),
    ('BOK', '2026-07-16', '2026-07-16', '10:00', 'Asia/Seoul', false, 'scheduled', 'bok', NULL),
    ('BOK', '2026-08-27', '2026-08-27', '10:00', 'Asia/Seoul', false, 'scheduled', 'bok', NULL),
    ('BOK', '2026-10-22', '2026-10-22', '10:00', 'Asia/Seoul', false, 'scheduled', 'bok', NULL),
    ('BOK', '2026-11-26', '2026-11-26', '10:00', 'Asia/Seoul', false, 'scheduled', 'bok', NULL),
    -- FOMC (성명 발표 14:00 미 동부시간, 회의 둘째 날). projections = 경제전망(SEP) 발표
    ('FOMC', '2025-01-29', '2025-01-28', '14:00', 'America/New_York', false, 'scheduled', 'fed', NULL),
    ('FOMC', '2025-03-19', '2025-03-18', '14:00', 'America/New_York', true,  'scheduled', 'fed', NULL),
    ('FOMC', '2025-05-07', '2025-05-06', '14:00', 'America/New_York', false, 'scheduled', 'fed', NULL),
    ('FOMC', '2025-06-18', '2025-06-17', '14:00', 'America/New_York', true,  'scheduled', 'fed', NULL),
    ('FOMC', '2025-07-30', '2025-07-29', '14:00', 'America/New_York', false, 'scheduled', 'fed', NULL),
    ('FOMC', '2025-09-17', '2025-09-16', '14:00', 'America/New_York', true,  'scheduled', 'fed', NULL),
    ('FOMC', '2025-10-29', '2025-10-28', '14:00', 'America/New_York', false, 'scheduled', 'fed', NULL),
    ('FOMC', '2025-12-10', '2025-12-09', '14:00', 'America/New_York', true,  'scheduled', 'fed', NULL),
    ('FOMC', '2026-01-28', '2026-01-27', '14:00', 'America/New_York', false, 'scheduled', 'fed', NULL),
    ('FOMC', '2026-03-18', '2026-03-17', '14:00', 'America/New_York', true,  'scheduled', 'fed', NULL),
    ('FOMC', '2026-04-29', '2026-04-28', '14:00', 'America/New_York', false, 'scheduled', 'fed', NULL),
    ('FOMC', '2026-06-17', '2026-06-16', '14:00', 'America/New_York', true,  'scheduled', 'fed', NULL),
    ('FOMC', '2026-07-29', '2026-07-28', '14:00', 'America/New_York', false, 'scheduled', 'fed', NULL),
    ('FOMC', '2026-09-16', '2026-09-15', '14:00', 'America/New_York', true,  'scheduled', 'fed', NULL),
    ('FOMC', '2026-10-28', '2026-10-27', '14:00', 'America/New_York', false, 'scheduled', 'fed', NULL),
    ('FOMC', '2026-12-09', '2026-12-08', '14:00', 'America/New_York', true,  'scheduled', 'fed', NULL),
    ('FOMC', '2027-01-27', '2027-01-26', '14:00', 'America/New_York', false, 'tentative', 'fed', NULL),
    ('FOMC', '2027-03-17', '2027-03-16', '14:00', 'America/New_York', true,  'tentative', 'fed', NULL),
    ('FOMC', '2027-04-28', '2027-04-27', '14:00', 'America/New_York', false, 'tentative', 'fed', NULL),
    ('FOMC', '2027-06-09', '2027-06-08', '14:00', 'America/New_York', true,  'tentative', 'fed', NULL),
    ('FOMC', '2027-07-28', '2027-07-27', '14:00', 'America/New_York', false, 'tentative', 'fed', NULL),
    ('FOMC', '2027-09-15', '2027-09-14', '14:00', 'America/New_York', true,  'tentative', 'fed', NULL),
    ('FOMC', '2027-10-27', '2027-10-26', '14:00', 'America/New_York', false, 'tentative', 'fed', NULL),
    ('FOMC', '2027-12-08', '2027-12-07', '14:00', 'America/New_York', true,  'tentative', 'fed', NULL)
) AS v(bank, decision_date, meeting_start, at_local, tz, projections, status, src, note)
CROSS JOIN LATERAL (SELECT CASE v.src
    WHEN 'fed' THEN 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'
    ELSE 'https://www.bok.or.kr/portal/singl/crncyPolicyDrcMtg/listYear.do?menuNo=200755' END AS source_url) s
ON CONFLICT (bank, decision_date) DO UPDATE SET
    meeting_start = EXCLUDED.meeting_start, announce_at = EXCLUDED.announce_at, projections = EXCLUDED.projections,
    status = EXCLUDED.status, source_url = EXCLUDED.source_url, note = EXCLUDED.note;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kic_readonly') THEN
        GRANT SELECT ON core.policy_meeting TO kic_readonly;
    END IF;
END $$;
