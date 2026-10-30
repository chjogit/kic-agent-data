-- 027: DART A·B 공시 원문 보관 메타 (원문은 Neon Object Storage, DB 에는 위치·크기만). 접수번호 기준 멱등.
CREATE TABLE IF NOT EXISTS ops.dart_archive (
    rcept_no    text PRIMARY KEY,
    company_id  bigint REFERENCES core.company (company_id),
    ticker      text,
    category    text NOT NULL,              -- DART pblntf_ty: A 정기공시 / B 주요사항보고
    object_key  text NOT NULL,              -- 버킷 안 위치 (dart/<유형>/<연도>/<접수번호>.zip)
    size_bytes  bigint NOT NULL,
    fetched_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_dart_archive_company ON ops.dart_archive (company_id);
