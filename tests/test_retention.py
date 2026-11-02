"""문서 보관 기한(14일) 오프라인 테스트: 메모리 저장소로 집계·삭제·멱등·제외 소스·청크 정리를 확인."""
from datetime import date
from types import SimpleNamespace

from kic_data import cli, db, jobs, retention

TODAY = date(2026, 11, 30)
OLD, NEW = date(2026, 11, 10), date(2026, 11, 20)       # 14일 기준 cutoff = 11-16


class FakeDB:
    """rag.document(+doc_sentiment, rag.chunk) 와 core.doc_daily_agg 를 흉내 냄.
    문서를 지우면 감성·청크 행도 함께 사라짐(ON DELETE CASCADE)."""

    ROW_BYTES = {"doc": 700.0, "chunk": 3000.0, "sentiment": 90.0}

    def __init__(self, docs, chunks=None):
        self.docs = {d[0]: d for d in docs}   # doc_id -> (doc_id, company_id, source, published_at, score)
        self.chunks = dict(chunks or {})      # doc_id -> 청크 수
        self.agg = {}
        self.applied = 0
        self.vacuumed = []

    def install(self, monkeypatch):
        monkeypatch.setattr(db, "retention_counts", self.counts)
        monkeypatch.setattr(db, "retention_row_bytes", lambda conn: dict(self.ROW_BYTES))
        monkeypatch.setattr(db, "retention_batch", lambda conn, sources, cutoff, n: self._old(sources, cutoff)[:n])
        monkeypatch.setattr(db, "retention_apply", self.apply)
        monkeypatch.setattr(db, "vacuum_analyze", lambda conn, tables: self.vacuumed.append(tuple(tables)))

    def _old(self, sources, cutoff):
        return sorted((d for d in self.docs.values() if d[2] in sources and d[3] is not None and d[3] < cutoff),
                      key=lambda d: d[0])

    def counts(self, conn, sources, cutoff):
        out = {s: (0, 0, 0) for s in sources}
        for d in self._old(sources, cutoff):
            n, c, s = out[d[2]]
            out[d[2]] = (n + 1, c + self.chunks.get(d[0], 0), s + (d[4] is not None))
        return out

    def apply(self, conn, agg, ids):
        for k, (n, ns, sm) in agg.items():
            o = self.agg.get(k, (0, 0, 0.0))
            self.agg[k] = (o[0] + n, o[1] + ns, o[2] + sm)
        for i in ids:
            del self.docs[i]
            self.chunks.pop(i, None)
        self.applied += 1


def _opinion_docs():
    return [(1, 7, "naver_blog", OLD, 0.5), (2, 7, "naver_blog", OLD, -0.1), (3, 7, "naver_blog", OLD, None),
            (4, 7, "naver_cafe", OLD, 0.2), (5, 8, "naver_blog", OLD, 0.9),
            (6, 7, "naver_blog", NEW, 0.3)]                       # 기한 안


def _main_docs():
    return [(11, 7, "naver_news", OLD, 0.4), (12, 7, "naver_news", OLD, -0.2), (13, 7, "finnhub_news", OLD, 0.1),
            (14, 7, "youtube_comment", OLD, None), (15, None, "naver_news", OLD, 0.3),   # 종목 없음 → 집계 없이 삭제
            (16, 7, "naver_news", NEW, 0.6),                                            # 기한 안
            (17, 7, "naver_news_backfill", OLD, 0.5), (18, 7, "dart_report", OLD, None),  # 제외 소스
            (19, 7, "sec_edgar", OLD, None)]


def test_aggregate_counts_and_sums_and_skips_docs_agent3_never_uses():
    agg = retention.aggregate([(7, "naver_blog", OLD, 0.5), (7, "naver_blog", OLD, -0.1), (7, "naver_blog", OLD, None),
                               (None, "naver_news", OLD, 0.3), (7, "naver_news", None, 0.2)])
    assert agg == {(7, "naver_blog", OLD): (3, 2, 0.4)}


def test_default_is_14_days_and_cutoff_is_exclusive():
    assert retention.DEFAULT_DAYS == 14
    assert retention.cutoff_date(TODAY, 14) == date(2026, 11, 16)   # 11-16 발행분까지 보관, 11-15 이전은 삭제


def test_purge_opinion_deletes_only_blog_and_cafe_and_keeps_aggregate(monkeypatch):
    monkeypatch.delenv("RETENTION_DAYS", raising=False)
    fake = FakeDB(_opinion_docs() + _main_docs())
    fake.install(monkeypatch)
    r = retention.purge(None, TODAY, batch=2)
    assert r["days"] == 14 and r["targets"] == 5 and r["deleted"] == 5 and fake.applied == 3   # 배치 2 → 3번
    assert set(fake.docs) == {6} | {d[0] for d in _main_docs()}
    assert fake.agg[(7, "naver_blog", OLD)] == (3, 2, 0.4)
    assert fake.agg[(7, "naver_cafe", OLD)] == (1, 1, 0.2)
    assert fake.agg[(8, "naver_blog", OLD)] == (1, 1, 0.9)


def test_purge_news_deletes_news_and_comments_with_chunks_and_never_excluded_sources(monkeypatch):
    fake = FakeDB(_main_docs(), chunks={11: 3, 12: 2, 15: 1, 16: 4, 17: 5, 18: 9})
    fake.install(monkeypatch)
    r = retention.purge(None, TODAY, 14, sources=retention.NEWS_SOURCES)
    assert r["deleted"] == 5
    assert set(fake.docs) == {16, 17, 18, 19}                      # 기한 안·backfill·dart_report·그 밖 소스는 그대로
    assert set(fake.chunks) == {16, 17, 18}                         # 지운 문서의 청크만 사라짐
    assert fake.agg == {(7, "naver_news", OLD): (2, 2, 0.2), (7, "finnhub_news", OLD): (1, 1, 0.1),
                        (7, "youtube_comment", OLD): (1, 0, 0.0)}   # 종목 없는 15번은 집계 없음


def test_excluded_sources_are_never_targets_even_if_asked(monkeypatch):
    fake = FakeDB(_main_docs())
    fake.install(monkeypatch)
    r = retention.purge(None, TODAY, 14, sources=("naver_news_backfill", "dart_report", "sec_edgar"))
    assert r["sources"] == [] and r["targets"] == 0 and len(fake.docs) == len(_main_docs())


def test_second_run_changes_nothing(monkeypatch):
    fake = FakeDB(_opinion_docs() + _main_docs())
    fake.install(monkeypatch)
    retention.purge(None, TODAY, 14, sources=retention.SOURCES)
    before = dict(fake.agg)
    r = retention.purge(None, TODAY, 14, sources=retention.SOURCES)
    assert r["targets"] == 0 and r["deleted"] == 0 and fake.agg == before


def test_dry_run_writes_nothing_and_reports_per_source_estimate_with_chunks(monkeypatch):
    fake = FakeDB(_main_docs(), chunks={11: 3, 12: 2})
    fake.install(monkeypatch)
    r = retention.purge(None, TODAY, 14, dry_run=True, sources=retention.NEWS_SOURCES, vacuum=True)
    news = r["by_source"]["naver_news"]
    assert (news["docs"], news["chunks"], news["sentiments"]) == (3, 5, 3)
    assert news["est_bytes"] == 3 * 700 + 5 * 3000 + 3 * 90
    assert r["targets"] == 5 and r["est_bytes"] == sum(v["est_bytes"] for v in r["by_source"].values())
    assert r["deleted"] == 0 and len(fake.docs) == len(_main_docs()) and fake.agg == {} and fake.vacuumed == []


def test_vacuum_runs_only_after_deleting(monkeypatch):
    fake = FakeDB(_main_docs())
    fake.install(monkeypatch)
    r = retention.purge(None, TODAY, 14, sources=retention.NEWS_SOURCES, vacuum=True)
    assert r["vacuumed"] and fake.vacuumed == [retention.VACUUM_TABLES]
    r = retention.purge(None, TODAY, 14, sources=retention.NEWS_SOURCES, vacuum=True)
    assert not r["vacuumed"] and len(fake.vacuumed) == 1


def test_retention_days_is_configurable(monkeypatch):
    monkeypatch.delenv("RETENTION_DAYS", raising=False)
    assert retention.retention_days() == 14
    monkeypatch.setenv("RETENTION_DAYS", "30")
    assert retention.retention_days() == 30 and retention.retention_days(10) == 10
    monkeypatch.setenv("RETENTION_DAYS", "abc")
    assert retention.retention_days() == 14


def test_youtube_video_metadata_and_excluded_sources_are_not_targets():
    assert "youtube_video" not in retention.SOURCES and "core.youtube_video" not in retention.VACUUM_TABLES
    assert not {"naver_news_backfill", "dart_report"} & set(retention.SOURCES)


def test_registered_as_daily_jobs_with_existing_commands():
    for name in ("purge-opinion", "purge-news"):
        job = next(j for j in jobs.JOBS if j.name == name)
        assert job.every == jobs.DAY and job.needs == jobs.DB and hasattr(cli, job.command)
        ns = job.namespace()
        assert ns.days is None and ns.dry_run is False and ns.vacuum is True


def test_purge_news_includes_leftover_blog_only_when_opinion_split(monkeypatch):
    seen = []

    class Conn:
        read_only = False

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(db, "connect", Conn)
    monkeypatch.setattr(cli, "_purge", lambda conn, sources, args: seen.append(tuple(sources)) or
                        {"cutoff": "2026-11-16", "days": 14, "targets": 0, "est_bytes": 0, "by_source": {},
                         "deleted": 0, "agg_rows": 0, "vacuumed": False})
    args = SimpleNamespace(days=None, dry_run=True, vacuum=False)
    monkeypatch.delenv(db.OPINION_URL_ENV, raising=False)
    cli.cmd_purge_news(args)
    monkeypatch.setenv(db.OPINION_URL_ENV, "postgresql://x")
    cli.cmd_purge_news(args)
    assert seen == [retention.NEWS_SOURCES, retention.NEWS_SOURCES + retention.OPINION_SOURCES]
