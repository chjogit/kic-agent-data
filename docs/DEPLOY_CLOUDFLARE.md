# Cloudflare Python Worker 배포 (조회 전용 대시보드)

> 수집은 기존대로 GitHub Actions 가 맡습니다 → [`AUTO_RUN.md`](AUTO_RUN.md).
> 이 문서는 웹 대시보드(`/`, `/quality`)를 Cloudflare Workers 에 올리는 방법입니다.
> Vercel 배포([`DEPLOY_VERCEL.md`](DEPLOY_VERCEL.md))는 롤백용으로 그대로 둡니다.
> 비밀번호·연결 문자열은 이 문서에도, 저장소에도 적지 않습니다. 모두 Cloudflare 쪽에서 직접 입력합니다.

## 1. 구조 요약

- FastAPI 앱(`main.py`)을 그대로 쓰되, Worker 는 **조회 전용**으로만 서빙합니다.
- 진입점은 `cloudflare/src/entry.py`. 요청은 `kic_data/worker_guard.py` 가 먼저 걸러 냅니다.
  - 조회 경로(`/`, `/quality`, `/dashboard`, `/api/health`, `/api/jobs`, `/api/runs`, `/api/status`, `/api/features`, `/api/quality/*`, `/static/kic.css`)의 GET 만 통과하고, 나머지는 전부 405. guard 는 HEAD 도 통과시키지만 FastAPI 의 GET 라우트가 HEAD 를 받지 않아 HEAD 는 결국 405 입니다.
  - `DASHBOARD_PASSWORD` 시크릿이 없으면 `/api/health` 를 뺀 모든 경로가 503 (비밀번호 없이 공개되지 않음).
  - 비밀번호가 있으면 기존 HTTP 기본 인증(`main._require_viewer`)이 확인합니다.
- DB 는 Hyperdrive 바인딩 2개(`HYPERDRIVE_MAIN`, `HYPERDRIVE_OPINION`) + `pg8000` 으로 접속하고, 요청마다 새 연결을 열어 읽기 전용 계정으로 조회합니다. 일부 경로(`/`, `/api/quality/*`, opinion 연결 등 `read_only=True` 를 쓰는 곳)는 트랜잭션도 READ ONLY 로 열고, `/api/jobs`, `/api/runs`, `/api/status`, `/api/features` 는 트랜잭션 설정 없이 읽기 전용 계정의 권한만으로 쓰기를 막습니다.
- 읽기 전용 계정이라 스키마 적용(`jobs.ensure_schema`)은 Worker 에서 건너뜁니다.
- 번들: `scripts/build_worker.py` 가 `kic_data`(수집기 `collectors` 제외), `sql`, `web`, `main.py` 를 `cloudflare/src/` 로 복사합니다. 복사본은 git 에 올리지 않습니다(`.gitignore`).
- 설정 파일: `cloudflare/wrangler.jsonc`(Worker 이름 `kic-agent-data`, `python_workers` 플래그, Hyperdrive 바인딩), `cloudflare/pyproject.toml`(fastapi, pg8000, tzdata).

## 2. 사전 준비: 조회 전용 계정과 Hyperdrive

1. **조회 전용 계정** (이미 만들어져 있어야 합니다)
   - 메인 DB: `kic_readonly` (core·ops·quality·rag 읽기만). 만드는 법은 [`DEPLOY_VERCEL.md`](DEPLOY_VERCEL.md) 의 "대시보드용 읽기 전용 계정".
   - 의견(opinion) DB: `opinion_reader`. 이미 만들어 둔 것으로 가정합니다(생성·GRANT SQL 은 저장소에 없습니다).
   - **`raw` 스키마는 SELECT 권한이 없습니다.** `/api/quality/schema/raw` 는 200 으로 응답하지만 카탈로그만 읽으므로 테이블 목록과 추정 건수만 보이고, 통계가 없는 테이블의 건수는 비어(null) 있을 수 있습니다. 다른 화면·API 는 `raw` 테이블을 읽지 않습니다.
2. **Hyperdrive 설정 2개** (Cloudflare 대시보드 → Workers & Pages → Hyperdrive, 또는 `npx wrangler hyperdrive create`)
   - `kic-main`: 메인 DB 의 `kic_readonly` 연결.
   - `kic-opinion`: opinion DB 의 `opinion_reader` 연결.
   - 접속 호스트는 Neon 의 풀러(`-pooler`)가 아닌 **직접 접속 호스트**를 씁니다. 연결 문자열은 대시보드에 직접 입력하고 어디에도 기록하지 않습니다.
   - `cloudflare/wrangler.jsonc` 의 `hyperdrive` 항목이 이 두 설정의 ID 를 바인딩 이름 `HYPERDRIVE_MAIN`(kic-main), `HYPERDRIVE_OPINION`(kic-opinion)으로 연결합니다. 설정을 새로 만들었다면 ID 를 맞춰 고치세요.
3. **Hyperdrive 캐시**: 기본으로 켜져 있어(기본 약 60초) 수집 직후에도 최신 데이터가 잠시 늦게 보일 수 있습니다. 오류가 아닙니다.

## 3. 배포 (내 컴퓨터에서)

필요한 것: Python 3.12 이상, `uv`, Node.js(`npx`), Cloudflare 계정 로그인(`npx wrangler login`).

```bash
# 1) 저장소 루트에서 번들 복사
python scripts/build_worker.py

# 2) 배포
cd cloudflare
uv run pywrangler deploy
```

### 시크릿 `DASHBOARD_PASSWORD` 등록

대시보드 비밀번호는 코드·`wrangler.jsonc` 에 넣지 않고 시크릿으로만 등록합니다. 값은 긴 임의 문자열을 쓰세요.

```bash
cd cloudflare
npx wrangler secret put DASHBOARD_PASSWORD
```

명령이 값을 물어 오면 직접 입력합니다. 또는 Cloudflare 대시보드 → Workers & Pages → `kic-agent-data` → Settings → Variables and Secrets 에서 Secret 으로 추가해도 됩니다.
시크릿이 없는 동안 Worker 는 503 으로 닫혀 있습니다.

## 4. Workers Builds(Git 연동)로 올릴 때

대시보드 → Workers & Pages → `kic-agent-data` → Settings → Build 에서 저장소를 연결하고 다음처럼 설정합니다.

| 항목 | 값 |
|---|---|
| Root directory | `/cloudflare` |
| 빌드 명령 | **실제 빌드 로그로 확정 필요** |
| 배포 명령 | **실제 빌드 로그로 확정 필요** (로컬은 `uv run pywrangler deploy`) |

주의: `cloudflare/src/` 의 복사본은 git 에 없으므로, **배포 전에 복사 스크립트(`python scripts/build_worker.py`)가 먼저 실행돼야 합니다.**
Root directory 가 `/cloudflare` 이면 스크립트가 저장소 루트에 있으므로 빌드 명령 앞에서 상위 폴더의 스크립트를 부르도록(`python ../scripts/build_worker.py`) 해야 할 수 있습니다.
정확한 명령과 경로는 첫 빌드 로그를 보고 확정하세요. 이 줄 이전의 값들은 추정입니다.

## 5. 배포 후 확인

`<주소>` 는 배포 결과의 `*.workers.dev` 주소입니다.

1. `GET /api/health` → 200 (비밀번호 없이도 응답).
2. 비밀번호 없이 `GET /api/status` → **401**. (503 이면 `DASHBOARD_PASSWORD` 시크릿이 아직 없는 것입니다.)
3. 브라우저에서 `/` 와 `/quality` 를 열고 로그인(아이디는 아무 값이나 입력해도 됩니다. 앱이 확인하지 않고 비밀번호만 `DASHBOARD_PASSWORD` 와 비교합니다)한 뒤 화면과 숫자가 나오는지 확인.
4. 쓰기 경로가 닫혀 있는지: `POST /api/cron/tick` 등은 405.
5. Vercel 배포는 이상이 없을 때까지 지우지 않습니다. 문제가 있으면 Vercel 주소로 돌아가면 됩니다.

## 6. 알려진 한계 / 배포 후 확인

처음 배포라 아직 실환경에서 확인하지 못한 부분이 있습니다. 아래를 배포 직후 점검하세요.

- **동기 엔드포인트를 스레드 없이 실행**: Pyodide(Workers)에는 스레드가 없어, `entry.py` 가 anyio 의 `to_thread.run_sync` 를 패치해 FastAPI 의 동기 엔드포인트를 호출한 자리에서 바로 실행합니다. 배포 후 `/api/jobs` 등을 실제로 열어 동작하는지 확인하세요.
- **비-Python 파일 번들**: `web/*.html`, `kic.css` 같은 파일이 번들에 포함되어 서빙되는지 확인하세요(`/`, `/quality`, `/static/kic.css`). 화면이 비어 있거나 스타일이 없으면 이 부분을 먼저 의심합니다.
- **요청 직렬화**: 동기 DB 연결을 여러 요청이 같이 쓰지 않도록 요청을 락으로 한 줄로 처리합니다. pg8000 의 timeout(30초)은 쿼리(소켓 연산) 하나마다 적용될 뿐 요청 전체의 상한이 아닙니다. `/` 처럼 쿼리를 많이 보내는 요청은 DB 가 느리면 30초보다 오래 락을 잡을 수 있고, 그동안 뒤 요청이 모두 기다립니다.
- **`tzdata` 의존성**: `main.py` 가 `ZoneInfo` 를 쓰기 때문에 `tzdata` 를 의존성에 추가했습니다. 시작 시 import 오류가 나면 가장 먼저 이것을 확인하세요.
- **쓰기 경로는 막혀 있음**: cron·POST·작업 실행 엔드포인트는 405 입니다. 수집을 지금처럼 GitHub Actions(또는 Vercel)에 둘지는 **사용자가 결정할 사항**입니다. 이 문서는 수집 방식을 바꾸지 않습니다.
- **실환경 미검증**: `pg8000` 의 실제 조회와 Hyperdrive 바인딩 속성 접근(`env.HYPERDRIVE_MAIN` 등)은 아직 실제 Cloudflare 에서 확인하지 못했습니다. 실패하면 `asyncpg` 어댑터로 바꾸거나 TypeScript(Hono)로 다시 쓰는 방향으로 대체합니다.
