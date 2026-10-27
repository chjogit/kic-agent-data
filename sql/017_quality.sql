-- 017: 데이터 품질 (API별 최근 상태, 품질 체크·이상 이벤트, 수집 건수 스냅샷)
-- 쓰기: 수집 작업이 끝날 때마다 kic_data/quality.py 가 기록. 읽기: 품질 대시보드(/quality, /api/quality/*).

CREATE SCHEMA IF NOT EXISTS quality;

-- API별 최근 상태
CREATE TABLE IF NOT EXISTS quality.api_status (
    api_name              text PRIMARY KEY,
    last_success          timestamptz,
    last_error            timestamptz,
    last_error_msg        text,
    consecutive_failures  int DEFAULT 0,
    quota_hit             boolean DEFAULT false,
    last_run_duration_ms  int,
    updated_at            timestamptz DEFAULT now()
);

-- 품질 체크 결과
CREATE TABLE IF NOT EXISTS quality.checks (
    id          bigserial PRIMARY KEY,
    check_time  timestamptz DEFAULT now(),
    source      text NOT NULL,          -- 'dart', 'yfinance', 'features', 'ecos' 등
    rule_id     text NOT NULL,          -- 'job_failed', 'job_partial', 'null_rate', 'price_spike', 'count_drop' 등
    severity    text NOT NULL CHECK (severity IN ('info', 'warn', 'error')),
    target      text,                   -- 티커, 테이블명, 작업명
    message     text,
    details     jsonb,
    resolved    boolean DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_checks_time ON quality.checks (check_time DESC);
CREATE INDEX IF NOT EXISTS idx_checks_severity ON quality.checks (severity, check_time DESC);
CREATE INDEX IF NOT EXISTS idx_checks_source ON quality.checks (source, check_time DESC);

-- 수집 건수 스냅샷 (이상 탐지용, 다음 단계에서 채움)
CREATE TABLE IF NOT EXISTS quality.collection_stats (
    id            bigserial PRIMARY KEY,
    collected_at  timestamptz DEFAULT now(),
    source        text NOT NULL,
    record_count  int,
    target_date   date
);

COMMENT ON SCHEMA quality IS '데이터 품질 체크 및 이상 이벤트 기록';

-- 대시보드용 읽기 전용 계정(kic_readonly)을 Neon 에서 만들어 두었으면 읽기 권한을 줍니다 (없으면 아무것도 안 함).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kic_readonly') THEN
        GRANT USAGE ON SCHEMA core, ops, quality, rag TO kic_readonly;
        GRANT SELECT ON ALL TABLES IN SCHEMA core, ops, quality, rag TO kic_readonly;
    END IF;
END $$;
