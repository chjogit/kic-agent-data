-- 034: rag.chunk 의 HNSW 벡터 인덱스(ix_chunk_embedding) 삭제 (#69, 사용자 승인 11/6). 메인 DB 용량 약 -197MB.
-- 이유: ① 800MB 한도(773MB) ② HNSW 는 종목·기준일 필터가 근사 검색 뒤에 걸려 40개를 요청해도 0~21개만 돌려줌(post-filter).
-- 이후 벡터 검색은 종목·출처·기준일로 먼저 거른 뒤 정확 거리 정렬(종목당 청크 수천 건이라 1초 안팎). 멱등.
-- 되돌리기: sql/rollback/034_restore_chunk_hnsw.sql (자동 적용 대상 아님 — apply-schema 는 sql/*.sql 만 읽음).
DROP INDEX IF EXISTS rag.ix_chunk_embedding;
