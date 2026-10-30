"""DART 원문 보관 오프라인 테스트: 모의 저장소·모의 DB, 네트워크 없음."""
import io
import zipfile

import pytest

from kic_data import dart_archive, db, storage

MB = 1024 * 1024


class FakeStore:
    def __init__(self, fail_on=None):
        self.objects, self.puts, self.fail_on = {}, [], fail_on

    def size(self, key):
        return len(self.objects[key]) if key in self.objects else None

    def put(self, key, data, content_type="application/zip"):
        if self.fail_on and self.fail_on in key:
            raise OSError("upload failed")
        self.objects[key] = data
        self.puts.append(key)

    def get(self, key):
        return self.objects[key]


class FakeDB:
    """core.disclosure 대상 목록과 ops.dart_archive 를 흉내 내는 메모리 저장소."""

    def __init__(self, cands):
        self.cands = cands  # (rcept_no, company_id, ticker, category, rcept_dt) 최근순
        self.saved = {}

    def install(self, monkeypatch):
        monkeypatch.setattr(db, "archive_candidates", self.candidates)
        monkeypatch.setattr(db, "archive_used_bytes", lambda conn: sum(v[4] for v in self.saved.values()))
        monkeypatch.setattr(db, "archive_avg_bytes", lambda conn: {})
        monkeypatch.setattr(db, "archive_save", self.save)
        monkeypatch.setattr(db, "archive_key", lambda conn, r: self.saved[r][3] if r in self.saved else None)

    def candidates(self, conn, categories, since=None, limit=None):
        out = [c for c in self.cands if c[3] in categories and c[0] not in self.saved and (not since or str(c[4]) >= since)]
        return out[:limit] if limit else out

    def save(self, conn, rcept_no, company_id, ticker, category, key, size):
        self.saved[rcept_no] = (company_id, ticker, category, key, size)


def _rows(n, cat="B"):
    return [(f"2026103000{i:04d}", 1, "005930", cat, f"2026-10-{30 - i % 20:02d}") for i in range(n)]


def _raw(session, rcept_no, size=1000):
    return b"PK" + rcept_no.encode() + b"x" * size


def test_only_a_and_b_and_dry_run_reports_without_writing(monkeypatch):
    fake = FakeDB(_rows(3, "B") + _rows(2, "A") + [("20261030999999", 1, "005930", "I", "2026-10-30")])
    fake.install(monkeypatch)
    store = FakeStore()
    r = dart_archive.run(None, None, store, dry_run=True, fetch_raw=_raw)
    assert r["targets"] == 5 and r["estimate_bytes"] == 3 * 300 * 1024 + 2 * 8 * MB
    assert store.puts == [] and fake.saved == {}


def test_upload_is_idempotent_and_records_meta(monkeypatch):
    fake = FakeDB(_rows(3))
    fake.install(monkeypatch)
    store = FakeStore()
    first = dart_archive.run(None, None, store, fetch_raw=_raw)
    again = dart_archive.run(None, None, store, fetch_raw=_raw)
    assert first["uploaded"] == 3 and first["uploaded_bytes"] > 0 and len(store.objects) == 3
    assert again["targets"] == 0 and again["uploaded"] == 0 and len(store.puts) == 3
    rcept, (cid, ticker, cat, key, size) = next(iter(fake.saved.items()))
    assert key == f"dart/B/{rcept[:4]}/{rcept}.zip" and (cid, ticker, cat) == (1, "005930", "B") and size == len(store.objects[key])


def test_object_already_in_store_only_fills_meta(monkeypatch):
    fake = FakeDB(_rows(1))
    fake.install(monkeypatch)
    rcept = fake.cands[0][0]
    store = FakeStore()
    store.objects[dart_archive.object_key(rcept, "B")] = b"already"
    calls = []
    r = dart_archive.run(None, None, store, fetch_raw=lambda s, rc: calls.append(rc) or b"x")
    assert calls == [] and store.puts == [] and r["uploaded"] == 1 and fake.saved[rcept][4] == len(b"already")


def test_cap_stops_newest_first_and_counts_the_rest(monkeypatch):
    fake = FakeDB(_rows(10))
    fake.install(monkeypatch)
    big = dart_archive.MAX_SINGLE
    r = dart_archive.run(None, None, FakeStore(), cap_gb=(2 * 1000 + big + 10) / 1024 ** 3, fetch_raw=lambda s, rc: b"x" * 1000)
    assert r["uploaded"] > 0 and r["uploaded"] < 10 and r["skipped_cap"] == 10 - r["uploaded"]
    assert set(fake.saved) == {c[0] for c in fake.cands[: r["uploaded"]]}      # 최근 공시부터


def test_failures_are_recorded_and_rest_continues(monkeypatch):
    fake = FakeDB(_rows(4))
    fake.install(monkeypatch)
    bad = fake.cands[1][0]

    def fetch(s, rc):
        if rc == bad:
            raise RuntimeError("[dart] 원문 없음 013")
        return b"PK" + b"x" * 10
    r = dart_archive.run(None, None, FakeStore(), fetch_raw=fetch)
    assert r["uploaded"] == 3 and [f[0] for f in r["failed"]] == [bad] and "원문 없음" in r["failed"][0][1]
    assert bad not in fake.saved
    store = FakeStore(fail_on="/2026")        # 업로드 오류도 같은 방식
    fake2 = FakeDB(_rows(2))
    fake2.install(monkeypatch)
    r2 = dart_archive.run(None, None, store, fetch_raw=fetch)
    assert r2["uploaded"] == 0 and len(r2["failed"]) == 2


def test_no_store_is_skipped_with_reason(monkeypatch):
    FakeDB(_rows(2)).install(monkeypatch)
    r = dart_archive.run(None, None, None, fetch_raw=_raw)
    assert r["skipped"] and r["uploaded"] == 0 and r["targets"] == 2


def test_missing_settings_lists_names_only(monkeypatch):
    for name in (*storage.REQUIRED, *storage.ALIASES.values()):
        monkeypatch.delenv(name, raising=False)
    assert storage.get_store() is None and storage.missing_settings() == list(storage.REQUIRED)
    monkeypatch.setenv("NEON_STORAGE_BUCKET", "b")
    assert "NEON_STORAGE_BUCKET" not in storage.missing_settings()
    monkeypatch.setenv("AWS_ENDPOINT_URL_S3", "https://x")        # AWS_* 이름도 같은 값으로 인정
    assert "NEON_STORAGE_ENDPOINT" not in storage.missing_settings()


def _zip_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("20261030000001.xml", "<DOCUMENT>본문</DOCUMENT>".encode("utf-8"))
    return buf.getvalue()


def test_fetch_files_uses_archive_without_calling_dart(monkeypatch):
    from kic_data.collectors import dart_doc

    fake = FakeDB([])
    fake.install(monkeypatch)
    store = FakeStore()
    key = dart_archive.object_key("20261030000001", "A")
    store.objects[key] = _zip_bytes()
    fake.saved["20261030000001"] = (1, "005930", "A", key, len(store.objects[key]))
    monkeypatch.setattr(dart_doc, "fetch_document", lambda s, rc: pytest.fail("DART 를 부르면 안 됨"))
    files = dart_archive.fetch_files(None, store, None, "20261030000001")
    assert any("본문" in text for text in files.values())


def test_fetch_files_falls_back_to_dart_when_not_archived_or_no_store(monkeypatch):
    from kic_data.collectors import dart_doc

    FakeDB([]).install(monkeypatch)
    monkeypatch.setattr(dart_doc, "fetch_document", lambda s, rc: {"live.xml": rc})
    assert dart_archive.fetch_files(None, FakeStore(), None, "20261030000002") == {"live.xml": "20261030000002"}
    assert dart_archive.fetch_files(None, None, None, "20261030000002") == {"live.xml": "20261030000002"}


def test_fetch_document_still_returns_parsed_files(monkeypatch):
    from kic_data.collectors import dart_doc

    monkeypatch.setattr(dart_doc, "fetch_document_raw", lambda s, rc: _zip_bytes())
    assert any("본문" in t for t in dart_doc.fetch_document(None, "20261030000001").values())
