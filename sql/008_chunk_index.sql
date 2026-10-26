-- 008: 벡터 근사 검색 인덱스 (코사인 거리). 임베딩은 정규화해 저장합니다.
CREATE INDEX IF NOT EXISTS ix_chunk_embedding ON rag.chunk USING hnsw (embedding vector_cosine_ops);
