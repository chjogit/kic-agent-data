# 데이터 변경 기록

kic-agent-data(수집)와 OpenResearch(에이전트)가 **같은 내용을 두 저장소에 둡니다**(파일 이름 `docs/DATA_CHANGES.md`). 최신이 위입니다.

## 쓰는 법
- 스키마(표·열·뷰), DB 배치(어느 프로젝트에 두는지), 환경변수·시크릿, 보관 정책, 소스 이름·키를 바꾸는 PR 은 **같은 PR 에서 한 항목을 맨 위에 추가**합니다. 한 쪽 저장소에서 추가했으면 다른 쪽에도 같은 항목을 붙입니다.
- 항목 형식: `### YYYY-MM-DD 제목 (PR/티켓)` 아래에 **변경 / 영향 / 필요 조치** 세 줄. 영향받는 팀원이 있으면 이름(CH·JT·SH·SW)을 적습니다.
- 비밀값·접속 주소는 쓰지 않습니다(환경변수 이름만).
- 정의의 원본은 kic-agent-data `sql/`, 읽는 쪽 요약은 OpenResearch `docs/DATA_MAP.md` 입니다.

---

### 2026-11-03 새 뉴스·공시 청크 자동 임베딩 (#27, kic-agent-data)
- **변경**: `embed-docs` 가 자동 실행기에서 하루 1회 돕니다(계산 줄, `purge-news` 다음). 청크가 없는 문서만 처리(멱등), DB 800MB 상한 유지. 새 시크릿 `UPSTAGE_API_KEY`(없으면 그 작업만 건너뜀).
- **영향**: `rag.chunk` 가 새 뉴스까지 따라갑니다. 11/3 점검 시점에 청크가 없는 문서는 naver_news 13,312건·finnhub_news 2,137건이었고(밀린 분량 적재 전), 적재 뒤 약 +87MB(메인 573MB → 약 660MB 추정). 청크 인용을 쓰는 SH(`dart_sec_risk_examiner`)·CH 는 최근 뉴스 청크가 늘어납니다.
- **필요 조치**: kic-agent-data Actions 시크릿 `UPSTAGE_API_KEY` 등록(사용자). 밀린 분량 적재는 사용자 승인 뒤 1회 실행.

### 2026-11-02 DART 공시 원문 Object Storage 보관 (#09·#19·#20, kic-agent-data)
- **변경**: DART 정기(A)·주요사항(B) 공시 원문 zip 을 Neon Object Storage 에 보관. 메타는 메인 `ops.dart_archive`(rcept_no, object_key, size_bytes …), 원문이 없는 014 공시는 `ops.dart_archive_skip` 에 영구 건너뜀으로 기록. 환경변수 `NEON_STORAGE_ENDPOINT`·`NEON_STORAGE_BUCKET`·`NEON_STORAGE_ACCESS_KEY_ID`·`NEON_STORAGE_SECRET_ACCESS_KEY`(·`NEON_STORAGE_REGION`), 별칭 `AWS_*`. 누적 상한 4GB.
- **영향**: 11/2 업로드 1,402건 430.7MB. 에이전트가 원문을 읽는 코드는 아직 없음(읽기 접속은 별도 합의 필요).
- **필요 조치**: 없음. 원문 읽기가 필요한 팀원은 PM 에게 요청.

### 2026-11-02 문서 보관 14일 + 일별 집계 (#15·#17, kic-agent-data)
- **변경**: 뉴스(`naver_news`·`finnhub_news`)·유튜브 댓글·블로그·카페는 발행일 기준 14일만 보관하고 지운 문서는 **일별 집계 `core.doc_daily_agg`**(company_id, source, d, n_docs, n_scored, score_sum)에 남깁니다. 감성(`core.doc_sentiment`)·청크(`rag.chunk`)도 함께 삭제. 제외: `naver_news_backfill`, `dart_report`, `core.youtube_video`. 환경변수 `RETENTION_DAYS`(기본 14). 매일 자동(`purge-opinion`·`purge-news`).
- **영향**: 14일보다 오래된 개별 문서는 없습니다. 20일·30일 윈도 감성은 `doc_daily_agg`(평균 = score_sum / n_scored)를 함께 써야 합니다. 문서 원문을 20일 이상 보는 코드(CH 에이전트 3·6, SH 청크 선택)는 결과가 달라집니다.
- **필요 조치**: CH — 장기 감성은 `core.doc_daily_agg` 로(진행: `claude/agent3-doc-agg`). 그 외 팀원은 `rag.document` 를 14일 창으로 가정.

### 2026-11-02 블로그·카페를 kic-opinion 프로젝트로 분리 (#10·#14, kic-agent-data)
- **변경**: `naver_blog`·`naver_cafe` 문서·감성·일별 집계는 별도 Neon 프로젝트 **kic-opinion** 의 `rag.document`·`core.doc_sentiment`·`core.doc_daily_agg` 에 둡니다(`sql/opinion/001_opinion.sql`). 접속 환경변수 `OPINION_DATABASE_URL`. 메인에는 블로그·카페가 더 이상 없습니다. kic-opinion 의 `doc_id` 는 메인 최대값 + 10억 간격으로 겹치지 않게 시작하고, `company_id` 는 메인과 같은 값(외래키 없음).
- **영향**: 메인 `rag.document` 에서 `source IN ('naver_blog','naver_cafe')` 를 읽던 코드는 **빈 결과**를 받습니다. 현재 `members/CH/ch_agents`(`rag.py`·`agent3_sentiment_flow.py`·`sentiment_eval.py`)가 이 소스를 메인에서 읽습니다 → CH 영향. kic-opinion 은 메인과 별도 프로젝트라 읽기 전용 계정도 따로 필요합니다.
- **필요 조치**: CH — 블로그·카페는 `OPINION_DATABASE_URL`(읽기 전용)에서 읽거나 `core.doc_daily_agg` 로 대체 결정(PM 확인 필요). kic-opinion 을 읽으려면 메인의 `agent_reader` 와 같은 읽기 전용 역할이 필요합니다(`docs/DB_READONLY.md` 방식, 관리자 작업).

### 2026-11-02 RAG 청크 벡터 `vector(1024)` → `halfvec(1024)` (kic-agent-data `sql/026`)
- **변경**: `rag.chunk.embedding` 이 `halfvec(1024)`(저장 절반), 인덱스 `ix_chunk_embedding` 은 `halfvec_cosine_ops`. 연산자는 같은 `<=>` 이지만 쿼리 벡터도 `halfvec` 로 캐스팅해야 합니다.
- **영향**: 11/3 기준 이 저장소에는 `rag.chunk` 를 벡터 연산자로 검색하는 코드가 없습니다(SH `neon_agents` 는 `doc_id` 로 청크를 읽음 → 영향 없음, `agent/tools/dart_sec_risk_examiner.py` 는 청크 검색이 TODO). 앞으로 벡터 검색을 붙이면 쿼리 벡터도 `halfvec` 로 맞춰야 합니다. 임베딩은 Upstage `solar-embedding-2-passage`(문서)/`-query`(질의), 차원 1024.
- **필요 조치**: 청크 벡터 검색을 새로 만드는 팀원(SH·SW)만 참고.

### 2026-11-02 에이전트 번호 3·4·6 분리, 계약 r4-1.3 (OpenResearch #21~#25)
- **변경**: 거시(3)·수급(4)·투자심리·뉴스(6) 에이전트 노드(N05~N08·N11·N12). 데이터 계약은 `r4-1.3`(선택 필드만 추가: `agent.agentNos`, `claims[].tag`, `nodes[]`, `nodeSummary`, `conflicts`, `predictionSkippedReason`), 옛 출력 호환. 옛 에이전트 번호 → 새 번호 변환표(p01→3, p03→4·6).
- **영향**: 에이전트 번호 1·3 을 기대하던 소비 코드(SW 오케스트레이터)는 `agentNos` 로 매핑. DB 스키마 변화는 없음.
- **필요 조치**: SW — 새 번호·선택 필드 반영.
