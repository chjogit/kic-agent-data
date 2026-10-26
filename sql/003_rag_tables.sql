-- 003: RAG용 문서와 벡터
-- 무료 플랜 1GB 한도 때문에 원문 전체가 아니라 요약과 청크, 임베딩만 저장합니다.
-- 임베딩 차원(1024)은 사용할 임베딩 모델에 맞게 바꾸세요. 모델을 정한 뒤 테이블을 만드는 것이 안전합니다.

CREATE TABLE IF NOT EXISTS rag.document (
    doc_id       bigserial PRIMARY KEY,
    company_id   bigint REFERENCES core.company (company_id),
    source       text   NOT NULL,           -- dart | sec_edgar | news 등
    title        text,
    url          text,
    published_at date,
    summary      text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rag.chunk (
    chunk_id   bigserial PRIMARY KEY,
    doc_id     bigint NOT NULL REFERENCES rag.document (doc_id) ON DELETE CASCADE,
    chunk_idx  int    NOT NULL,
    content    text   NOT NULL,
    embedding  vector(1024),
    UNIQUE (doc_id, chunk_idx)
);

-- 데이터가 쌓인 뒤에 근사 검색 인덱스를 만드세요 (예시):
-- CREATE INDEX ix_chunk_embedding ON rag.chunk USING hnsw (embedding vector_cosine_ops);
