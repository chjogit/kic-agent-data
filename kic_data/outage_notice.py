"""DB 접속 불가(한도 초과 포함)를 DB 없이 GitHub Issue 1개로 알립니다 (#86).

배경: 11/15 14시~11/16 12시 Neon 전송량 한도 초과로 접속이 막혔을 때, 수집·알림·내보내기가 모두 접속 단계에서 죽어
실패를 DB 에 기록하지 못해 `/quality`·기존 알림(alert-sync)이 조용했습니다.

- 워크플로가 실패한 뒤 이 모듈이 DB 에 직접 접속해 보고(probe), 접속이 안 될 때만 이슈를 엽니다(다른 이유의 실패면 아무것도 안 함).
- 이슈는 항상 1개: 열려 있으면 댓글만 붙이고, 원인이 '한도 초과'로 드러나면 제목만 바꿉니다. 접속이 돌아오면 닫습니다.
- 비밀값·접속 문자열·호스트·IP 는 이슈에 쓰지 않습니다. 오류 문구는 종류('한도 초과'/'기타')로만 분류하고 버립니다.
- 기존 alert-sync(label kic-alert, 제목 '[KIC 알림] ')와 겹치지 않게 label `kic-outage`·제목 '[KIC 장애] ' 를 씁니다.
- 어떤 경우에도 예외·실패 종료 코드를 내지 않습니다(이미 실패한 워크플로를 더 꼬이지 않게).
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone

from .alerts import GitHubIssues
from .config import env

OUTAGE_MARK = "kic-outage"
KEY_TAG = "<!-- kic-outage:db-unreachable -->"
TITLE = "[KIC 장애] DB 접속 불가"
TITLE_QUOTA = "[KIC 장애] DB 접속 불가 — 한도 초과"
PROJECT_ENVS = (("메인", "DATABASE_URL"), ("kic-opinion", "OPINION_DATABASE_URL"))


class OutageIssues(GitHubIssues):
    """GitHubIssues 와 같은 호출을 쓰되 label 만 kic-outage 로 (alert-sync 가 건드리지 않게)."""

    def list_open(self) -> list[dict]:
        return self._call("get", f"/issues?labels={OUTAGE_MARK}&state=open&per_page=100")

    def create(self, title: str, body: str) -> dict:
        return self._call("post", "/issues", json={"title": title, "body": body, "labels": [OUTAGE_MARK]})

    def update_title(self, number: int, title: str) -> None:
        self._call("patch", f"/issues/{number}", json={"title": title})


def classify(text: str) -> str:
    """오류 문구 → 'quota'(한도 초과) | 'other'. 문구 자체는 저장하지 않는다."""
    return "quota" if re.search(r"exceeded the quota", text or "", re.I) else "other"


def probe(urls: dict[str, str | None], connect=None) -> dict[str, str | None]:
    """프로젝트별 접속 시도. 결과: 접속 성공 → None, 실패 → 'quota' | 'other'. URL 이 없는 프로젝트는 건너뜀."""
    if connect is None:
        def connect(url):
            import psycopg

            return psycopg.connect(url, connect_timeout=15)
    out: dict[str, str | None] = {}
    for name, url in urls.items():
        if not url:
            continue
        try:
            conn = connect(url)
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            out[name] = None
        except Exception as exc:  # noqa: BLE001
            out[name] = classify(str(exc))
    return out


def _down(result: dict[str, str | None]) -> dict[str, str]:
    return {k: v for k, v in result.items() if v}


def _kind_text(kind: str) -> str:
    return "한도 초과(exceeded the quota)" if kind == "quota" else "접속 실패(원인 미확인)"


def build_body(result: dict[str, str | None], workflow: str, run_url: str, now: datetime) -> str:
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    lines = [KEY_TAG, f"**error** — DB 에 접속할 수 없어 `{workflow}` 가 실패했습니다 ({stamp}).", ""]
    lines += [f"- {name}: {_kind_text(kind)}" for name, kind in _down(result).items()]
    lines += ["", f"- 실행 기록: {run_url}",
              "- 한도 초과면 Neon 콘솔에서 요금제·사용량을 확인하세요. 접속이 돌아오면 이 이슈는 자동으로 닫힙니다.",
              "- 이 이슈는 DB 없이 만들어집니다(비밀값·접속 주소는 적지 않음)."]
    return "\n".join(lines)


def _find(client) -> dict | None:
    for iss in client.list_open():
        if KEY_TAG in (iss.get("body") or ""):
            return iss
    return None


def notify_failure(client, result: dict[str, str | None], workflow: str, run_url: str, now: datetime) -> str:
    """접속이 실제로 안 될 때만 이슈 1개를 열거나 댓글을 붙인다. 반환: db_ok | opened | commented."""
    down = _down(result)
    if not down:
        return "db_ok"
    quota = "quota" in down.values()
    title = TITLE_QUOTA if quota else TITLE
    iss = _find(client)
    if iss is None:
        client.create(title, build_body(result, workflow, run_url, now))
        return "opened"
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    projects = ", ".join(f"{n}({_kind_text(k)})" for n, k in down.items())
    client.comment(iss["number"], f"{stamp} 다시 실패 — `{workflow}`: {projects} · {run_url}")
    if quota and iss.get("title") != TITLE_QUOTA:
        client.update_title(iss["number"], TITLE_QUOTA)
    return "commented"


def close_if_recovered(client, result: dict[str, str | None], now: datetime) -> str:
    """접속한 모든 프로젝트가 정상이면 열린 장애 이슈를 닫는다. 반환: none | still_down | closed."""
    iss = _find(client)
    if iss is None:
        return "none"
    if _down(result) or not result:
        return "still_down"
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    client.comment(iss["number"], f"{stamp} 해결됨 — DB 접속이 정상으로 돌아와 이슈를 닫습니다.")
    client.close(iss["number"])
    return "closed"


def _client() -> OutageIssues | None:
    token, repo = env("GITHUB_TOKEN"), env("GITHUB_REPOSITORY")
    return OutageIssues(token, repo) if token and repo else None


def _run_url() -> str:
    server, repo, run = env("GITHUB_SERVER_URL") or "https://github.com", env("GITHUB_REPOSITORY"), env("GITHUB_RUN_ID")
    return f"{server}/{repo}/actions/runs/{run}" if repo and run else "(실행 기록 주소 없음)"


def main(argv: list[str] | None = None) -> int:
    """사용: python -m kic_data.outage_notice failure --workflow kic-collect | recovered. 항상 0 으로 끝난다."""
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else ""
    workflow = argv[argv.index("--workflow") + 1] if "--workflow" in argv and argv.index("--workflow") + 1 < len(argv) else "워크플로"
    now = datetime.now(timezone.utc)
    try:
        client = _client()
        if client is None:
            print("[장애 알림] GITHUB_TOKEN·GITHUB_REPOSITORY 없음 → 건너뜀")
            return 0
        result = probe({name: env(key) for name, key in PROJECT_ENVS})
        summary = ", ".join(f"{n}={'정상' if not k else _kind_text(k)}" for n, k in result.items()) or "접속 정보 없음"
        print(f"[장애 알림] 접속 점검: {summary}")
        if cmd == "failure":
            print(f"[장애 알림] {notify_failure(client, result, workflow, _run_url(), now)}")
        elif cmd == "recovered":
            print(f"[장애 알림] {close_if_recovered(client, result, now)}")
        else:
            print("[장애 알림] 사용법: failure --workflow NAME | recovered")
    except Exception as exc:  # noqa: BLE001  (GitHub API·네트워크 오류도 워크플로를 더 실패시키지 않음)
        print(f"[장애 알림] 오류 {type(exc).__name__} → 건너뜀")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
