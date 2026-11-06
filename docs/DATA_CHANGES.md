# 데이터 변경 기록

kic-agent-data(수집)와 OpenResearch(에이전트)가 **같은 내용을 두 저장소에 둡니다**(파일 이름 `docs/DATA_CHANGES.md`). 최신이 위입니다.

## 쓰는 법
- 스키마(표·열·뷰), DB 배치(어느 프로젝트에 두는지), 환경변수·시크릿, 보관 정책, 소스 이름·키를 바꾸는 PR 은 **같은 PR 에서 한 항목을 맨 위에 추가**합니다. 한 쪽 저장소에서 추가했으면 다른 쪽에도 같은 항목을 붙입니다.
- 항목 형식: `### YYYY-MM-DD 제목 (PR/티켓)` 아래에 **변경 / 영향 / 필요 조치** 세 줄. 영향받는 팀원이 있으면 이름(CH·JT·SH·SW)을 적습니다.
- 비밀값·접속 주소는 쓰지 않습니다(환경변수 이름만).
- 정의의 원본은 kic-agent-data `sql/`, 읽는 쪽 요약은 OpenResearch `docs/DATA_MAP.md` 입니다.

---

### 2026-11-03 신용공여 잔고(시장 전체) 수집 (#40, kic-agent-data `sql/031`) — 추가만
- **변경**: 새 표 `core.credit_balance`(bas_dt, loan_total, fields jsonb, raw jsonb) — 공공데이터포털 금융위원회_금융투자협회종합통계정보의 신용공여 잔고 추이. **시장 전체 합계이며 종목별이 아님.** 작업 `credit-balance` 매일(키 `DATA_GO_KR_KEY`/`FSC_API_KEY`). 키가 서비스에 적용 전이면 '건너뜀'. 11/4 적재 완료: 1,216행(2021-11-09~2026-11-02, 영업일 일별), 원천이 하루 늦게 올라옴. 과거분 보충은 `collect-credit-balance --days N --backfill`.
- **영향**: 기존 표·열 변화 없음. 엔드포인트·필드·단위는 11/4 `probe-credit-balance` 로 확인 완료(단위 **원**). `loan_total` = 신용거래융자 전체(`crdTrFingWhl`, 약 29.2조), 나머지 숫자 칸(유가증권·코스닥 융자, 대주, 청약자금대출, 예탁증권담보융자)은 `fields` 에 원천 이름 그대로 저장.
- **필요 조치**: 수급(에이전트 4)·심리(에이전트 6) 담당은 시장 배경 지표로만 사용(종목 비교 금지). 보고서에 '출처: 금융위원회(금융투자협회)' 표시.
### 2026-11-03 감가상각·이자비용 항목 매핑 확대 (#36, kic-agent-data `sql/033`) — 추가만
- **변경**: 새 표 `core.financial_item`(회사·연도·연결/별도별 감가상각·무형자산상각·사용권자산상각·합산 D&A·이자비용·금융비용·이자지급 항목)과 뷰 `core.v_ebitda_coverage`(영업이익 + 상각 = EBITDA, 영업이익 / 이자비용 = 이자보상배율). 작업 `financial-items` 매주. `core.financial_fact` 는 변경 없음.
- **영향**: 입력이 있을 때만 값, 없으면 NULL(미확보). 2025 사업보고서 기준 감가상각 확보 5→약 53개사, 이자비용 34→61개사(금융비용은 외환손실 등을 포함해 이자비용으로 대체하지 않음). 나머지는 현금흐름표에 상각을 따로 적지 않고 주석에만 있어 미확보.
- **필요 조치**: 기업가치 담당은 `v_ebitda_coverage` 사용, `ebitda_complete` 가 false 면 무형자산상각 미확인(하한 근사).

### 2026-11-03 감사의견 구분 정형화 (#35, kic-agent-data `sql/032`) — 추가만
- **변경**: 새 표 `core.audit_opinion`(사업보고서별 감사인·감사의견 원문·구분 `opinion_class`(unqualified 적정/qualified 한정/adverse 부적정/disclaimer 의견거절/unknown 미확인)·강조사항·핵심감사사항·`has_emphasis`·`going_concern`)과 뷰 `core.v_audit_opinion_latest`(회사·연도별 가장 나쁜 의견). 원천은 DART `accnutAdtorNmNdAdtOpinion`. 작업 `audit-opinion` 매주.
- **영향**: 기존 표 변화 없음. DART 가 자리표시 행('-')만 주는 회사(2025 사업보고서 기준 5개사 전부, 9개사 일부 행)는 `unknown` — 추정하지 않음. `going_concern` 은 강조사항·특기사항 문장에 '계속기업'이 있을 때만 true.
- **필요 조치**: 재무·회계 담당은 `v_audit_opinion_latest` 사용. `unknown` 은 '확인 불가'로 표기(적정으로 간주 금지).

### 2026-11-03 지분공시 수집·정형화 (#34, kic-agent-data `sql/030`) — 추가만
- **변경**: DART 지분공시(유형 D) 목록을 `core.disclosure`(category `D`)에 추가 수집(작업 `disclosures-ownership` 매일·`backfill-disclosures-ownership` 1회). 정형 새 표 `core.ownership_report`(대량보유상황보고: 접수번호·보고자·보고일·보유 주식 수·보유 비율%·증감%p·보고 사유)와 `core.largest_holder`(최대주주 및 특수관계인: 사업연도·성명·관계·주식 종류·기말 지분율%·기초 지분율%), 뷰 `core.v_largest_holder_total`(보통주 합계). 작업 `ownership` 매주. 기존 A·B·I 수집과 표는 그대로.
- **영향**: 기존 표·열 변화 없음. 임원·주요주주 소유보고는 `core.disclosure` 목록(`category='D'`)만 있고 지분율 정형 표는 아직 없음. 필드 이름은 DART 개발가이드 기준이라 첫 실행 후 `probe-ownership` 로 확인 필요(원 응답은 `raw` jsonb 에 보존).
- **필요 조치**: 지배구조·경영진 담당 팀원은 `core.ownership_report`·`core.largest_holder` 를 읽기 전용으로 사용.

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

### 2026-11-06 RAG 청크 HNSW 인덱스 삭제 + 유튜브 댓글 임베딩 (#68·#69, kic-agent-data `sql/034`)
- **변경**: `rag.chunk` 의 `ix_chunk_embedding`(HNSW) 삭제 — 메인 DB 약 -197MB(773MB → 약 576MB 예상). 되돌리기 SQL 은 `sql/rollback/034_restore_chunk_hnsw.sql`. 유튜브 댓글(`youtube_comment`)도 `embed-docs` 대상에 추가(매일 `embed-docs` 에서 자동 임베딩, 14일 보관 삭제 때 청크도 함께 삭제).
- **영향**: 벡터 검색(`ORDER BY embedding <=> ...`)은 결과는 같고 정확해지되(HNSW 는 종목 필터 뒤에 걸려 40개를 요청해도 0~21개만 돌려줬음) 종목·기준일로 먼저 거른 뒤 정렬해 약 1초 안팎입니다. 이 저장소에서 벡터 검색을 하는 코드는 OpenResearch `members/CH`(`rag_hybrid.py`)뿐입니다(JT·SH·SW 없음).
- **필요 조치**: 벡터 검색을 새로 만드는 팀원은 쿼리에 종목(`company_id`)·기준일 조건을 꼭 넣을 것(전체 청크 정렬은 느림). 유튜브 청크는 원문 비공개 출처라 근거 청크로 쓰지 말고 번호·점수·건수만.
