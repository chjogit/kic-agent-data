-- 028: 블로그·카페 문서 일별 집계. 보관 기한(retention)이 지난 문서 행을 지우기 전에 에이전트 3 이 쓰는 값을 남깁니다.
-- 에이전트 3 의 문서 감성은 (종목, 소스, 일자)별 '점수가 붙은 문서 수 n' 과 '점수 평균' 만 씁니다 → n_scored, score_sum 으로 보존.
-- 언급량은 core.mention_count(검색 API total 스냅샷)에 이미 문서와 별개로 저장돼 있어 여기서 다루지 않습니다.
CREATE TABLE IF NOT EXISTS core.doc_daily_agg (
    company_id  bigint NOT NULL REFERENCES core.company (company_id),
    source      text   NOT NULL,            -- naver_blog | naver_cafe
    d           date   NOT NULL,            -- 문서 발행일(published_at)
    n_docs      int    NOT NULL,            -- 지운 문서 수
    n_scored    int    NOT NULL,            -- 그중 감성 점수가 붙은 문서 수
    score_sum   numeric NOT NULL,           -- 점수 합 (평균 = score_sum / n_scored)
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, source, d)
);
