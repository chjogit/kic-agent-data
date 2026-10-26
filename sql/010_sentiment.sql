-- 010: 문서(뉴스·블로그·카페글) 감성 점수. 원문은 rag.document 의 제목·요약만 사용.
CREATE TABLE IF NOT EXISTS core.doc_sentiment (
    doc_id     bigint PRIMARY KEY REFERENCES rag.document (doc_id) ON DELETE CASCADE,
    score      numeric,              -- -1(부정) ~ +1(긍정), 판단 불가면 NULL
    label      text,                 -- positive | neutral | negative | none
    method     text NOT NULL,        -- lexicon | finbert
    scored_at  timestamptz NOT NULL DEFAULT now()
);
