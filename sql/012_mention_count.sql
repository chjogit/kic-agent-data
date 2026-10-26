-- 012: 언급량(검색 결과 누적 수 total) 스냅샷.
-- 수집 표본(최신 100~200건)은 큰 종목일수록 '오늘 글' 만 잡혀 기간 비교가 왜곡됩니다.
-- 대신 검색 API 의 total(누적 결과 수)을 시점마다 저장하고, 두 시점의 차이를 '그 사이 새 글 수' 로 씁니다.
CREATE TABLE IF NOT EXISTS core.mention_count (
    company_id  bigint NOT NULL REFERENCES core.company (company_id),
    source      text   NOT NULL,          -- naver_news | naver_blog | naver_cafe
    query       text   NOT NULL,
    captured_at timestamptz NOT NULL DEFAULT now(),
    trade_date  date   NOT NULL,          -- 한국 날짜
    total       bigint NOT NULL,
    PRIMARY KEY (company_id, source, captured_at)
);
CREATE INDEX IF NOT EXISTS ix_mention_count_day ON core.mention_count (company_id, source, trade_date);

ALTER TABLE core.reaction_pulse ADD COLUMN IF NOT EXISTS new_since_last int;   -- 직전 스냅샷 이후 새 글 (total 차이)
