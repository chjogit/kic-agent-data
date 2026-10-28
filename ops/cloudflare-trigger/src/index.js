// 매시 15분 GitHub Actions kic-collect 를 workflow_dispatch 로 실행합니다.
// GitHub 의 schedule 은 대기열 때문에 20~40분 늦게 시작하지만, workflow_dispatch 요청은 보통 1분 안에 시작합니다.
// 기존 cron("7 * * * *") 은 비상용으로 그대로 둡니다. 두 실행이 겹쳐도 concurrency 로 차례대로 돌고,
// 작업마다 마지막 완료 시각(ops.job_state)을 보고 건너뛰어 중복 수집은 생기지 않습니다.

export const REPO = "chjogit/kic-agent-data";
export const WORKFLOW = "collect.yml";

export async function dispatch(env, fetchImpl = fetch) {
  if (!env.GITHUB_TOKEN) {
    throw new Error("GITHUB_TOKEN 비밀값이 없습니다 (npx wrangler secret put GITHUB_TOKEN)");
  }
  const res = await fetchImpl(`https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "kic-collect-trigger",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  if (!res.ok) {
    const text = (await res.text()).slice(0, 300);
    throw new Error(`GitHub 실행 요청 실패 ${res.status}: ${text}`); // 실패로 기록돼 대시보드에 보임
  }
  return res.status;
}

export default {
  async scheduled(controller, env) {
    const status = await dispatch(env);
    console.log(`kic-collect 실행 요청 완료 (HTTP ${status}, 예약 ${new Date(controller.scheduledTime).toISOString()})`);
  },
};
