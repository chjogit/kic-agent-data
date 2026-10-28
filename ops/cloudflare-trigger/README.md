# kic-collect 정시 실행기 (Cloudflare Workers)

GitHub Actions 예약 실행(`cron: "7 * * * *"`)은 GitHub 대기열 때문에 보통 20~40분 늦게 시작합니다.
이 Worker 는 **매시 15분**에 GitHub API 로 `kic-collect` 를 실행 요청(workflow_dispatch)해서, 보통 1분 안에 시작하게 합니다.

- 기존 7분 예약은 비상용으로 그대로 둡니다. Worker 가 한 번 빠져도 수집은 멈추지 않습니다.
- 두 실행이 겹쳐도 `concurrency: kic-collect` 로 차례대로 돌고, 작업마다 마지막 완료 시각을 보고 건너뛰어 중복 수집은 없습니다.
- 공개 URL 은 없습니다(`workers_dev = false`). 예약 실행만 합니다.
- 비밀값은 이 폴더에 없습니다. GitHub 토큰은 Cloudflare 비밀값(`GITHUB_TOKEN`)에만 넣습니다.

## 준비물

- Node.js (설치 확인: `node --version`)
- Cloudflare 계정 (무료 요금제로 충분)
- GitHub 세분화 토큰(fine-grained PAT)
  - GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token
  - Repository access: **Only select repositories → kic-agent-data**
  - Repository permissions: **Actions: Read and write** (나머지는 없음)
  - 만료일: 본선 마감(12/22) 이후로

## 설치 (Windows cmd, 한 줄씩)

저장소 폴더에서:

```
cd ops\cloudflare-trigger
```

```
npx wrangler login
```

```
npx wrangler deploy
```

```
npx wrangler secret put GITHUB_TOKEN
```

마지막 명령에서 토큰을 붙여넣으라고 나오면 GitHub 토큰을 붙여넣습니다 (화면에 보이지 않음).

## 확인

- 다음 15분이 지나면 GitHub → Actions → kic-collect 목록에 **workflow_dispatch** 실행이 생깁니다.
- 실시간 로그:

```
npx wrangler tail
```

- Cloudflare 대시보드 → Workers & Pages → kic-collect-trigger → Logs 에서 실행 기록과 오류(`GitHub 실행 요청 실패 401` 등)를 볼 수 있습니다.

## 바꾸기·끄기

- 시각 변경: `wrangler.toml` 의 `crons` 를 고치고 `npx wrangler deploy`
- 토큰 교체: `npx wrangler secret put GITHUB_TOKEN`
- 끄기: `npx wrangler delete` (GitHub 예약 실행만 남음)

## 테스트

```
node --test test.mjs
```
