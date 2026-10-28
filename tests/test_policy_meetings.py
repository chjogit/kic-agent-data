"""sql/021 통화정책 결정 회의 일정 시드 점검 (DB 없이 파일만)."""
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

SQL = (Path(__file__).resolve().parents[1] / "sql" / "021_policy_meetings.sql").read_text(encoding="utf-8")
ROW = re.compile(r"\('(BOK|FOMC)', '(\d{4}-\d{2}-\d{2})', '(\d{4}-\d{2}-\d{2})', '(\d{2}:\d{2})', '([\w/]+)', "
                 r"(true|false),\s*'(\w+)', '(\w+)', NULL\)")
ROWS = [m.groups() for m in ROW.finditer(SQL)]


def test_rows_parse_and_are_unique():
    assert len(ROWS) == SQL.count("('BOK',") + SQL.count("('FOMC',")
    keys = [(b, d) for b, d, *_ in ROWS]
    assert len(keys) == len(set(keys))


def test_bok_is_single_day_10am_seoul():
    bok = [r for r in ROWS if r[0] == "BOK"]
    assert bok and all(r[1] == r[2] and r[3] == "10:00" and r[4] == "Asia/Seoul" and r[5] == "false" for r in bok)
    assert Counter(d[:4] for _, d, *_ in bok)["2026"] == 8           # 한 해 8회


def test_fomc_two_day_meetings_2pm_new_york():
    fomc = [r for r in ROWS if r[0] == "FOMC"]
    for _, dec, start, at, tz, *_ in fomc:
        assert date.fromisoformat(dec) - date.fromisoformat(start) == timedelta(days=1)
        assert at == "14:00" and tz == "America/New_York"
    per_year = Counter(d[:4] for _, d, *_ in fomc)
    assert per_year == {"2025": 8, "2026": 8, "2027": 8}
    sep = Counter(d[:4] for _, d, _, _, _, p, *_ in fomc if p == "true")
    assert sep == {"2025": 4, "2026": 4, "2027": 4}                  # 경제전망(SEP)은 3·6·9·12월
    assert all(s == "tentative" for _, d, *_, s, _ in fomc if d.startswith("2027"))


def test_seed_is_idempotent_and_readable():
    assert "ON CONFLICT (bank, decision_date) DO UPDATE" in SQL
    assert "GRANT SELECT ON core.policy_meeting TO kic_readonly" in SQL
