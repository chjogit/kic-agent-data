"""RAG 청크 적재(rag.ingest) 오프라인 테스트: DB·네트워크 없이 모의 객체로."""
import pytest

from kic_data import db, rag

MB = 1024 * 1024


class FakeEmbedder:
    dim = 1024

    def __init__(self, fail_on=None):
        self.calls = 0
        self.fail_on = fail_on

    def encode(self, texts):
        self.calls += 1
        if self.fail_on and self.calls == self.fail_on:
            raise RuntimeError("API 오류")
        return [[0.1] * 1024 for _ in texts]


class FakeDB:
    """rag.document / rag.chunk 를 흉내 내는 메모리 저장소."""

    def __init__(self, docs, col="halfvec(1024)", size=428 * MB):
        self.docs = docs  # (doc_id, title, summary, source, ticker)
        self.chunks = {}
        self.col, self.size = col, size
        self.limit_args = None

    def install(self, monkeypatch):
        monkeypatch.setattr(db, "chunk_embedding_type", lambda conn: self.col)
        monkeypatch.setattr(db, "database_size_bytes", lambda conn: self.size)
        monkeypatch.setattr(db, "docs_without_chunks", self.docs_without_chunks)
        monkeypatch.setattr(db, "insert_chunks", self.insert_chunks)

    def docs_without_chunks(self, conn, source, limit=None, since=None, until=None):
        self.limit_args = (source, limit, since, until)
        have = {k[0] for k in self.chunks}
        out = [d for d in self.docs if d[3] in source and d[0] not in have]
        return out[:limit] if limit else out

    def insert_chunks(self, conn, rows, vec_type="vector"):
        for doc_id, idx, content, vec in rows:
            self.chunks[(doc_id, idx)] = (content, vec)
        return len(rows)


def _docs(n, source="naver_news"):
    return [(i, f"제목{i}", f"요약{i}", source, "005930") for i in range(1, n + 1)]


def test_ingest_is_idempotent(monkeypatch):
    fake = FakeDB(_docs(5))
    fake.install(monkeypatch)
    first = rag.ingest(None, FakeEmbedder())
    second = rag.ingest(None, FakeEmbedder())
    assert first["written"] == 5 and len(fake.chunks) == 5
    assert second["docs"] == 0 and second["written"] == 0 and len(fake.chunks) == 5


def test_only_target_sources(monkeypatch):
    FakeDB(_docs(1)).install(monkeypatch)
    with pytest.raises(RuntimeError, match="대상이 아닌 소스"):
        rag.ingest(None, FakeEmbedder(), sources=["naver_blog"])


def test_limit_and_period_are_passed_to_query(monkeypatch):
    fake = FakeDB(_docs(5))
    fake.install(monkeypatch)
    r = rag.ingest(None, FakeEmbedder(), sources=["naver_news"], limit=2, since="2026-10-01", until="2026-10-30")
    assert fake.limit_args == (["naver_news"], 2, "2026-10-01", "2026-10-30")
    assert r["docs"] == 2 and r["written"] == 2


def test_stops_without_write_when_over_limit(monkeypatch):
    fake = FakeDB(_docs(5), size=800 * MB - 10 * 1024)
    fake.install(monkeypatch)
    emb = FakeEmbedder()
    r = rag.ingest(None, emb, limit_mb=800)
    assert r["stopped"] is True and r["written"] == 0
    assert emb.calls == 0 and fake.chunks == {}


def test_dry_run_reports_estimate_without_write(monkeypatch):
    fake = FakeDB(_docs(3))
    fake.install(monkeypatch)
    emb = FakeEmbedder()
    r = rag.ingest(None, emb, dry_run=True)
    assert r["docs"] == 3 and r["chunks"] == 3 and r["estimate_bytes"] > 0
    assert emb.calls == 0 and fake.chunks == {}


def test_dim_mismatch_raises_before_any_write(monkeypatch):
    fake = FakeDB(_docs(2), col="halfvec(768)")
    fake.install(monkeypatch)
    with pytest.raises(RuntimeError, match="차원"):
        rag.ingest(None, FakeEmbedder())
    assert fake.chunks == {}


def test_api_failure_is_recorded_and_rest_continues(monkeypatch):
    fake = FakeDB(_docs(4))
    fake.install(monkeypatch)
    r = rag.ingest(None, FakeEmbedder(fail_on=1), batch=2)
    assert [f[0] for f in r["failed"]] == [1, 2]
    assert "API 오류" in r["failed"][0][2]
    assert r["written"] == 2 and sorted(k[0] for k in fake.chunks) == [3, 4]
    # 실패한 문서는 청크가 없으므로 다음 실행에서 다시 시도된다
    again = rag.ingest(None, FakeEmbedder(), batch=2)
    assert again["written"] == 2 and len(fake.chunks) == 4


def test_missing_api_key_records_failures(monkeypatch):
    monkeypatch.delenv("UPSTAGE_API_KEY", raising=False)
    monkeypatch.setattr(rag, "env", lambda name, default=None: None)
    fake = FakeDB(_docs(2))
    fake.install(monkeypatch)
    r = rag.ingest(None, rag.UpstageEmbedder(session=object()))
    assert len(r["failed"]) == 2 and "UPSTAGE_API_KEY" in r["failed"][0][2] and fake.chunks == {}


def test_requests_split_to_api_batch_limit(monkeypatch):
    fake = FakeDB(_docs(250))
    fake.install(monkeypatch)
    sizes = []

    class Emb(FakeEmbedder):
        def encode(self, texts):
            sizes.append(len(texts))
            return super().encode(texts)

    rag.ingest(None, Emb(), batch=250)
    assert max(sizes) <= rag.UPSTAGE_MAX_BATCH and sum(sizes) == 250


def test_parse_vector_type_and_estimate():
    assert rag.parse_vector_type("halfvec(1024)") == ("halfvec", 1024)
    assert rag.parse_vector_type("vector") == ("vector", None)
    with pytest.raises(RuntimeError):
        rag.parse_vector_type("text")
    assert rag.estimate_chunk_bytes(54000, 300) < 400 * MB
