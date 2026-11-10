"""Cloudflare Worker 용 요청 가드 (순수 ASGI 래퍼, 외부 의존성 없음).

fail-closed 원칙:
  1. 허용 목록(GET/HEAD 의 조회 경로)에 없는 요청은 전부 405 — 쓰기·cron·작업 실행 경로가 앱에 남아 있어도 닿지 않음.
  2. DASHBOARD_PASSWORD 가 없거나 비어 있으면 /api/health 를 뺀 모든 경로가 503 (비밀번호 없이 공개되지 않게).
  3. 비밀번호가 있으면 통과시키고, 인증은 기존 main._require_viewer(HTTP 기본 인증)가 맡음.
비밀번호 값은 읽기만 하고 응답·로그에 쓰지 않습니다.
"""
from __future__ import annotations

import json
import os

ALLOWED_METHODS = frozenset({"GET", "HEAD"})
ALLOWED_EXACT = frozenset({
    "/", "/quality", "/dashboard",
    "/api/health", "/api/jobs", "/api/runs", "/api/status", "/api/features",
    "/static/kic.css",
})
ALLOWED_PREFIXES = ("/api/quality/",)
OPEN_PATHS = frozenset({"/api/health"})   # 비밀번호 설정 전에도 응답 (배포 확인용)


def is_allowed(method: str, path: str) -> bool:
    if method.upper() not in ALLOWED_METHODS:
        return False
    if ".." in path or "//" in path or "\\" in path or "%" in path:
        return False   # 경로 우회 시도 차단 (정상 경로에는 쓰이지 않음)
    if path in ALLOWED_EXACT:
        return True
    return path.startswith(ALLOWED_PREFIXES) and len(path) > len("/api/quality/")


async def _json(send, status: int, detail: str, headers: list | None = None) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json; charset=utf-8"),
                            (b"content-length", str(len(body)).encode())] + (headers or [])})
    await send({"type": "http.response.body", "body": body})


def guard(app):
    """ASGI 앱을 가드로 감싼 새 ASGI 앱을 돌려줍니다."""

    async def guarded(scope, receive, send):
        kind = scope["type"]
        if kind == "lifespan":
            return await app(scope, receive, send)
        if kind != "http":   # websocket 등은 쓰지 않음
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return None
        method, path = scope.get("method", ""), scope.get("path", "")
        if not is_allowed(method, path):
            return await _json(send, 405, "허용되지 않는 요청입니다.", [(b"allow", b"GET, HEAD")])
        if path not in OPEN_PATHS and not os.environ.get("DASHBOARD_PASSWORD"):
            return await _json(send, 503, "DASHBOARD_PASSWORD 시크릿을 먼저 설정하세요.")
        return await app(scope, receive, send)

    return guarded
