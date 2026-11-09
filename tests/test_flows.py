import unittest
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from kic_data import features, jobs
from kic_data.collectors import investor_flow, lending


def _naver(d: date, f="+100", o="-50", i="-50", hold="46.11%", vol="1,000"):
    return {"itemCode": "005930", "bizdate": d.strftime("%Y%m%d"), "foreignerPureBuyQuant": f, "organPureBuyQuant": o,
            "individualPureBuyQuant": i, "foreignerHoldRatio": hold, "accumulatedTradingVolume": vol}


class InvestorFlowTest(unittest.TestCase):
    def test_parse(self):
        rows = investor_flow.parse_trend([_naver(date(2026, 10, 23), "-868,997", "+627,670", "-61,043", "46.11%", "16,145,972")])
        self.assertEqual(rows, [{"trade_date": date(2026, 10, 23), "foreign_net_qty": -868997, "institution_net_qty": 627670,
                                 "individual_net_qty": -61043, "foreign_hold_pct": Decimal("46.11"), "volume": 16145972}])

    def test_hold_ratio_out_of_range_becomes_none(self):
        rows = investor_flow.parse_trend([_naver(date(2026, 10, 23), hold="12345.6%"), _naver(date(2026, 10, 22), hold="-3%"),
                                          _naver(date(2026, 10, 21), hold="100%")])
        self.assertEqual([r["foreign_hold_pct"] for r in rows], [None, None, Decimal("100")])

    def test_parse_error(self):
        with self.assertRaises(RuntimeError):
            investor_flow.parse_trend({"message": "not found"})

    def test_paging_uses_oldest_bizdate(self):
        days = [date(2026, 10, 23) - timedelta(days=i) for i in range(130)]
        calls = []

        def fake(session, source, url, params=None, **kw):
            calls.append(dict(params))
            before = params.get("bizdate")  # 네이버는 bizdate '이전' 거래일부터 돌려줌
            pool = [d for d in days if before is None or d.strftime("%Y%m%d") < before]
            return [_naver(d) for d in pool[:investor_flow.PAGE]]

        with mock.patch.object(investor_flow, "get_json", side_effect=fake):
            rows = investor_flow.fetch_flows(None, "005930", date(2026, 7, 1))
        self.assertEqual(rows[0]["trade_date"], date(2026, 7, 1))
        self.assertEqual(rows[-1]["trade_date"], date(2026, 10, 23))
        self.assertEqual(len(rows), len({r["trade_date"] for r in rows}))
        self.assertEqual(calls[1]["bizdate"], (date(2026, 10, 23) - timedelta(days=59)).strftime("%Y%m%d"))

    def test_flow_rows(self):
        flows = [{"trade_date": date(2026, 9, 1) + timedelta(days=i), "foreign_net_qty": 10 if i >= 22 else -5,
                  "institution_net_qty": -10, "individual_net_qty": 0, "foreign_hold_pct": 40 + i * 0.1, "volume": 100}
                 for i in range(25)]
        got = {r["feature"]: r["value"] for r in features.flow_rows(flows)}
        self.assertAlmostEqual(got["foreign_net_buy_20d_ratio"], (3 * 10 - 17 * 5) / 2000)
        self.assertAlmostEqual(got["institution_net_buy_20d_ratio"], -0.1)
        self.assertEqual(got["foreign_net_buy_streak"], 3)
        self.assertAlmostEqual(got["foreign_hold_pct"], 42.4)
        self.assertAlmostEqual(got["foreign_hold_chg_20d"], 2.0)
        self.assertEqual(features.flow_rows(None), [])
        self.assertIsNone({r["feature"]: r["value"] for r in features.flow_rows(flows[:5])}["foreign_net_buy_20d_ratio"])


class LendingTest(unittest.TestCase):
    def test_parse(self):
        payload = {"response": {"header": {"resultCode": "00"}, "body": {"totalCount": 2, "items": {"item": [
            {"basDt": "20260902", "mrktClsfNm": "코스피", "stckItmsNm": "동화약품", "stckItmsCd": "000020",
             "cclStckCnt": "23447", "rdptStckCnt": "23447", "balnStckCnt": "638252", "balnStckAmt": "3293380320"},
            {"basDt": "20260902", "stckItmsCd": ""}]}}}}
        rows, total = lending.parse_lending(payload)
        self.assertEqual(total, 2)
        self.assertEqual(rows, [{"ticker": "000020", "trade_date": date(2026, 9, 2), "lent_qty": 23447, "repaid_qty": 23447,
                                 "balance_qty": 638252, "balance_amt": 3293380320}])

    def test_empty_and_error(self):
        self.assertEqual(lending.parse_lending({"response": {"header": {"resultCode": "00"}, "body": {"items": ""}}}), ([], 0))
        with self.assertRaises(RuntimeError):
            lending.parse_lending({"response": {"header": {"resultCode": "30", "resultMsg": "SERVICE KEY IS NOT REGISTERED"}}})

    def test_lending_rows(self):
        lend = [{"trade_date": date(2026, 9, 1) + timedelta(days=i), "balance_qty": 1000 + i * 10} for i in range(21)]
        got = {r["feature"]: r["value"] for r in features.lending_rows(lend, 100000)}
        self.assertAlmostEqual(got["lending_balance_to_shares"], 0.012)
        self.assertAlmostEqual(got["lending_balance_chg_20d"], 0.2)
        self.assertEqual(features.lending_rows(None, 1), [])


class JobsTest(unittest.TestCase):
    def test_registered(self):
        for name in ("investor-flows", "backfill-investor-flows", "lending"):
            self.assertIn(name, jobs.JOB_BY_NAME)
        self.assertIn("backfill-investor-flows", jobs.PER_TICKER_BACKFILLS)


if __name__ == "__main__":
    unittest.main()


class LendingCommandTest(unittest.TestCase):
    def test_only_missing_trading_days_newest_first(self):
        from argparse import Namespace
        from contextlib import nullcontext

        from kic_data import cli

        today = date.today()
        trading = [today - timedelta(days=i) for i in range(1, 8)]
        asked = []

        def fetch_day(session, d, market="코스피"):
            asked.append((market, d))
            if market != "코스피":
                return []
            return [{"ticker": "005930", "trade_date": d, "lent_qty": 1, "repaid_qty": 1, "balance_qty": 10, "balance_amt": 100}]

        written = []
        with mock.patch.object(cli.db, "connect", return_value=nullcontext(None)), \
             mock.patch.object(cli.db, "list_companies", return_value=[(7, "005930", "삼성전자", None, None)]), \
             mock.patch.object(cli.db, "lending_dates", return_value={trading[0]}), \
             mock.patch.object(cli.db, "trading_dates", return_value=sorted(trading)), \
             mock.patch.object(cli.db, "upsert_lending", side_effect=lambda c, cid, rows, src: written.append((cid, rows, src)) or len(rows)), \
             mock.patch.object(cli.lending, "fetch_day", side_effect=fetch_day):
            rc = cli.cmd_lending(Namespace(days=30, max_days=3))
        self.assertEqual(rc, 0)
        # 시장마다 이미 있는 날은 건너뛰고 최근부터 3일 (코스피 → 코스닥)
        self.assertEqual(asked, [("코스피", d) for d in trading[1:4]] + [("코스닥", d) for d in trading[1:4]])
        self.assertEqual(written[0][0], 7)
        self.assertEqual(len(written[0][1]), 3)
        self.assertEqual(written[0][2], "fsc_lending")


class InvestorFlowCommandTest(unittest.TestCase):
    def _run(self, companies, latest, served, only_lagging=True, max_pages=None, target=date(2026, 10, 29)):
        from argparse import Namespace
        from contextlib import nullcontext

        from kic_data import cli

        asked, written, pages = [], [], []

        def fetch(session, ticker, since, max_pages=10):
            asked.append(ticker)
            pages.append(max_pages)
            return [{"trade_date": d} for d in served(ticker)]

        with mock.patch.object(cli.db, "connect", return_value=nullcontext(None)), \
             mock.patch.object(cli.db, "list_companies", return_value=companies), \
             mock.patch.object(cli.db, "latest_price_and_flow_dates", return_value=latest), \
             mock.patch.object(cli.db, "upsert_investor_flows", side_effect=lambda c, cid, rows, src: written.append(cid) or len(rows)), \
             mock.patch.object(cli.investor_flow, "fetch_flows", side_effect=fetch), \
             mock.patch.object(cli, "expected_flow_date", return_value=target), \
             mock.patch.object(cli, "drop_incomplete_today", side_effect=lambda rows, market: rows):
            rc = cli.cmd_investor_flows(Namespace(days=10, only_lagging=only_lagging, max_pages=max_pages))
        self.pages = pages
        return rc, asked, written

    def test_max_pages_defaults_to_10_and_can_be_raised_for_backfill(self):
        d0 = date(2026, 10, 28)
        companies = [(1, "000001", "a", None, None)]
        self._run(companies, {}, lambda t: [d0], only_lagging=False)
        self.assertEqual(self.pages, [10])
        self._run(companies, {}, lambda t: [d0], only_lagging=False, max_pages=20)
        self.assertEqual(self.pages, [20])

    def test_only_lagging_skips_up_to_date(self):
        d0, d1 = date(2026, 10, 28), date(2026, 10, 29)
        companies = [(1, "000001", "a", None, None), (2, "000002", "b", None, None)]
        latest = {1: (d1, d1), 2: (d1, d0)}  # 1 은 이미 시세 최신일까지 있음
        rc, asked, written = self._run(companies, latest, lambda t: [d0, d1])
        self.assertEqual((rc, asked, written), (0, ["000002"], [2]))

    def test_stops_when_naver_has_not_posted(self):
        d0, d1 = date(2026, 10, 28), date(2026, 10, 29)
        companies = [(i, f"{i:06d}", str(i), None, None) for i in range(1, 7)]
        latest = {i: (d1, d0) for i in range(1, 7)}
        rc, asked, _ = self._run(companies, latest, lambda t: [d0])  # 네이버에 10-29 가 아직 없음
        self.assertEqual(rc, 0)
        self.assertEqual(len(asked), cli_wait_stop())

    def test_recheck_runs_for_the_expected_trading_day_even_when_price_is_a_day_behind(self):
        """#74: 시세(금융위원회)는 다음 날 오전에야 올라와, 시세 마지막 날만 보면 그날 저녁 재확인이 모두 건너뛰어졌다."""
        d4, d5, d6 = date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6)
        companies = [(1, "000001", "a", None, None), (2, "000002", "b", None, None)]
        latest = {1: (d5, d5), 2: (d5, d5)}                      # 시세·수급 모두 11/5 까지 (11/6 시세는 아직 안 올라옴)
        rc, asked, written = self._run(companies, latest, lambda t: [d5, d6], target=d6)    # 금요일 저녁: 받아야 할 날 = 11/6
        self.assertEqual((rc, asked, written), (0, ["000001", "000002"], [1, 2]))
        rc, asked, _ = self._run(companies, latest, lambda t: [d4, d5], target=d5)           # 받아야 할 날이 이미 있으면 건너뜀
        self.assertEqual(asked, [])

    def test_waits_and_stops_when_expected_day_not_yet_posted(self):
        d5, d6 = date(2026, 11, 5), date(2026, 11, 6)
        companies = [(i, f"{i:06d}", str(i), None, None) for i in range(1, 8)]
        latest = {i: (d5, d5) for i in range(1, 8)}
        rc, asked, _ = self._run(companies, latest, lambda t: [d5], target=d6)
        self.assertEqual((rc, len(asked)), (0, cli_wait_stop()))

    def test_without_flag_fetches_all(self):
        d0 = date(2026, 10, 28)
        companies = [(1, "000001", "a", None, None), (2, "000002", "b", None, None)]
        rc, asked, _ = self._run(companies, {}, lambda t: [d0], only_lagging=False)
        self.assertEqual(asked, ["000001", "000002"])


def cli_wait_stop() -> int:
    from kic_data import cli

    return cli.FLOW_WAIT_STOP


class ExpectedFlowDateTest(unittest.TestCase):
    def _d(self, y, m, d, hh, mm=0, after=None):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from kic_data.util import expected_flow_date

        now = datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("Asia/Seoul"))
        return expected_flow_date(now) if after is None else expected_flow_date(now, after=after)

    def test_evening_weekday_expects_today_and_daytime_expects_previous_weekday(self):
        self.assertEqual(self._d(2026, 11, 6, 19), date(2026, 11, 6))       # 금 저녁 → 금
        self.assertEqual(self._d(2026, 11, 6, 14), date(2026, 11, 5))       # 금 낮 → 목
        self.assertEqual(self._d(2026, 11, 9, 14), date(2026, 11, 6))       # 월 낮 → 금 (11/9 PM 시연 시점)
        self.assertEqual(self._d(2026, 11, 9, 19), date(2026, 11, 9))

    def test_weekend_expects_friday(self):
        self.assertEqual(self._d(2026, 11, 7, 20), date(2026, 11, 6))
        self.assertEqual(self._d(2026, 11, 8, 3), date(2026, 11, 6))

    def test_alert_flow_date_is_latest_day_whose_next_business_day_1400_has_passed(self):
        """#74 경보 규칙: 기준 거래일의 다음 영업일 14:00(KST) 까지 수급이 없으면 경보 (F = 그 14:00 이 지난 가장 최근 영업일)."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from kic_data.util import alert_flow_date

        def f(m, d, hh, mm=0):
            return alert_flow_date(datetime(2026, m, d, hh, mm, tzinfo=ZoneInfo("Asia/Seoul")))

        self.assertEqual(f(11, 4, 13, 59), date(2026, 11, 2))    # 수 13:59 → 화 자료는 아직 기다림 (F=월)
        self.assertEqual(f(11, 4, 14, 1), date(2026, 11, 3))     # 수 14:01 → F=화
        self.assertEqual(f(11, 4, 14, 0), date(2026, 11, 3))     # 정각부터
        self.assertEqual(f(11, 7, 12), date(2026, 11, 5))        # 토 → F=목 (금 자료는 월 14시까지 기다림)
        self.assertEqual(f(11, 8, 23, 59), date(2026, 11, 5))    # 일 전체
        self.assertEqual(f(11, 9, 13, 59), date(2026, 11, 5))    # 월 13:59
        self.assertEqual(f(11, 9, 14, 1), date(2026, 11, 6))     # 월 14:01 → F=금
        self.assertEqual(f(11, 6, 20), date(2026, 11, 5))        # 금 저녁 → F=목 (금 자료는 월 14시)


class LateAlertRuleTest(unittest.TestCase):
    """#74: 실제 경보 판정(alerts.late_summary + alert_flow_date). 시세는 다음 영업일 11:16 에 올라온다고 가정."""

    def _late(self, last_flow, price_max, m, d, hh, mm=0):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from kic_data import alerts
        from kic_data.util import alert_flow_date

        f = alert_flow_date(datetime(2026, m, d, hh, mm, tzinfo=ZoneInfo("Asia/Seoul")))
        flow = alerts.late_summary([("A", price_max, last_flow, None)], flow_expected=f)[0]
        return flow["late_companies"] == 1

    def test_tuesday_data_alerts_only_from_wednesday_1400(self):
        mon, tue = date(2026, 11, 2), date(2026, 11, 3)
        self.assertFalse(self._late(mon, tue, 11, 4, 13, 59))    # 시세(화)는 수 11:16 에 올라왔지만 14시 전이라 경보 없음
        self.assertTrue(self._late(mon, tue, 11, 4, 14, 1))

    def test_friday_data_alerts_only_from_monday_1400(self):
        thu, fri = date(2026, 11, 5), date(2026, 11, 6)
        for args in ((11, 7, 12), (11, 8, 12), (11, 9, 13, 59)):
            self.assertFalse(self._late(thu, thu, *args), args)
        self.assertFalse(self._late(thu, fri, 11, 9, 13, 59))    # 월 11:16 이후 시세(금)가 올라와도 14시 전엔 경보 없음
        self.assertTrue(self._late(thu, fri, 11, 9, 14, 1))

    def test_no_alert_when_flow_is_present(self):
        fri = date(2026, 11, 6)
        self.assertFalse(self._late(fri, fri, 11, 9, 14, 1))
        self.assertFalse(self._late(fri, fri, 11, 10, 9))
