-- kic-opinion 읽기 전용 DB 역할 만들기 (#78). 자동 실행되지 않습니다(sql/manual/ 은 스키마 적용 대상이 아님).
-- 실행: 관리자(CH)가 Neon 콘솔 → kic-opinion 프로젝트 → SQL 편집기에서 소유자 계정으로 직접 실행합니다.
--       실행 전 '<비밀번호>' 자리에 새 비밀번호를 채우고, 이 파일·저장소·채팅방에는 비밀번호를 남기지 않습니다.
-- 팀원별로 역할을 따로 만들면(opinion_ro_이름) 폐기가 쉽습니다. 아래 opinion_ro 를 바꿔 쓰세요.
-- 소유자 역할이 실행해야 ALTER DEFAULT PRIVILEGES 가 앞으로 소유자가 만드는 표에 적용됩니다.

CREATE ROLE opinion_ro LOGIN PASSWORD '<비밀번호>';
GRANT USAGE ON SCHEMA core, rag TO opinion_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA core, rag TO opinion_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA core GRANT SELECT ON TABLES TO opinion_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA rag GRANT SELECT ON TABLES TO opinion_ro;
ALTER ROLE opinion_ro SET default_transaction_read_only = on;

-- 확인(읽기 전용 접속 문자열로 접속해서): SELECT count(*) FROM core.company;  → 성공
--                                       INSERT INTO core.company (market, ticker, name) VALUES ('X','X','X'); → 거부돼야 함

-- ---- 폐기(키 유출·퇴사 등): 아래를 소유자 역할로 실행 ----
-- ALTER DEFAULT PRIVILEGES IN SCHEMA core REVOKE SELECT ON TABLES FROM opinion_ro;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA rag REVOKE SELECT ON TABLES FROM opinion_ro;
-- REVOKE ALL ON ALL TABLES IN SCHEMA core, rag FROM opinion_ro;
-- REVOKE USAGE ON SCHEMA core, rag FROM opinion_ro;
-- DROP ROLE opinion_ro;
