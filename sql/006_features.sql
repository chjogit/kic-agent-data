-- 006: 5개 관점 에이전트 입력용 지표 스냅샷 (긴 형식)
CREATE TABLE IF NOT EXISTS core.feature_snapshot (
    company_id  bigint  NOT NULL REFERENCES core.company (company_id),
    as_of       date    NOT NULL,
    perspective text    NOT NULL CHECK (perspective IN ('valuation', 'fundamentals', 'supply_demand', 'macro', 'risk')),
    feature     text    NOT NULL,
    value       numeric,
    unit        text,
    note        text,
    PRIMARY KEY (company_id, as_of, perspective, feature)
);
CREATE INDEX IF NOT EXISTS ix_feature_snapshot_asof ON core.feature_snapshot (as_of, perspective);
