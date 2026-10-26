-- 001: 확장과 스키마
-- pgvector (RAG용 벡터 타입). Neon에서는 별도 설치 없이 켤 수 있습니다.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS raw;   -- API 원본 응답 (무료 1GB 한도 때문에 기본은 저장하지 않음)
CREATE SCHEMA IF NOT EXISTS core;  -- 정제된 분석용 테이블
CREATE SCHEMA IF NOT EXISTS rag;   -- 문서 요약과 벡터
