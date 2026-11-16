"""#86: DB 접속 불가(한도 초과 포함)를 DB 없이 GitHub Issue 1개로 알린다. 비밀값·호스트는 쓰지 않는다."""
from datetime import datetime, timezone

from kic_data import alerts, outage_notice as on

NOW = datetime(2026, 11, 16, 3, 0, tzinfo=timezone.utc)
SECRET_ERR = ('connection failed: connection to server at "13.251.17.193", port 5432 failed: ERROR:  Your account or '
              'project has exceeded the quota; ep-snowy-river-ao2ff6cd-pooler.c-2.ap-southeast-1.aws.neon.tech postgresql://u:pw@h/db')


class FakeIssues:
    def __init__(self):
        self.issues, self.comments, self.next = [], [], 1

    def list_open(self):
        return [i for i in self.issues if i["state"] == "open"]

    def create(self, title, body):
        self.issues.append({"number": self.next, "title": title, "body": body, "state": "open"})
        self.next += 1
        return self.issues[-1]

    def comment(self, number, text):
        self.comments.append((number, text))

    def update_title(self, number, title):
        next(i for i in self.issues if i["number"] == number)["title"] = title

    def close(self, number):
        next(i for i in self.issues if i["number"] == number)["state"] = "closed"


def test_classify():
    assert on.classify(SECRET_ERR) == "quota"
    assert on.classify("connection timed out") == "other"


def test_probe_reports_kind_without_leaking_error_text():
    def boom(url):
        raise RuntimeError(SECRET_ERR)

    res = on.probe({"메인": "postgresql://x"}, connect=boom)
    assert res == {"메인": "quota"}
    assert "13.251" not in str(res) and "neon.tech" not in str(res)


def test_probe_ok_and_missing_url_skipped():
    class C:
        def close(self):
            pass

    assert on.probe({"메인": "u", "kic-opinion": None}, connect=lambda url: C()) == {"메인": None}


def test_failure_opens_one_issue_then_only_comments():
    gh = FakeIssues()
    r1 = on.notify_failure(gh, {"메인": "quota", "kic-opinion": "quota"}, "kic-collect", "https://github.com/o/r/actions/runs/1", NOW)
    r2 = on.notify_failure(gh, {"메인": "quota"}, "kic-alerts", "https://github.com/o/r/actions/runs/2", NOW)
    assert r1 == "opened" and r2 == "commented"
    assert len(gh.issues) == 1 and len(gh.comments) == 1
    assert "한도 초과" in gh.issues[0]["title"] and gh.issues[0]["title"].startswith("[KIC 장애]")
    assert "runs/2" in gh.comments[0][1] and "kic-alerts" in gh.comments[0][1]


def test_title_upgrades_to_quota_when_cause_becomes_visible():
    gh = FakeIssues()
    on.notify_failure(gh, {"메인": "other"}, "kic-collect", "u1", NOW)
    assert "한도 초과" not in gh.issues[0]["title"]
    on.notify_failure(gh, {"메인": "quota"}, "kic-collect", "u2", NOW)
    assert "한도 초과" in gh.issues[0]["title"] and len(gh.issues) == 1


def test_body_has_no_secrets_or_hosts():
    gh = FakeIssues()
    on.notify_failure(gh, {"메인": "quota"}, "kic-collect", "https://github.com/o/r/actions/runs/9", NOW)
    text = gh.issues[0]["title"] + gh.issues[0]["body"]
    assert "postgresql" not in text and "neon.tech" not in text and "13.251" not in text and "pw" not in text
    assert "runs/9" in text and "메인" in text


def test_recovered_closes_only_when_all_ok():
    gh = FakeIssues()
    on.notify_failure(gh, {"메인": "quota"}, "kic-collect", "u", NOW)
    assert on.close_if_recovered(gh, {"메인": "quota"}, NOW) == "still_down" and gh.issues[0]["state"] == "open"
    assert on.close_if_recovered(gh, {"메인": None, "kic-opinion": None}, NOW) == "closed" and gh.issues[0]["state"] == "closed"
    assert on.close_if_recovered(gh, {"메인": None}, NOW) == "none"


def test_probe_ok_means_no_issue_for_unrelated_failure():
    gh = FakeIssues()
    assert on.notify_failure(gh, {"메인": None}, "kic-alerts", "u", NOW) == "db_ok"
    assert gh.issues == []


def test_alert_sync_ignores_outage_issue():
    """기존 alert-sync(label kic-alert)가 장애 이슈를 닫거나 갱신하지 않도록 라벨·제목이 달라야 한다."""
    assert on.OUTAGE_MARK != alerts.MARK and not on.TITLE.startswith(alerts.TITLE_PREFIX)
    assert alerts._key_of({"body": on.build_body({"메인": "quota"}, "kic-collect", "u", NOW)}) is None


def test_main_never_fails_even_without_token_or_on_error(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert on.main(["failure", "--workflow", "kic-collect"]) == 0
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr(on, "probe", lambda urls, connect=None: (_ for _ in ()).throw(RuntimeError("boom")))
    assert on.main(["failure"]) == 0
    assert "boom" not in capsys.readouterr().out   # 오류 문구는 출력하지 않고 종류만


def test_workflows_wire_outage_steps():
    import pathlib
    import yaml

    root = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"
    alerts_steps = yaml.safe_load((root / "alerts.yml").read_text(encoding="utf-8"))["jobs"]["alerts"]["steps"]
    text = " ".join(str(s.get("run", "")) for s in alerts_steps)
    assert "outage_notice failure --workflow kic-alerts" in text and "outage_notice recovered" in text
    prep = yaml.safe_load((root / "collect.yml").read_text(encoding="utf-8"))["jobs"]["prepare"]
    assert prep["permissions"]["issues"] == "write"
    assert any("outage_notice failure --workflow kic-collect" in str(s.get("run", "")) and "failure()" in str(s.get("if", ""))
               for s in prep["steps"])
