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
