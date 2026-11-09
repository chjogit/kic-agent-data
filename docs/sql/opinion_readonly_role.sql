-- kic-opinion 읽기 전용 역할 (#78). 관리자가 Neon SQL 편집기(kic-opinion 프로젝트)에서 직접 실행합니다.
-- <비밀번호> 는 실행할 때만 채우고, 이 파일·저장소·채팅방에는 절대 적지 않습니다.
-- 팀원별로 역할을 따로 만들면(opinion_ro_이름) 폐기가 쉽습니다.
CREATE ROLE opinion_ro LOGIN PASSWORD '<비밀번호>';
GRANT USAGE ON SCHEMA core, rag TO opinion_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA core, rag TO opinion_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA core GRANT SELECT ON TABLES TO opinion_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA rag GRANT SELECT ON TABLES TO opinion_ro;
ALTER ROLE opinion_ro SET default_transaction_read_only = on;
-- 폐기: REVOKE ALL ON ALL TABLES IN SCHEMA core, rag FROM opinion_ro; REVOKE USAGE ON SCHEMA core, rag FROM opinion_ro; DROP ROLE opinion_ro;
