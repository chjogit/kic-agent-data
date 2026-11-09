-- 008: 벡터 근사 검색 인덱스 (코사인 거리). 임베딩은 정규화해 저장합니다.
-- 열이 halfvec 로 바뀐 DB(026)에서는 vector_cosine_ops 로 만들 수 없으므로 건너뜁니다 (그 인덱스는 026 이 halfvec_cosine_ops 로 관리).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('rag.chunk') AND attname = 'embedding' AND NOT attisdropped
          AND format_type(atttypid, atttypmod) LIKE 'halfvec%'
    ) THEN
        NULL;   -- HNSW 인덱스는 만들지 않음(#69, sql/034). 다시 만들면 sql/034 가 지움
    END IF;
END $$;
