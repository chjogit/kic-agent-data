# 자동 수집: GitHub Actions (서버 없이, 무료) — 매시간, 공개 저장소, KOSPI 200

GitHub 저장소에 코드를 올려 두면 GitHub 가 매시간 수집을 대신 실행합니다. PC 를 켜 둘 필요도, Vercel 같은 서버도 필요 없습니다.
수집 결과는 지금처럼 Neon DB 에 쌓입니다.

## 1. 동작 방식

- `.github/workflows/collect.yml` 이 매시 7분(UTC 기준) `python -m kic_data.cli tick` 을 실행합니다.
  GitHub 예약 실행은 보통 20~40분 늦게 시작해서, cron-job.org 가 매시 15분(KST)에 같은 워크플로를 실행 요청합니다
  (보통 1분 안에 시작). 7분 예약은 cron-job.org 가 빠질 때를 대비한 비상용입니다. 설정은 아래 '정시 실행(cron-job.org)'.
- `tick` 은 `kic_data/jobs.py` 의 시간표에서 **차례가 된 작업만** 실행합니다 (한 번 실행에 최대 20분, 남은 것은 다음 실행으로).
- 원천 API 별로 줄(lane)을 나눠 병렬로 실행합니다: 준비 → `dart`(DART·SEC) · `public`(공공데이터포털) · `naver`(검색 API) · `naver_etc`(데이터랩·네이버 증권) · `global`(yfinance·ECOS·FRED·Finnhub·유튜브) 동시 → 계산(감성·TTM·Peer·지표). 같은 API 는 한 줄에만 있어 동시 호출이 늘지 않고, 20분 예산은 줄마다 따로 적용됩니다. 한 줄만 돌리려면 `python -m kic_data.cli tick --lane dart`.
- 대상: **KOSPI 200 전 종목**(매일 네이버 증권 공개 목록으로 동기화, `index_tags` 에 `KOSPI200`) + `universe.py` 의 반도체 비교군·해외 종목.

| 묶음 | 주기 | 작업 |
|---|---|---|
| 준비 | 매일·매주 | DB 스키마, 종목 등록, KOSPI 200 종목 마스터(매일), DART 고유번호, 업종(KSIC→KRX 업종)·발행주식수(매주) |
| 과거분 | 1회 | 국내·해외 시세 5년, 지수 5년, ETF 5년, 거시 5년, 공시 1년, 뉴스·블로그·카페 30일, 검색 관심도 1년, 국내 연간 재무 5년·분기 2년 |
| 수집 | 매시간 | 국내 시세(전 종목 조회), 해외 시세, 지수, ETF, 거시·업황, 공시(시장 전체 조회), Finnhub 영문 뉴스, 감성 점수, 5개 관점 지표 |
| 수집 | 6시간 | 네이버 뉴스, 블로그·카페, 언급량 스냅샷, 검색 관심도 (종목마다 부르는 API 라 하루 한도 때문에) |
| 수집 | 매일 | 유튜브(국내 시총 상위 40 + 해외 종목), 관세청 수출입 |
| 재무 | 매일 | 국내 연간(최근 2년)·분기(올해) 재무(DART 다중회사 100개사씩), SEC 연간/분기, 분기 단독 값·TTM, Peer Group |

- 종목이 200개가 넘어도 시세·공시·재무는 **전 종목/다중회사 조회**라 호출이 몇 번이면 끝납니다.
  - 국내 시세: 기간 안의 전 종목 시세를 한 번에 받아(10일 ≈ 2만 행) 대상 종목만 저장, 상장주식수도 함께 갱신.
  - 국내 재무: OpenDART `fnlttMultiAcnt`(주요계정)로 100개사씩. 매출액·영업이익·당기순이익·자산·부채·자본총계를
    단일회사 API 와 같은 계정 ID 로 저장합니다. (현금흐름표 등 세부 계정이 필요하면 `collect-dart` 로 따로 받기)
  - 국내 공시: 유가증권·코스닥 전체 공시 목록(3일)에서 대상 종목만.
- KOSPI 200 에 새 종목이 들어오면 `sync-kospi200` 이 종목별 과거분 작업을 처음부터 다시 하게 되돌립니다.
- 업종: KRX 종목별 업종 배정표는 KRX 정보데이터시스템(로그인 필요)에만 있어, DART 기업개황의 KSIC 코드를 KRX 업종 26개로
  옮겨 씁니다(`kic_data/krx_sector.py`, 지주회사는 '기타금융'). KRX 와 다르게 들어간 종목은 `OVERRIDES` 에 고칩니다.
  Peer Group 은 같은 KRX 업종의 다른 KOSPI 200 종목(시총 상위 15개)입니다(`method = 'krx_sector:<업종>'`).
- 키가 없는 작업은 실패가 아니라 **'건너뜀'** 으로 기록하고, 키를 넣으면 다음 실행부터 돌아갑니다.
- 작업 상태와 실행 기록은 DB(`ops.job_state`, `ops.job_run`)에 남고, 실행마다 **Actions 실행 요약 화면에 상태표**가 나옵니다.
- 시세는 원천이 하루 한 번 갱신되므로(FSC 는 다음 영업일 13시 이후) 새 값은 하루 한 번만 들어옵니다.

## 2. 사용량과 API 한도

- 이 저장소는 **공개(public)** 라 Actions 시간이 무제한입니다. 키는 Secrets 에 있어 노출되지 않고 코드와 실행 기록은 누구나 볼 수 있습니다.
  비공개로 돌리면 월 2,000분 한도라 KOSPI 200 규모에서는 cron 을 6~8시간 간격으로 줄여야 합니다.
- 네이버 검색 API 하루 25,000회: 뉴스·블로그·카페·언급량 6시간 주기 ≈ 하루 8~9천 회.
- 네이버 데이터랩 하루 1,000회: 검색 관심도 6시간 주기 ≈ 하루 230회 (한 번에 4종목 + 기준어).
- 유튜브 하루 10,000단위: 검색 1회 100단위 × 약 57종목 ≈ 5,700 + 댓글 수백.

## 3. 설정 순서

1. GitHub 에서 새 저장소를 만들고 이 폴더를 올립니다.
2. 저장소 **Settings → Secrets and variables → Actions → New repository secret** 에 키를 하나씩 넣습니다.

| 이름 | 필수 | 설명 |
|---|---|---|
| `DATABASE_URL` | 필수 | Neon 연결 문자열. PC 의 `.env` 에 있는 값을 그대로 붙여 넣으면 됩니다 (풀러 주소도 동작) |
| `FSC_API_KEY` | 권장 | 공공데이터포털 일반 인증키(Decoding). 주식시세정보 + 증권상품시세정보(ETF) 활용신청 |
| `DART_API_KEY` | 권장 | OpenDART |
| `SEC_USER_AGENT` | 권장 | `이름 이메일` 형식 (예: `KIC Team team@example.com`) |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | 권장 | 네이버 개발자센터 검색 API |
| `NCP_APIHUB_KEY_ID`, `NCP_APIHUB_KEY` | 선택 | NAVER API HUB 검색 트렌드 |
| `ECOS_API_KEY`, `FRED_API_KEY` | 권장 | 한국은행 ECOS, 미국 FRED |
| `FINNHUB_API_KEY` | 선택 | finnhub.io 무료 키 (해외 종목 영문 뉴스) |

3. **Actions** 탭 → `kic-collect` → **Run workflow** 로 한 번 실행해 봅니다. 초록색이면 성공이고,
   실행을 눌러 들어가면 아래쪽 Summary 에 작업별 상태표가 보입니다. 이후에는 2시간마다 자동 실행됩니다.
4. 과거분(5년치)과 첫 재무는 처음 서너 번 실행에 걸쳐 채워집니다 (한 번 실행에 최대 20분). 그 뒤 평소 실행은 몇 분이면 끝납니다.
5. ETF 는 공공데이터포털에서 **'금융위원회_증권상품시세정보'** 를 따로 활용신청해야 합니다 (같은 키 사용). 신청 전에는 ETF 작업이 HTTP 403 '일부 실패' 로 남습니다.

`Run workflow` 의 `only` 칸에 `준비`, `과거분(1회)`, `news` 처럼 묶음이나 작업 이름을 넣으면 그것만 실행합니다.

## 4. 알아 둘 점

- GitHub 예약 실행은 몇 분에서 수십 분까지 늦어질 수 있고, **저장소에 60일 동안 커밋이 없으면 예약 실행이 멈춥니다**
  (Actions 탭에서 다시 켜면 됨).
- GitHub Actions 서버는 미국에 있습니다. 국내 공공 API 가 해외 접속을 막는 경우가 생기면 그 작업만 '일부 실패' 로 남습니다.
  첫 실행 결과표에서 FSC·DART·ECOS 가 정상인지 확인해 주세요.
- yfinance(해외 시세·지수)는 비공식이라 일시 차단이 날 수 있고, 실패한 종목은 다음 실행에서 다시 시도합니다.
- **PC 의 작업 스케줄러(`kic_daily_update`, 장중 펄스 `pulse.bat`)는 GitHub Actions 첫 실행이 성공하면 꺼 주세요.**
  같은 DB 에 같은 데이터를 쓰므로 하나만 돌리면 됩니다. 끄기: `schtasks /Change /TN "작업이름" /DISABLE`
- 대시보드 화면이 필요하면 PC 에서 `.env` 를 채우고 `pip install -r requirements-dev.txt` → `uvicorn main:app` →
  http://127.0.0.1:8000 을 열면 같은 DB 의 작업 상태를 볼 수 있습니다. 서버에 올리고 싶을 때는 `docs/DEPLOY_VERCEL.md` 참고.

## 정시 실행 (cron-job.org)

GitHub 예약(schedule)은 대기열 때문에 늦지만, 실행 요청(workflow_dispatch)은 바로 시작합니다.
그래서 외부 타이머 cron-job.org 가 매시 15분에 GitHub API 로 `kic-collect` 실행을 요청합니다.
설정은 CH 계정의 cron-job.org 화면에 있습니다 (저장소에는 코드 없음).

- 두 실행이 겹쳐도 `concurrency: kic-collect` 로 차례대로 돌고, 작업마다 `ops.job_state` 의 마지막 완료 시각을 보고
  건너뛰어 중복 수집은 없습니다. 같은 시간의 두 번째 실행은 할 일이 거의 없어 1~2분 만에 끝납니다.
- cron-job.org 가 멈추거나 토큰이 만료돼도 7분 예약이 이어 받습니다 (늦게 시작할 뿐 수집은 계속).

### 1. GitHub 토큰 (세분화 토큰)

GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token

- Repository access: Only select repositories → `kic-agent-data`
- Repository permissions: **Actions: Read and write** (다른 권한 없음)
- 만료일: 정기 수집을 끝낼 날 (만료되면 정시 실행만 멈추고 7분 예약은 남음)

토큰 값은 cron-job.org 화면에만 넣고 문서·코드·대화에 적지 않습니다.

### 2. cron-job.org 작업

Create cronjob:

| 항목 | 값 |
| --- | --- |
| URL | `https://api.github.com/repos/chjogit/kic-agent-data/actions/workflows/collect.yml/dispatches` |
| 실행 시각 | 사용자 지정: 매시 15분, 시간대 Asia/Seoul |
| 요청 방식 (고급) | POST |
| 요청 본문 (고급) | `{"ref":"main"}` |
| 헤더 (고급) | `Authorization: Bearer <토큰>`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`, `Content-Type: application/json` |
| 알림 | 실행 실패 시 메일 |

### 3. 확인

- cron-job.org 의 테스트 실행 → 응답 204 (또는 200)
- GitHub → Actions → kic-collect 목록에 이벤트가 `workflow_dispatch` 인 실행이 생김
- 401: 토큰 오류·만료 / 403: 토큰 권한에 Actions 쓰기 없음 / 404: 토큰의 저장소 선택에 kic-agent-data 없음

### 끄기

cron-job.org 에서 작업을 끄거나 지우면 됩니다 (7분 예약만 남음). 정기 수집 전체를 멈추려면 `collect.yml` 의 `schedule` 도 지웁니다.

