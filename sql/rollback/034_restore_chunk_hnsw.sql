-- 034 되돌리기: HNSW 인덱스 다시 만들기(약 197MB, 수 분). 용량 여유가 있을 때만. 수동 실행.
CREATE INDEX IF NOT EXISTS ix_chunk_embedding ON rag.chunk USING hnsw (embedding halfvec_cosine_ops);
