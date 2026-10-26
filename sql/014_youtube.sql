-- 유튜브 공개 영상 (YouTube Data API, API 키만 사용). 공개 댓글 본문은 rag.document(source='youtube_comment')
CREATE TABLE IF NOT EXISTS core.youtube_video (
    video_id      text PRIMARY KEY,
    company_id    bigint NOT NULL REFERENCES core.company (company_id),
    query         text,                     -- 검색어
    title         text,
    channel       text,                     -- 게시 채널명 (공개 정보)
    published_at  date,
    view_count    bigint,
    like_count    bigint,
    comment_count bigint,
    collected_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_youtube_video_company ON core.youtube_video (company_id, published_at DESC);
