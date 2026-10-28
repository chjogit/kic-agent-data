// node --test ops/cloudflare-trigger/test.mjs
import assert from "node:assert/strict";
import test from "node:test";
import { dispatch, REPO, WORKFLOW } from "./src/index.js";

test("workflow_dispatch 를 main 으로 요청", async () => {
  let seen;
  const fake = async (url, opts) => { seen = { url, opts }; return new Response(null, { status: 204 }); };
  assert.equal(await dispatch({ GITHUB_TOKEN: "t" }, fake), 204);
  assert.equal(seen.url, `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`);
  assert.equal(seen.opts.method, "POST");
  assert.equal(seen.opts.headers.Authorization, "Bearer t");
  assert.deepEqual(JSON.parse(seen.opts.body), { ref: "main" });
});

test("토큰이 없으면 요청하지 않고 실패", async () => {
  let called = false;
  await assert.rejects(dispatch({}, async () => { called = true; }), /GITHUB_TOKEN/);
  assert.equal(called, false);
});

test("GitHub 오류는 상태 코드와 함께 실패", async () => {
  const fake = async () => new Response('{"message":"Bad credentials"}', { status: 401 });
  await assert.rejects(dispatch({ GITHUB_TOKEN: "x" }, fake), /401.*Bad credentials/);
});
