# kic-opinion 연결 안내 (팀원용)

> 초안 작성: PM, 2026-11-09. 이 문서는 kic-agent-data 저장소 `docs/KIC_OPINION_ACCESS.md` 로 옮겨 팀에 공유합니다(#78).
> **이 문서와 저장소·이슈·채팅방 공지에는 접속 주소·비밀번호·키를 절대 적지 않습니다.** 접속 정보는 관리자(CH)가 한 사람씩 따로 전달합니다.

## 1. kic-opinion 이 무엇인가
메인 DB(1GB 한도) 용량을 지키려고 만든 **두 번째 Neon 프로젝트**입니다. 두 가지가 들어 있습니다.

| 구분 | 무엇 | 크기 |
|---|---|---|
| **DB (PostgreSQL)** | 블로그·카페 문서 정보, 문서별 감성 점수, 종목·출처·일자별 집계 | 수시로 변함 (`python -m kic_data.cli status` 로 확인) |
| **Object Storage (S3 호환)** | DART 공시 원문, 감성 집계 파일(하루 4회), DART 전체 재무제표 Parquet(수집 예정) | — |

뉴스·유튜브 댓글·시세·수급·재무(주요 계정)·RAG 청크는 **메인 DB** 에 있습니다(기존 `DATABASE_URL`).

## 2. 무엇이 들어 있나

### 2-1. DB 표
| 표 | 행(약) | 열 | 설명 |
|---|---|---|---|
| `rag.document` | 수십만 | doc_id, company_id, source, title, url, published_at, summary, created_at | 네이버 블로그(`naver_blog`)·카페(`naver_cafe`) 문서 정보. 본문 전체는 없고 요약만 |
| `core.doc_sentiment` | 수십만 | doc_id, score, label, method, scored_at | 문서별 감성 점수·라벨(긍정·중립·부정·무관)·방식 |
| `core.doc_daily_agg` | 수천 | company_id, source, d, n_docs, n_scored, score_sum, updated_at | 종목·출처·일자별 문서 수·감성 합(보관 기간이 지나 지운 문서도 집계로 남음) |
| `core.company` | 230종목 | company_id, market, ticker, name | 종목 목록(메인 DB 와 같은 company_id) |

- 보관: 블로그·카페 문서는 **14일 보관** 원칙입니다(집계 표는 남음).
- 감성 점수의 한계: 키워드 사전 방식, 사람 라벨 600건 중 감성 대상 440건 기준 **정확도 56.6%**(긍정을 적게 잡고 중립으로 쏠림). 판단 근거로 쓸 때 이 점을 함께 적어 주세요.

### 2-2. Object Storage 경로
| 접두사 | 내용 | 갱신 |
|---|---|---|
| `dart/{분류}/{연도}/{접수번호}.zip` | DART 공시 원문(사업보고서 등 A·B 분류) | 수집 때 추가 |
| `sentiment/latest/` (첫 업로드 승인 전에는 아직 없음) | 감성 집계 — 종목 × 출처 × 기간 창(14·7·3·1일). `window=14d.json`·`.csv` 등 + `_meta.json` | 매일 09·14·18·21시(KST) **덮어씀**. `_meta.json` 이 새 것이면 나머지도 모두 새 것 |
| `financials/` | DART 전체 재무제표 5년치(연결·별도, 숫자만) Parquet | 수집 후 생김(#77) |

## 3. 접속 정보 받기
1. 관리자(CH)에게 "kic-opinion 읽기 권한"을 요청합니다.
2. 관리자가 **읽기 전용** 정보 두 가지를 개인 메시지로 전달합니다.
   - DB 읽기 전용 접속 문자열 → 환경변수 `OPINION_DATABASE_URL`
   - Object Storage 읽기 전용 키 → 환경변수 `NEON_STORAGE_ENDPOINT`, `NEON_STORAGE_BUCKET`, `NEON_STORAGE_ACCESS_KEY_ID`, `NEON_STORAGE_SECRET_ACCESS_KEY`, `NEON_STORAGE_REGION`
     (같은 값을 `AWS_ENDPOINT_URL_S3`·`AWS_ACCESS_KEY_ID`·`AWS_SECRET_ACCESS_KEY`·`AWS_REGION` 이름으로 써도 됩니다.)
3. 받은 값은 **자기 PC 의 `.env` 에만** 넣습니다. `.env` 는 git 에 올리지 않습니다(`.gitignore` 확인).

## 4. 연결해 보기

### 4-1. DB (Python)
```python
import os
import psycopg
from dotenv import load_dotenv

load_dotenv()
with psycopg.connect(os.environ["OPINION_DATABASE_URL"],
                     options="-c default_transaction_read_only=on") as conn:   # 읽기 전용 세션
    rows = conn.execute("""
        select c.name, a.source, a.d, a.n_docs, a.score_sum / nullif(a.n_scored, 0) as avg_score
        from core.doc_daily_agg a join core.company c using (company_id)
        where c.name = %s and a.d >= current_date - 7
        order by a.d""", ["삼성전자"]).fetchall()
print(rows)
```

### 4-2. Object Storage (Python, boto3)
```python
import json
import os
import boto3
from dotenv import load_dotenv

load_dotenv()
s3 = boto3.client("s3",
                  endpoint_url=os.environ["NEON_STORAGE_ENDPOINT"],
                  aws_access_key_id=os.environ["NEON_STORAGE_ACCESS_KEY_ID"],
                  aws_secret_access_key=os.environ["NEON_STORAGE_SECRET_ACCESS_KEY"],
                  region_name=os.environ.get("NEON_STORAGE_REGION", "ap-southeast-1"))
bucket = os.environ["NEON_STORAGE_BUCKET"]

meta = json.loads(s3.get_object(Bucket=bucket, Key="sentiment/latest/_meta.json")["Body"].read())
print(meta["as_of"] if "as_of" in meta else meta)          # 기준 시각
for o in s3.list_objects_v2(Bucket=bucket, Prefix="sentiment/latest/").get("Contents", []):
    print(o["Key"], o["Size"])
```

### 4-3. 재무제표 Parquet 읽기 (수집 후)
```python
import io
import pandas as pd
# 위 s3·bucket 그대로 사용
key = "financials/fs/year=2024/report=11011/part-0.parquet"     # 실제 파일 이름은 financials/_meta.json 참고
df = pd.read_parquet(io.BytesIO(s3.get_object(Bucket=bucket, Key=key)["Body"].read()))
print(df.head())
```
필요 패키지: `pip install psycopg[binary] python-dotenv boto3 pandas pyarrow`

## 5. 지켜 주세요
- **읽기만** 합니다. 표를 바꾸거나 파일을 올리지 않습니다(읽기 전용 권한이라 막혀 있지만, 소유자 권한을 받은 경우에도 마찬가지).
- **블로그·카페·유튜브 원문·주소·작성자**를 보고서·화면·발표 자료에 그대로 넣지 않습니다. 숫자(건수·감성 집계)와 문서 번호만 씁니다.
- 기준일(as_of)을 지킵니다 — 분석 기준일 이후 문서·파일을 근거로 쓰지 않습니다.
- 큰 조회(43만 행 전체 등)는 피하고 종목·기간을 걸러 주세요(무료 플랜 계산 시간 한도).
- 접속 정보가 새었거나 더 필요 없으면 관리자에게 알려 주세요(키 폐기·교체).

## 6. 문의
- 데이터·권한: CH(데이터 수집 담당)
- 구성 변경 기록: kic-agent-data `docs/DATA_CHANGES.md`

## 7. 관리자용 (CH): 읽기 전용 접속 만들기
이 절에는 비밀번호·키를 적지 않습니다. 실행은 관리자가 직접 하거나 승인 후에만 합니다.
- **DB**: [`docs/sql/opinion_readonly_role.sql`](sql/opinion_readonly_role.sql) 의 `<비밀번호>` 자리를 채워 Neon SQL 편집기(kic-opinion 프로젝트)에서 실행합니다. `core`·`rag` 스키마 SELECT 만 주고, 앞으로 생기는 표에도 기본 권한을 겁니다. 만든 뒤 접속 문자열은 팀원에게 개인 메시지로 전달합니다.
- **Object Storage**: Neon 콘솔 → kic-opinion 프로젝트 → Storage → 자격 증명(credential) 생성 시 권한을 `storage:read` 로 고릅니다. 팀원별로 하나씩 만들면 폐기가 쉽습니다. 키는 한 번만 보이므로 바로 개인 메시지로 전달합니다.
- 연결 확인은 4-1·4-2 예제를 읽기 전용 계정으로 실행해 봅니다(값은 화면·로그에 남기지 않습니다).
