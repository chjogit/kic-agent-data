import asyncio
import base64

import pytest

from kic_data import response_cache as rc


def _auth(pw: str, user: str = "x") -> list:
    return [(b"authorization", b"Basic " + base64.b64encode(f"{user}:{pw}".encode()))]


class FakeApp:
    def __init__(self, status=200, chunks=(b"hello",)):
        self.calls = 0
        self.status = status
        self.chunks = chunks

    async def __call__(self, scope, receive, send):
        self.calls += 1
        await send({"type": "http.response.start", "status": self.status, "headers": [(b"content-type", b"text/plain")]})
        for i, c in enumerate(self.chunks):
            await send({"type": "http.response.body", "body": c, "more_body": i < len(self.chunks) - 1})


def call(app, path="/api/status", method="GET", headers=None, query=b""):
    out = []

    async def send(m):
        out.append(m)

    scope = {"type": "http", "method": method, "path": path, "query_string": query, "headers": headers or []}
    asyncio.run(app(scope, None, send))
    start = out[0]
    body = b"".join(m.get("body", b"") for m in out[1:])
    xcache = dict(start["headers"]).get(b"x-cache")
    return start["status"], body, xcache


@pytest.fixture(autouse=True)
def password(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pw")


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def test_second_authorized_get_is_served_from_cache():
    inner = FakeApp()
    app = rc.response_cache(inner)
    assert call(app, headers=_auth("pw")) == (200, b"hello", b"MISS")
    assert call(app, headers=_auth("pw")) == (200, b"hello", b"HIT")
    assert inner.calls == 1


def test_username_is_ignored_like_the_app():
    inner = FakeApp()
    app = rc.response_cache(inner)
    call(app, headers=_auth("pw", user="a"))
    assert call(app, headers=_auth("pw", user="b"))[2] == b"HIT"


def test_unauthenticated_never_gets_cached_content():
    inner = FakeApp()
    app = rc.response_cache(inner)
    call(app, headers=_auth("pw"))          # 캐시 채움
    for headers in (None, _auth("wrong"), [(b"authorization", b"Basic !!!notbase64")], [(b"authorization", b"Bearer pw")]):
        assert call(app, headers=headers)[2] is None   # 캐시 경로를 타지 않음 (x-cache 없음)
    assert inner.calls == 5                              # 전부 앱으로 넘어감 (앱이 401 처리)


def test_no_password_configured_bypasses_cache(monkeypatch):
    monkeypatch.delenv("DASHBOARD_PASSWORD")
    inner = FakeApp()
    app = rc.response_cache(inner)
    call(app, headers=_auth("pw"))
    call(app, headers=_auth("pw"))
    assert inner.calls == 2


def test_entry_expires_after_ttl():
    clock, inner = Clock(), FakeApp()
    app = rc.response_cache(inner, ttl=300, clock=clock)
    call(app, headers=_auth("pw"))
    clock.t += 299
    assert call(app, headers=_auth("pw"))[2] == b"HIT"
    clock.t += 2
    assert call(app, headers=_auth("pw"))[2] == b"MISS"
    assert inner.calls == 2


def test_only_200_is_cached():
    inner = FakeApp(status=503)
    app = rc.response_cache(inner)
    call(app, headers=_auth("pw"))
    call(app, headers=_auth("pw"))
    assert inner.calls == 2


def test_dashboard_error_box_is_not_cached():
    inner = FakeApp(chunks=(b"<html>", b"<div class='errbox'>OperationalError</div>"))
    app = rc.response_cache(inner)
    call(app, path="/", headers=_auth("pw"))
    call(app, path="/", headers=_auth("pw"))
    assert inner.calls == 2


@pytest.mark.parametrize("path,method", [("/api/health", "GET"), ("/static/kic.css", "GET"), ("/api/status", "POST"),
                                         ("/api/status", "HEAD"), ("/api/cron/tick", "GET")])
def test_other_paths_and_methods_bypass(path, method):
    inner = FakeApp()
    app = rc.response_cache(inner)
    call(app, path=path, method=method, headers=_auth("pw"))
    call(app, path=path, method=method, headers=_auth("pw"))
    assert inner.calls == 2


def test_query_string_is_part_of_the_key():
    inner = FakeApp()
    app = rc.response_cache(inner)
    call(app, path="/api/quality/api/x", headers=_auth("pw"), query=b"a=1")
    assert call(app, path="/api/quality/api/x", headers=_auth("pw"), query=b"a=2")[2] == b"MISS"
    assert call(app, path="/api/quality/api/x", headers=_auth("pw"), query=b"a=1")[2] == b"HIT"


def test_multi_chunk_body_is_replayed_whole():
    inner = FakeApp(chunks=(b"ab", b"cd", b"ef"))
    app = rc.response_cache(inner)
    assert call(app, headers=_auth("pw"))[1] == b"abcdef"
    assert call(app, headers=_auth("pw"))[1:] == (b"abcdef", b"HIT")


def test_store_is_bounded_oldest_evicted():
    inner = FakeApp()
    app = rc.response_cache(inner, max_entries=2)
    for q in (b"a", b"b", b"c"):
        call(app, path="/api/quality/api/x", headers=_auth("pw"), query=q)
    assert call(app, path="/api/quality/api/x", headers=_auth("pw"), query=b"a")[2] == b"MISS"   # a 는 밀려남


def test_oversize_body_is_not_cached(monkeypatch):
    monkeypatch.setattr(rc, "MAX_BODY_BYTES", 4)
    inner = FakeApp(chunks=(b"12345",))
    app = rc.response_cache(inner)
    call(app, headers=_auth("pw"))
    call(app, headers=_auth("pw"))
    assert inner.calls == 2
