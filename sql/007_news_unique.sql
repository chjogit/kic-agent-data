-- 007: 뉴스·공시 문서 중복 적재 방지 (같은 종목·출처·URL 은 한 번만)
CREATE UNIQUE INDEX IF NOT EXISTS ux_document_company_source_url
    ON rag.document (company_id, source, url);
