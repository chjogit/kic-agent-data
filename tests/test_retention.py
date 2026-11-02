"""블로그·카페 보관 기한 오프라인 테스트: 메모리 저장소로 집계·삭제·멱등을 확인."""
from datetime import date

from kic_data import cli, db, jobs, retention

TODAY = date(2026, 11, 30)


class FakeDB:
    """rag.document(+doc_sentiment) 와 core.doc_daily_agg 를 흉내 냄. 삭제 시 감성 행도 함께 사라짐(ON DELETE CASCADE)."""

    def __init__(self, docs):
        self.docs = {d[0]: d for d in docs}   # doc_id -> (doc_id, company_id, source, day, score)
        self.agg = {}
        self.applied = 0

    def install(self, monkeypatch):
        monkeypatch.setattr(db, "retention_count", lambda conn, sources, cutoff: len(self._old(sources, cutoff)))
        monkeypatch.setattr(db, "retention_batch", lambda conn, sources, cutoff, n: self._old(sources, cutoff)[:n])
        monkeypatch.setattr(db, "retention_apply", self.apply)

    def _old(self, sources, cutoff):
        return sorted((d for d in self.docs.values() if d[2] in sources and d[3] < cutoff), key=lambda d: d[0])

    def apply(self, conn, agg, ids):
        for k, (n, ns, sm) in agg.items():
            o = self.agg.get(k, (0, 0, 0.0))
            self.agg[k] = (o[0] + n, o[1] + ns, o[2] + sm)
        for i in ids:
            del self.docs[i]
        self.applied += 1


def _docs():
    old, new = date(2026, 10, 20), date(2026, 11, 20)
    return [(1, 7, "naver_blog", old, 0.5), (2, 7, "naver_blog", old, -0.1), (3, 7, "naver_blog", old, None),
            (4, 7, "naver_cafe", old, 0.2), (5, 8, "naver_blog", old, 0.9),
            (6, 7, "naver_blog", new, 0.3),                       # 기한 안
            (7, 7, "naver_news", old, 0.4), (8, 7, "youtube_comment", old, 0.1)]   # 대상 소스가 아님


def test_aggregate_counts_and_sums():
    agg = retention.aggregate([(7, "naver_blog", date(2026, 10, 20), 0.5), (7, "naver_blog", date(2026, 10, 20), -0.1),
                               (7, "naver_blog", date(2026, 10, 20), None)])
    assert agg == {(7, "naver_blog", date(2026, 10, 20)): (3, 2, 0.4)}


def test_cutoff_is_exclusive_of_the_retention_day():
    assert retention.cutoff_date(TODAY, 30) == date(2026, 10, 31)   # 10-31 발행분까지 보관, 10-30 이전은 삭제


def test_purge_deletes_only_blog_and_cafe_past_retention_and_keeps_aggregate(monkeypatch):
    fake = FakeDB(_docs())
    fake.install(monkeypatch)
    r = retention.purge(None, TODAY, 30, batch=2)
    assert r["targets"] == 5 and r["deleted"] == 5 and fake.applied == 3        # 배치 2 → 3번
    assert set(fake.docs) == {6, 7, 8}                                          # 기한 안·뉴스·댓글은 그대로
    assert fake.agg[(7, "naver_blog", date(2026, 10, 20))] == (3, 2, 0.4)
    assert fake.agg[(7, "naver_cafe", date(2026, 10, 20))] == (1, 1, 0.2)
    assert fake.agg[(8, "naver_blog", date(2026, 10, 20))] == (1, 1, 0.9)


def test_second_run_changes_nothing(monkeypatch):
    fake = FakeDB(_docs())
    fake.install(monkeypatch)
    retention.purge(None, TODAY, 30)
    before = dict(fake.agg)
    r = retention.purge(None, TODAY, 30)
    assert r["targets"] == 0 and r["deleted"] == 0 and fake.agg == before


def test_dry_run_writes_nothing_and_reports_estimate(monkeypatch):
    fake = FakeDB(_docs())
    fake.install(monkeypatch)
    r = retention.purge(None, TODAY, 30, dry_run=True)
    assert r["targets"] == 5 and r["deleted"] == 0 and r["est_bytes"] == 5 * retention.ROW_BYTES
    assert len(fake.docs) == 8 and fake.agg == {}


def test_retention_days_is_configurable(monkeypatch):
    monkeypatch.delenv("RETENTION_DAYS", raising=False)
    assert retention.retention_days() == 30
    monkeypatch.setenv("RETENTION_DAYS", "45")
    assert retention.retention_days() == 45 and retention.retention_days(10) == 10
    monkeypatch.setenv("RETENTION_DAYS", "abc")
    assert retention.retention_days() == 30


def test_registered_as_daily_job_with_existing_command():
    job = next(j for j in jobs.JOBS if j.name == "purge-opinion")
    assert job.every == jobs.DAY and job.needs == jobs.DB and hasattr(cli, job.command)
    ns = job.namespace()
    assert ns.days is None and ns.dry_run is False
