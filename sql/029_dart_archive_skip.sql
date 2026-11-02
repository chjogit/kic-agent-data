-- 029: 원문이 없는 공시(DART 014) 영구 건너뜀 기록. archive-dart 가 다시 시도하지 않게 접수번호만 남김.
CREATE TABLE IF NOT EXISTS ops.dart_archive_skip (
    rcept_no    text PRIMARY KEY,
    reason      text NOT NULL,
    checked_at  timestamptz NOT NULL DEFAULT now()
);
