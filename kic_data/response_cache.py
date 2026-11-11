"""조회 화면용 응답 캐시 (순수 ASGI 래퍼, 외부 의존성 없음).

수집은 2시간마다라 대시보드를 매번 새로 계산할 필요가 없습니다(`/` 와 `/api/status` 는 쿼리 30여 개, 10초 이상).
- 캐시는 '인증을 통과한 GET 요청'에만 내줍니다. 비밀번호가 틀리거나 없으면 캐시를 건드리지 않고 앱으로 넘겨 401 을 받습니다.
- 200 응답만, 오류 안내 상자가 들어간 화면은 제외하고 저장합니다 (DB 오류 화면이 몇 분간 남지 않게).
- 저장소는 이 isolate(프로세스) 메모리입니다. 락·공유 promise 를 쓰지 않습니다 (Workers 는 요청 사이에 promise 를 넘기면 대기 요청을 취소함).
- 같은 시각에 여러 요청이 캐시 없이 들어오면 각자 계산합니다. 정확성에는 문제없고 계산이 중복될 뿐입니다.
"""
from __future__ import annotations

import base64
import hmac
import os
import time

CACHED_EXACT = frozenset({"/", "/dashboard", "/api/jobs", "/api/runs", "/api/status", "/api/features"})
CACHED_PREFIXES = ("/api/quality/",)
ERROR_MARKER = b"<div class='errbox'>"   # main.dashboard 가 DB 오류를 200 화면 안에 담을 때 쓰는 상자
MAX_BODY_BYTES = 2_000_000


def _cacheable_path(path: str) -> bool:
    return path in CACHED_EXACT or path.startswith(CACHED_PREFIXES)


def _authorized(scope) -> bool:
    """main._require_viewer 와 같은 규칙(기본 인증, 아이디 무시, 상수 시간 비교). 비밀번호가 없으면 거부."""
    password = os.environ.get("DASHBOARD_PASSWORD")
    if not password:
        return False
    auth = ""
    for k, v in scope.get("headers") or []:
        if k.lower() == b"authorization":
            auth = v.decode("latin-1")
            break
    if not auth.lower().startswith("basic "):
        return False
    try:
        _, _, given = base64.b64decode(auth[6:]).decode().partition(":")
    except Exception:  # noqa: BLE001
        return False
    return hmac.compare_digest(given.encode(), password.encode())


def _with_header(message: dict, value: bytes) -> dict:
    headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-cache"]
    return {**message, "headers": headers + [(b"x-cache", value)]}


def response_cache(app, ttl: float = 300.0, max_entries: int = 64, clock=time.monotonic):
    store: dict = {}   # (path, query) -> (만료 시각, start 메시지, body)

    async def cached(scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "GET" or not _cacheable_path(scope.get("path", "")) \
                or not _authorized(scope):
            return await app(scope, receive, send)
        key = (scope["path"], scope.get("query_string", b""))
        now = clock()
        hit = store.get(key)
        if hit and hit[0] > now:
            await send(_with_header(hit[1], b"HIT"))
            await send({"type": "http.response.body", "body": hit[2]})
            return None
        store.pop(key, None)

        start, chunks, size, ok = None, [], 0, True

        async def capture(message):
            nonlocal start, size, ok
            if message["type"] == "http.response.start":
                start = message
                await send(_with_header(message, b"MISS"))
                return
            if message["type"] == "http.response.body":
                body = message.get("body", b"")
                size += len(body)
                if size > MAX_BODY_BYTES:
                    ok = False
                else:
                    chunks.append(body)
            await send(message)

        await app(scope, receive, capture)
        body = b"".join(chunks)
        if ok and start and start["status"] == 200 and ERROR_MARKER not in body:
            while len(store) >= max_entries:
                store.pop(next(iter(store)))   # 가장 오래된 것부터 버림
            store[key] = (now + ttl, start, body)
        return None

    return cached
