-- kic-opinion 프로젝트 전용 스키마: 블로그·카페 문서와 그 감성 점수·일별 집계 (specs/db-capacity.md C7).
-- 메인과 달리 다른 프로젝트라 core.company 에 대한 FK 를 걸 수 없습니다. company_id 는 메인과 같은 값이며
-- core.company 는 점검용 최소 복사본입니다 (db.sync_opinion_companies 가 맞추고, 어긋나면 오류).
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS rag;
CREATE TABLE IF NOT EXISTS core.company (company_id bigint PRIMARY KEY, market text, ticker text, name text);
CREATE TABLE IF NOT EXISTS rag.document (
    doc_id       bigserial PRIMARY KEY,
    company_id   bigint,
    source       text NOT NULL,
    title        text,
    url          text,
    published_at date,
    summary      text,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_document_company_source_url ON rag.document (company_id, source, url);
CREATE TABLE IF NOT EXISTS core.doc_sentiment (
    doc_id     bigint PRIMARY KEY REFERENCES rag.document (doc_id) ON DELETE CASCADE,
    score      numeric,
    label      text,
    method     text NOT NULL,
    scored_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS core.doc_daily_agg (
    company_id bigint NOT NULL,
    source     text NOT NULL,
    d          date NOT NULL,
    n_docs     int NOT NULL,
    n_scored   int NOT NULL,
    score_sum  numeric NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, source, d)
);
