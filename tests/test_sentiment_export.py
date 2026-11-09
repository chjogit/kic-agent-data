"""감성 집계 내보내기(#71): 창·집계·비교 불가·파일 모양·원문 0·덮어쓰기 범위(sentiment/latest/ 밖은 건드리지 않음)·/quality 신호등."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone

import pytest

from kic_data import quality, sentiment_export as sx

AS_OF = date(2026, 11, 9)
NOW = datetime(2026, 11, 9, 9, 5, tzinfo=sx.KST)
CO = [{"company_id": 1, "ticker": "005930", "name": "삼성전자", "market": "KR"},
      {"company_id": 2, "ticker": "000660", "name": "SK하이닉스", "market": "KR"}]
SRC = ["naver_news", "naver_blog"]


def agg(cid, src, win, kind, n=10, lab=10, pos=3, neu=1, neg=2, none=4, scored=6, ssum=0.6, last=date(2026, 11, 8)):
    return {"company_id": cid, "source": src, "win": win, "kind": kind, "n_docs": n, "n_labeled": lab, "positive": pos, "neutral": neu,
            "negative": neg, "none": none, "n_scored": scored, "sum_score": ssum, "last_doc_date": last,
            "last_collected_at": datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)}


def test_window_specs_are_inclusive_n_days_and_previous_period_of_same_length():
    s = sx.window_specs(AS_OF)
    got = {(w, k): (lo, hi) for w, k, lo, hi in zip(s["win"], s["kind"], s["lo"], s["hi"])}
    assert set(got) == {(n, k) for n in (14, 7, 3, 1) for k in ("cur", "prev")}
    assert got[(1, "cur")] == (date(2026, 11, 8), AS_OF)                    # 1일 = 기준일 하루(날짜가 (lo, hi] 반열림)
    assert got[(7, "cur")] == (date(2026, 11, 2), AS_OF) and got[(7, "prev")] == (date(2026, 10, 26), date(2026, 11, 2))


def test_rows_cover_every_company_source_window_including_zero_docs_and_total():
    rows = sx.build_rows(CO, SRC, [agg(1, "naver_news", 7, "cur")], AS_OF)
    assert len(rows) == 2 * 4 * (len(SRC) + 1)                              # 종목 2 × 창 4 × (출처 2 + 합계)
    empty = next(r for r in rows if r["ticker"] == "000660" and r["source"] == "naver_news" and r["window_days"] == 7)
    assert empty["n_docs"] == 0 and empty["positive_ratio"] is None and empty["avg_score"] is None and empty["last_doc_date"] is None


def test_counts_ratios_average_and_total_row():
    rows = sx.build_rows(CO, SRC, [agg(1, "naver_news", 7, "cur"), agg(1, "naver_blog", 7, "cur", n=5, lab=5, pos=1, neu=0, neg=3, none=1, scored=4, ssum=-1.0)], AS_OF)
    one = next(r for r in rows if r["ticker"] == "005930" and r["source"] == "naver_news" and r["window_days"] == 7)
    assert (one["n_docs"], one["positive"], one["positive_ratio"], one["avg_score"], one["n_scored"]) == (10, 3, 0.3, 0.1, 6)
    tot = next(r for r in rows if r["ticker"] == "005930" and r["source"] == sx.TOTAL and r["window_days"] == 7)
    assert (tot["n_docs"], tot["n_labeled"], tot["positive"], tot["negative"], tot["n_scored"]) == (15, 15, 4, 5, 10)
    assert tot["avg_score"] == round((0.6 - 1.0) / 10, 4) and tot["positive_ratio"] == round(4 / 15, 4)


def test_change_is_only_given_when_previous_period_has_scores():
    both = sx.build_rows(CO, SRC, [agg(1, "naver_news", 7, "cur", ssum=0.6), agg(1, "naver_news", 7, "prev", ssum=-0.6)], AS_OF)
    r = next(x for x in both if x["ticker"] == "005930" and x["source"] == "naver_news" and x["window_days"] == 7)
    assert r["change"] == 0.2 and r["change_status"] == "비교 가능" and r["prev_avg_score"] == -0.1
    only = sx.build_rows(CO, SRC, [agg(1, "naver_news", 14, "cur")], AS_OF)                 # 14일 창은 직전 기간 자료가 없음(보관 14일)
    r = next(x for x in only if x["ticker"] == "005930" and x["source"] == "naver_news" and x["window_days"] == 14)
    assert r["change"] is None and r["change_status"].startswith("비교 불가")
    none = next(x for x in only if x["ticker"] == "000660" and x["source"] == "naver_news" and x["window_days"] == 14)
    assert none["change_status"] == "비교 불가(이번 기간 점수 없음)"


def _files():
    rows = sx.build_rows(CO, SRC, [agg(1, "naver_news", 7, "cur")], AS_OF)
    return sx.make_files(rows, NOW, NOW + timedelta(minutes=1), {"seconds_to_build": 1.0})


def test_files_are_four_json_four_csv_and_meta_with_hashes_and_no_raw_text():
    f = _files()
    assert sorted(k.removeprefix(sx.PREFIX) for k in f) == sorted([f"window={n}d.{e}" for n in (14, 7, 3, 1) for e in ("json", "csv")] + ["_meta.json"])
    meta = json.loads(f[sx.PREFIX + "_meta.json"])
    assert meta["complete"] is True and meta["as_of"].startswith("2026-11-09T09:05") and "56.6%" in meta["sentiment_method"]
    assert all(v["bytes"] == len(f[k]) for k, v in meta["files"].items()) and sx.PREFIX + "_meta.json" not in meta["files"]
    body = json.loads(f[sx.PREFIX + "window=7d.json"])
    assert body["window_days"] == 7 and len(body["rows"]) == 2 * (len(SRC) + 1) and body["columns"] == list(sx.COLUMNS)
    text = b"".join(f.values()).decode("utf-8")
    assert not re.search(r"https?://|youtube\.com|youtu\.be|blog\.naver\.com|cafe\.naver\.com", text)
    assert not {"title", "url", "author", "summary"} & set(sx.COLUMNS)
    assert f[sx.PREFIX + "window=7d.csv"].startswith("﻿ticker,name,market,source".encode("utf-8"))


# ---------------------------------------------------------------- 덮어쓰기

class FakeStore:
    def __init__(self, objects=None, fail_on=None):
        self.objects = dict(objects or {})
        self.fail_on, self.calls = fail_on, []

    def put(self, key, data, content_type="application/zip"):
        self.calls.append(("put", key))
        if key == self.fail_on:
            raise RuntimeError("쓰기 실패")
        self.objects[key] = data

    def size(self, key):
        return len(self.objects[key]) if key in self.objects else None

    def list(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]

    def delete(self, key):
        self.calls.append(("delete", key))
        del self.objects[key]


OUTSIDE = {"disclosure/dart/2026/00123.zip": b"zip", "sentiment/archive/old.json": b"x", "sentiment/latest_old/a.json": b"y"}


def test_upload_overwrites_same_names_removes_leftovers_and_never_touches_outside_objects():
    store = FakeStore({**OUTSIDE, sx.PREFIX + "window=30d.json": b"old", sx.PREFIX + "window=7d.json": b"older", sx.PREFIX + "legacy/x.csv": b"z"})
    f = _files()
    r = sx.upload(store, f)
    assert {k for k in store.objects if k.startswith(sx.PREFIX)} == set(f)                          # 새 9개만 남음
    assert store.objects[sx.PREFIX + "window=7d.json"] == f[sx.PREFIX + "window=7d.json"]           # 같은 이름은 덮어씀
    assert r["deleted"] == [sx.PREFIX + "legacy/x.csv", sx.PREFIX + "window=30d.json"]
    assert {k: store.objects[k] for k in OUTSIDE} == OUTSIDE                                        # 밖의 객체 변화 0
    assert all(c[1].startswith(sx.PREFIX) for c in store.calls)
    puts = [c[1] for c in store.calls if c[0] == "put"]
    assert puts[-1] == sx.PREFIX + "_meta.json"                                                     # 메타는 마지막
    again = sx.upload(store, f)                                                                     # 두 번째 실행도 같은 결과
    assert again["deleted"] == [] and {k for k in store.objects if k.startswith(sx.PREFIX)} == set(f)


def test_upload_failure_deletes_nothing_and_leaves_previous_leftovers():
    store = FakeStore({sx.PREFIX + "window=30d.json": b"old"}, fail_on=sx.PREFIX + "window=3d.json")
    with pytest.raises(RuntimeError):
        sx.upload(store, _files())
    assert not [c for c in store.calls if c[0] == "delete"] and sx.PREFIX + "window=30d.json" in store.objects


def test_upload_refuses_paths_outside_prefix_and_size_mismatch():
    store = FakeStore()
    with pytest.raises(ValueError):
        sx.upload(store, {"disclosure/x.json": b"{}"})
    with pytest.raises(ValueError):
        sx.upload(store, {sx.PREFIX + "../disclosure/x.json": b"{}"})
    assert store.calls == []

    class Short(FakeStore):
        def size(self, key):
            return 1
    with pytest.raises(RuntimeError, match="크기 불일치"):
        sx.upload(Short(), _files())


# ---------------------------------------------------------------- /quality 신호등

def _row(**kw):
    return {"api_name": "sentiment-export", "last_success": datetime.now(timezone.utc), "consecutive_failures": 0, **kw}


def test_quality_signal_is_red_on_one_failure_and_when_older_than_13_hours():
    now = datetime.now(timezone.utc)
    assert quality.api_status(_row(), now) == "ok"
    assert quality.api_status(_row(consecutive_failures=1), now) == "error"                         # 4회 중 한 번만 실패해도 빨강
    assert quality.api_status(_row(last_success=now - timedelta(hours=14)), now) == "error"
    assert quality.api_status(_row(last_success=now - timedelta(hours=12)), now) == "ok"            # 21시 → 다음 날 9시 간격
    other = {"api_name": "naver", "last_success": now - timedelta(hours=14), "consecutive_failures": 1}
    assert quality.api_status(other, now) == "warn"                                                 # 다른 API 규칙은 그대로
    assert "sentiment-export" in quality.API_NAMES and quality.EXTERNAL_API_OF_JOB["export-sentiment"] == "sentiment-export"
    assert "export-sentiment" not in quality.API_OF_JOB


# ---------------------------------------------------------------- S3Store.list / delete

def test_s3store_list_follows_continuation_and_delete_calls_client():
    from kic_data.storage import S3Store

    class Client:
        def __init__(self):
            self.deleted, self.pages = [], [
                {"Contents": [{"Key": "sentiment/latest/a"}], "IsTruncated": True, "NextContinuationToken": "t1"},
                {"Contents": [{"Key": "sentiment/latest/b"}], "IsTruncated": False}]

        def list_objects_v2(self, **kw):
            assert kw["Prefix"] == "sentiment/latest/"
            assert ("ContinuationToken" in kw) == bool(self.deleted == [] and len(self.pages) == 1)
            return self.pages.pop(0)

        def delete_object(self, Bucket, Key):
            self.deleted.append((Bucket, Key))

    c = Client()
    s = S3Store(c, "bk")
    assert s.list("sentiment/latest/") == ["sentiment/latest/a", "sentiment/latest/b"]
    s.delete("sentiment/latest/a")
    assert c.deleted == [("bk", "sentiment/latest/a")]
