"""UpstageEmbedder 오프라인 테스트: 모의 세션으로 429 재시도·응답 순서 정렬·차원 오류·키 없음."""
import pytest

from kic_data import rag


class Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body or {}, text

    def json(self):
        return self._body


class Session:
    def __init__(self, *resps):
        self.resps, self.calls = list(resps), []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json))
        return self.resps.pop(0)


def _vec(first, dim=rag.EMBED_DIM):
    return [first] + [0.0] * (dim - 1)


def _embedder(session, retries=3):
    sleeps = []
    emb = rag.UpstageEmbedder(session=session, min_interval=0, retries=retries, sleep=sleeps.append)
    emb.sleeps = sleeps
    return emb


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("UPSTAGE_API_KEY", "test-key")


def test_retries_on_429_then_succeeds():
    ok = Resp(200, {"data": [{"index": 0, "embedding": _vec(1.0)}], "usage": {"total_tokens": 7}})
    s = Session(Resp(429), Resp(503), ok)
    emb = _embedder(s)
    assert emb.encode(["a"])[0][0] == 1.0
    assert len(s.calls) == 3 and emb.sleeps == [2, 4] and emb.tokens == 7


def test_gives_up_after_retries_with_http_error():
    s = Session(*[Resp(429, text="too many")] * 4)
    emb = _embedder(s, retries=3)
    with pytest.raises(RuntimeError, match="HTTP 429"):
        emb.encode(["a"])
    assert len(s.calls) == 4


def test_non_retryable_error_fails_immediately():
    s = Session(Resp(401, text="bad key"))
    with pytest.raises(RuntimeError, match="HTTP 401"):
        _embedder(s).encode(["a"])
    assert len(s.calls) == 1


def test_response_is_sorted_by_index():
    body = {"data": [{"index": 1, "embedding": _vec(2.0)}, {"index": 0, "embedding": _vec(1.0)}]}
    out = _embedder(Session(Resp(200, body))).encode(["첫째", "둘째"])
    assert [v[0] for v in out] == [1.0, 2.0]


def test_dimension_mismatch_raises():
    body = {"data": [{"index": 0, "embedding": _vec(1.0, dim=768)}]}
    with pytest.raises(RuntimeError, match="차원 768"):
        _embedder(Session(Resp(200, body))).encode(["a"])


def test_count_mismatch_raises():
    body = {"data": [{"index": 0, "embedding": _vec(1.0)}]}
    with pytest.raises(RuntimeError, match="개수"):
        _embedder(Session(Resp(200, body))).encode(["a", "b"])


def test_missing_key_and_batch_limit_raise_before_any_call(monkeypatch):
    s = Session()
    monkeypatch.setattr(rag, "env", lambda name, default=None: None)
    with pytest.raises(RuntimeError, match="UPSTAGE_API_KEY"):
        _embedder(s).encode(["a"])
    monkeypatch.setattr(rag, "env", lambda name, default=None: "k")
    with pytest.raises(RuntimeError, match="100개"):
        _embedder(s).encode(["a"] * 101)
    assert s.calls == []


def test_request_uses_passage_model_and_bearer_header():
    s = Session(Resp(200, {"data": [{"index": 0, "embedding": _vec(1.0)}]}))
    _embedder(s).encode(["a"])
    assert s.calls[0][1] == {"model": "solar-embedding-2-passage", "input": ["a"]}
