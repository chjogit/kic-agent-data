"""품질 알림 오프라인 테스트: 모의 GitHub·모의 DB, 네트워크 없음."""
from datetime import date, datetime, timedelta, timezone

from kic_data import alerts

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


class FakeGH:
    def __init__(self, open_issues=None, fail=None):
        self.issues = {i["number"]: dict(i) for i in (open_issues or [])}
        self.calls, self.fail, self.n = [], fail, 100

    def _chk(self, name):
        if self.fail == name:
            raise RuntimeError("HTTP 500")

    def list_open(self):
        self._chk("list")
        return [i for i in self.issues.values() if i.get("state", "open") == "open"]

    def create(self, title, body):
        self._chk("create")
        self.n += 1
        self.issues[self.n] = {"number": self.n, "title": title, "body": body}
        self.calls.append(("create", self.n))
        return self.issues[self.n]

    def comment(self, number, text):
        self.calls.append(("comment", number))

    def update_body(self, number, body):
        self.issues[number]["body"] = body
        self.calls.append(("update", number))

    def close(self, number):
        self.issues[number]["state"] = "closed"
        self.calls.append(("close", number))


def _health(mb=950, late=0, stalled=False):
    return {"capacity": [alerts.capacity_item("main", int(mb * 1024 * 1024)), alerts.capacity_item("kic-opinion", None, False)],
            "late": [{"dataset": "investor_flow", "label": "투자자별 수급", "late_companies": late, "total": 200,
                      "max_days_late": 3 if late else 0}],
            "tick": {"last": None, "hours_ago": 5.0, "limit_hours": 3.0, "stalled": stalled}}


def test_capacity_item_states():
    assert alerts.capacity_item("kic-opinion", None, False) == {"project": "kic-opinion", "configured": False}
    assert alerts.capacity_item("main", None)["error"]
    ok = alerts.capacity_item("main", 100 * 1024 * 1024, warn_mb=900)
    assert ok["over"] is False and ok["mb"] == 100.0
    assert alerts.capacity_item("main", 950 * 1024 * 1024, warn_mb=900)["over"] is True


def test_late_summary_counts_only_lagging_and_skips_missing():
    d = date(2026, 10, 30)
    rows = [("A", d, d, d), ("B", d, d - timedelta(days=3), None), ("C", d, None, d - timedelta(days=1)), ("D", None, d, d)]
    flow, lend = alerts.late_summary(rows)
    assert (flow["late_companies"], flow["total"], flow["max_days_late"]) == (1, 2, 3)
    assert (lend["late_companies"], lend["total"]) == (1, 2)


def test_tick_status():
    assert alerts.tick_status(NOW - timedelta(hours=1), NOW, 3)["stalled"] is False
    assert alerts.tick_status(NOW - timedelta(hours=5), NOW, 3)["stalled"] is True
    assert alerts.tick_status(None, NOW, 3)["stalled"] is True


def test_build_alerts_collects_every_cause():
    ov = {"apis": [{"api": "dart", "status": "error", "consecutive_failures": 4, "last_error_msg": "x"},
                   {"api": "fsc", "status": "ok"}]}
    keys = {i["key"] for i in alerts.build_alerts(ov, _health(950, late=5, stalled=True))}
    assert keys == {"api:dart", "capacity:main", "late:investor_flow", "stalled:tick"}
    assert alerts.build_alerts({"apis": []}, _health(100)) == []


def test_sync_opens_then_is_idempotent_then_updates_then_closes():
    gh = FakeGH()
    items = alerts.build_alerts(None, _health(950))
    assert alerts.sync_issues(gh, items, NOW)["opened"] == 1
    again = alerts.sync_issues(gh, items, NOW)
    assert again["opened"] == 0 and again["unchanged"] == 1 and len(gh.issues) == 1
    worse = alerts.build_alerts(None, _health(980))
    r = alerts.sync_issues(gh, worse, NOW)
    assert r["updated"] == 1 and ("comment", 101) in gh.calls and len(gh.issues) == 1
    r = alerts.sync_issues(gh, alerts.build_alerts(None, _health(100)), NOW)
    assert r["closed"] == 1 and gh.issues[101]["state"] == "closed"


def test_sync_without_client_or_on_github_errors_never_raises():
    assert alerts.sync_issues(None, alerts.build_alerts(None, _health(950)))["skipped"]
    r = alerts.sync_issues(FakeGH(fail="list"), alerts.build_alerts(None, _health(950)))
    assert r["errors"] and r["opened"] == 0
    r = alerts.sync_issues(FakeGH(fail="create"), alerts.build_alerts(None, _health(950)))
    assert r["errors"] and r["opened"] == 0


def test_get_client_needs_token_and_repo(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert alerts.get_client() is None
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    assert alerts.get_client().repo == "o/r"


def test_connect_opinion_none_without_setting(monkeypatch):
    monkeypatch.delenv(alerts.OPINION_URL_ENV, raising=False)
    assert alerts.connect_opinion() is None
