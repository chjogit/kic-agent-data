# (선택) Vercel 배포: 웹 대시보드 + 자동 수집

> 기본 자동 수집은 서버 없이 GitHub Actions 로 돌립니다 → [`AUTO_RUN.md`](AUTO_RUN.md).
> 이 문서는 대시보드를 웹에 올리고 싶을 때만 보세요. Vercel 에서 tick 을 부르려면 `docs/vercel-tick.yml.example` 을
> `.github/workflows/` 로 옮기고, 이때는 `collect.yml` 을 지워 둘 중 하나만 쓰세요.

이 프로젝트는 그대로 Vercel 에 올릴 수 있는 웹 프로그램(FastAPI, `main.py`)을 포함합니다.
대시보드에서 작업별 상태·실행 기록·수집 현황을 보고, 수집은 시간표에 따라 자동으로 돌아갑니다.

## 1. 동작 방식 (꼭 읽어 주세요)

- Vercel 함수는 한 번 호출에 **최대 300초**만 실행됩니다. 34종목 전체 수집을 한 번에 끝낼 수 없어서,
  수집을 작업 30여 개로 나누고 `/api/cron/tick` 을 부를 때마다 **차례가 된 작업만** 시간 예산(240초) 안에서 실행합니다.
  종목이 많은 작업은 몇 종목씩 나눠서 다음 tick 에 이어 갑니다.
- **Vercel 무료(Hobby) 요금제는 크론이 하루 1회로 제한**됩니다. 매시간 크론(`0 * * * *`)을 넣으면 배포가 실패합니다.
  그래서 기본 설정은 아래와 같습니다.
  - `vercel.json`: 하루 1회(한국 시간 06시) tick (Hobby 에서도 배포됨, 백업용)
  - `docs/vercel-tick.yml.example` 를 `.github/workflows/` 로 옮기면 **GitHub Actions 가 15분마다 tick 호출** → 모든 작업이 매시간 돌아감
- Vercel **Pro** 요금제라면 GitHub Actions 없이 `vercel.json` 의 크론을 `"*/15 * * * *"` 로 바꾸기만 하면 됩니다.
- GitHub 를 쓰지 않으려면 cron-job.org 같은 무료 외부 크론에서 15분마다
  `https://<배포주소>/api/cron/tick?key=<CRON_SECRET>` 을 부르게 해도 같습니다.

### 작업 시간표

| 묶음 | 주기 | 작업 |
|---|---|---|
| 준비 | 매일 | DB 스키마 적용, 종목 등록, DART 고유번호, 업종코드·발행주식수 |
| 과거분 | 1회 | 국내·해외 시세 5년, 지수 5년, ETF 5년, 거시 5년, 공시 1년, 뉴스·블로그·카페 30일, 검색 관심도 1년 |
| 재무 | 매일 | DART·SEC 연간/분기 재무, 분기 단독 값·TTM, Peer Group |
| 수집 | 매시간 | 국내·해외 시세, 지수, ETF, 거시·업황, 공시, 네이버 뉴스, Finnhub 영문 뉴스, 블로그·카페, 검색 관심도, 감성 점수, 언급량 스냅샷, 5개 관점 지표 재계산 |

- 키(환경변수)가 없는 작업은 실패가 아니라 **'키 없음'** 으로 건너뛰고, 키를 넣으면 다음 tick 부터 실행됩니다.
- 시세는 원천이 하루 한 번 갱신되므로(FSC 는 다음 영업일 13시 이후) 매시간 호출해도 새 값은 하루 한 번만 들어옵니다.
  매시간 의미가 있는 것은 뉴스·블로그·카페·언급량·공시입니다.
- 네이버 검색 API 사용량은 34종목 매시간 기준 하루 약 12,000회로, 일 한도 25,000회 안입니다.
- `features.json` 은 파일 대신 **`/api/features`** 에서 같은 내용을 JSON 으로 받을 수 있습니다 (에이전트 입력용).

## 2. 배포 순서

### ① GitHub 에 올리기
1. GitHub 에서 새 저장소를 만듭니다 (예: `kic-agent-data`, 비공개 권장).
2. 이 폴더를 그 저장소에 올립니다. `.env` 는 `.gitignore` 에 있어 올라가지 않습니다.

### ② Vercel 프로젝트 만들기
1. vercel.com → **Add New… → Project** → 위 저장소 Import.
2. Framework Preset 은 자동으로 **FastAPI** 로 잡힙니다 (`main.py` 의 `app`). 다른 설정은 바꾸지 않아도 됩니다.
3. **Environment Variables** 에 아래 값을 넣고 Deploy.

| 변수 | 필수 | 설명 |
|---|---|---|
| `DATABASE_URL` | 필수 | Neon 연결 문자열 (Neon 콘솔 → Connect, 호스트에 `-pooler` 없는 direct 연결) |
| `CRON_SECRET` | 필수 | 아무 긴 문자열 (예: 비밀번호 생성기 32자). tick·수동 실행 인증용 |
| `FSC_API_KEY` | 권장 | 공공데이터포털 일반 인증키(Decoding). 주식시세정보 + **증권상품시세정보(ETF) 활용신청** |
| `DART_API_KEY` | 권장 | OpenDART |
| `SEC_USER_AGENT` | 권장 | `이름 이메일` 형식 (예: `KIC Team team@example.com`) |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | 권장 | 네이버 개발자센터 검색 API |
| `NCP_APIHUB_KEY_ID`, `NCP_APIHUB_KEY` | 선택 | NAVER API HUB 검색 트렌드 |
| `ECOS_API_KEY`, `FRED_API_KEY` | 권장 | 한국은행 ECOS, 미국 FRED |
| `FINNHUB_API_KEY` | 선택 | finnhub.io 무료 키 (해외 종목 영문 뉴스) |
| `DASHBOARD_PASSWORD` | 선택 | 넣으면 대시보드에 비밀번호가 걸립니다 (아이디 칸은 아무거나) |

키는 나중에 넣어도 됩니다. Vercel 에서 환경변수를 바꾼 뒤에는 **Deployments → 최신 배포 → Redeploy** 해야 적용됩니다.

### ③ 자동 실행 연결 (GitHub Actions)
1. GitHub 저장소 → **Settings → Secrets and variables → Actions → New repository secret** 에 두 개를 넣습니다.
   - `KIC_APP_URL`: Vercel 배포 주소 (예: `https://kic-agent-data.vercel.app`, 끝에 `/` 없이)
   - `CRON_SECRET`: Vercel 에 넣은 값과 같게
2. **Actions** 탭 → `kic-tick` → **Run workflow** 로 한 번 실행해 성공(초록)인지 확인합니다.
   이후에는 15분마다 자동 실행됩니다 (GitHub 사정으로 몇 분씩 늦을 수 있음).

### ④ 확인
- 배포 주소를 열면 대시보드가 나옵니다. **지금 tick 실행** 을 누르고 `CRON_SECRET` 을 입력하면 바로 한 번 돕니다.
- 첫 tick 에서 DB 스키마(`sql/001~013`)가 자동 적용됩니다. 과거분(5년치) 작업은 여러 tick 에 걸쳐 1~2시간 안에 끝납니다.
- 작업 줄의 **실행** 버튼은 그 작업만 바로 실행하고, 1회 작업의 **다시** 버튼은 처음부터 다시 하도록 초기화합니다.

## 3. 알아 둘 점

- **리전**: 팀 Neon DB 가 싱가포르(ap-southeast-1)에 있어 `vercel.json` 의 `regions` 를 싱가포르(`sin1`)로 두었습니다. DB 왕복이 가장 짧습니다.
  Neon DB 가 다른 지역(예: 싱가포르)에 있으면 DB 왕복이 느려질 수 있으니 Neon 리전과 가까운 곳으로 바꿔도 됩니다.
- **yfinance(해외 시세·지수)** 는 비공식 라이브러리라 클라우드 서버에서 일시 차단(429)이 날 수 있습니다.
  실패한 종목은 '일부 실패' 로 남고 다음 tick 에서 다시 시도합니다.
- **ETF** 는 같은 공공데이터포털 키를 쓰지만 '금융위원회_증권상품시세정보' 를 따로 활용신청해야 합니다.
  신청 전에는 ETF 작업이 '일부 실패' 로 표시됩니다.
- **Finnhub 뉴스** 는 영문이라 지금의 한국어 감성 사전으로는 채점하지 않습니다 (영문 감성 모델은 모델 결정 후).
- 로컬 PC 의 `scripts/daily_update.bat` 작업 스케줄러와 **동시에 돌리지 마세요**. 둘 다 같은 DB 에 쓰므로 하나만 쓰면 됩니다.
- 로컬에서 웹 확인: `pip install -r requirements-dev.txt` 후 `uvicorn main:app --reload` → http://127.0.0.1:8000

## 4. 수집 범위 확대 (10-25)

- 종목 17 → 34개: 국내 반도체 7개(리노공업, 이오테크닉스, 원익IPS, 솔브레인, 동진쎄미켐, ISC, LX세미콘),
  미국 반도체 10개(QCOM, TXN, LRCX, KLAC, ADI, MRVL, ON, NXPI, MCHP, TER). 반도체 Peer Group 에 모두 포함.
- 국내 ETF 5종 시세·NAV (`core.etf_price_daily`, 괴리율 계산 가능): KODEX 200, KODEX 반도체, TIGER 반도체,
  TIGER 미국필라델피아반도체나스닥, TIGER 반도체TOP10.
- 반도체 업황 지표 (FRED): `IPG3344S` 반도체·전자부품 산업생산지수, `PCU33443344` 생산자물가지수.
- 해외 종목 영문 뉴스 (Finnhub, `source='finnhub_news'`).
- 종목·ETF 는 `kic_data/universe.py` 에서 바꾸면 다음 날 '준비' 작업이 자동으로 등록합니다 (급하면 대시보드에서 수동 실행).
  새로 추가한 종목의 5년 시세가 필요하면 `backfill-kr-prices` / `backfill-us-prices` 의 **다시** 를 누르세요.
