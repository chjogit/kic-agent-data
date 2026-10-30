-- 026: rag.chunk.embedding 을 halfvec(1024) 로 (벡터 저장 크기 절반, 무료 1GB 한도 대비).
-- 청크가 아직 0건일 때만 바꿉니다(이미 적재된 열은 건드리지 않음). 매 실행 적용돼도 안전하도록 멱등.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('rag.chunk') AND attname = 'embedding' AND NOT attisdropped
          AND format_type(atttypid, atttypmod) = 'vector(1024)'
    ) AND NOT EXISTS (SELECT 1 FROM rag.chunk) THEN
        DROP INDEX IF EXISTS rag.ix_chunk_embedding;
        ALTER TABLE rag.chunk ALTER COLUMN embedding TYPE halfvec(1024);
        CREATE INDEX ix_chunk_embedding ON rag.chunk USING hnsw (embedding halfvec_cosine_ops);
    END IF;
END $$;
