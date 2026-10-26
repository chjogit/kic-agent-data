"""오프라인 파서 테스트. pytest 와 `python -m unittest` 둘 다 실행됩니다 (DB·네트워크 불필요)."""
import io
import unittest
import zipfile
from datetime import date, timedelta
from decimal import Decimal

from kic_data import features, quarterly
from kic_data.collectors import dart, fsc, macro, sec
from kic_data.util import parse_ymd, to_decimal, to_int


class UtilTest(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(to_decimal("1,234"), Decimal("1234"))
        self.assertIsNone(to_decimal("-"))
        self.assertIsNone(to_decimal("."))
        self.assertEqual(to_int("70,000"), 70000)

    def test_nan_is_none(self):
        self.assertIsNone(to_decimal(float("nan")))
        self.assertIsNone(to_decimal("inf"))

    def test_dates(self):
        self.assertEqual(parse_ymd("20240102"), date(2024, 1, 2))
        self.assertEqual(parse_ymd("2024-01-02"), date(2024, 1, 2))


class FscTest(unittest.TestCase):
    def test_parse(self):
        payload = {"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": [
            {"basDt": "20240102", "clpr": "78,500", "mkp": "78000", "hipr": "79000", "lopr": "77500",
             "trqu": "1000", "trPrc": "78000000", "mrktTotAmt": "500000000"}]}}}}
        rows = fsc.parse_prices(payload)
        self.assertEqual(rows[0]["close"], Decimal("78500"))
        self.assertEqual(rows[0]["trade_date"], date(2024, 1, 2))

    def test_empty_and_error(self):
        self.assertEqual(fsc.parse_prices({"response": {"header": {"resultCode": "00"}, "body": {"items": ""}}}), [])
        with self.assertRaises(RuntimeError):
            fsc.parse_prices({"response": {"header": {"resultCode": "30", "resultMsg": "KEY"}}})


class DartTest(unittest.TestCase):
    def test_corp_codes(self):
        xml = ("<result><list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name>"
               "<stock_code>005930</stock_code></list><list><corp_code>1</corp_code><corp_name>비상장</corp_name>"
               "<stock_code> </stock_code></list></result>")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("CORPCODE.xml", xml)
        rows = dart.parse_corp_codes(buf.getvalue())
        self.assertEqual(rows, [{"corp_code": "00126380", "corp_name": "삼성전자", "stock_code": "005930"}])

    def test_financials(self):
        payload = {"status": "000", "list": [{"sj_div": "IS", "account_id": "ifrs-full_Revenue",
                                               "account_nm": "매출액", "thstrm_amount": "1,000", "rcept_no": "X"}]}
        rows = dart.parse_financials(payload, 2023, "11011", "CFS")
        self.assertEqual(rows[0]["amount"], Decimal("1000"))
        self.assertEqual(dart.parse_financials({"status": "013"}, 2023, "11011", "CFS"), [])
        with self.assertRaises(RuntimeError):
            dart.parse_financials({"status": "020"}, 2023, "11011", "CFS")


class MacroTest(unittest.TestCase):
    def test_ecos(self):
        payload = {"StatisticSearch": {"row": [{"TIME": "202401", "DATA_VALUE": "3.50"}]}}
        self.assertEqual(macro.parse_ecos(payload, "M"), [(date(2024, 1, 1), Decimal("3.50"))])
        self.assertEqual(macro.parse_ecos({"RESULT": {"CODE": "INFO-200"}}, "M"), [])
        with self.assertRaises(RuntimeError):
            macro.parse_ecos({"RESULT": {"CODE": "INFO-100", "MESSAGE": "키"}}, "M")

    def test_fred(self):
        payload = {"observations": [{"date": "2024-01-02", "value": "4.0"}, {"date": "2024-01-03", "value": "."}]}
        rows = macro.parse_fred(payload)
        self.assertEqual(rows[0], (date(2024, 1, 2), Decimal("4.0")))
        self.assertIsNone(rows[1][1])


class SecTest(unittest.TestCase):
    def test_ticker_map(self):
        self.assertEqual(sec.parse_ticker_map({"0": {"cik_str": 320193, "ticker": "AAPL"}}), {"AAPL": "0000320193"})

    def test_facts(self):
        payload = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
            {"form": "10-K", "fp": "FY", "fy": 2023, "val": 5, "accn": "a"},
            {"form": "10-Q", "fp": "Q1", "fy": 2023, "val": 1, "accn": "b"}]}}}}}
        rows = sec.parse_annual_facts(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["amount"], 5)


class ExtraTest(unittest.TestCase):
    def test_dart_company(self):
        self.assertEqual(dart.parse_company({"status": "000", "induty_code": "264"})["induty_code"], "264")
        with self.assertRaises(RuntimeError):
            dart.parse_company({"status": "010"})

    def test_sec_submissions(self):
        self.assertEqual(sec.parse_submissions({"sic": "3674", "sicDescription": "Semiconductors"})["sic"], "3674")

    def test_sec_keeps_latest_period(self):
        payload = {"facts": {"us-gaap": {"Assets": {"units": {"USD": [
            {"form": "10-K", "fp": "FY", "fy": 2023, "val": 1, "end": "2022-12-31", "accn": "a"},
            {"form": "10-K", "fp": "FY", "fy": 2023, "val": 2, "end": "2023-12-31", "accn": "a"}]}}}}}
        self.assertEqual(sec.parse_annual_facts(payload)[0]["amount"], 2)

    def test_sec_skips_non_annual_duration(self):
        payload = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
            {"form": "10-K", "fp": "FY", "fy": 2023, "val": 9, "start": "2023-10-01", "end": "2023-12-31", "accn": "a"}]}}}}}
        self.assertEqual(sec.parse_annual_facts(payload), [])

    def test_sec_ifrs_20f(self):
        payload = {"facts": {"ifrs-full": {
            "Revenue": {"units": {"EUR": [{"form": "20-F", "fp": "FY", "fy": 2024, "val": 28, "start": "2024-01-01", "end": "2024-12-31", "accn": "x"}]}},
            "Equity": {"units": {"EUR": [{"form": "20-F", "fp": "FY", "fy": 2024, "val": 12, "end": "2024-12-31", "accn": "x"}]}}}}}
        rows = {r["account_id"]: r for r in sec.parse_annual_facts(payload)}
        self.assertEqual(rows["ifrs-full_Revenue"]["currency"], "EUR")
        self.assertEqual(rows["ifrs-full_Equity"]["amount"], 12)

    def test_sec_usgaap_non_usd(self):
        payload = {"facts": {"us-gaap": {"Assets": {"units": {"EUR": [
            {"form": "20-F", "fp": "FY", "fy": 2024, "val": 7, "end": "2024-12-31", "accn": "x"}]}}}}}
        rows = sec.parse_annual_facts(payload)
        self.assertEqual((rows[0]["amount"], rows[0]["currency"]), (7, "EUR"))

    def test_sec_shares_latest_and_adr(self):
        payload = {"facts": {"dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"val": 100, "end": "2025-01-31", "filed": "2025-02-20"},
            {"val": 25_000, "end": "2026-02-28", "filed": "2026-03-10"},
            {"val": 25_900, "end": "2026-02-28", "filed": "2026-04-01"}]}}}}}
        self.assertEqual(sec.parse_shares_outstanding(payload, "NVDA"), (25_900, date(2026, 2, 28)))
        self.assertEqual(sec.parse_shares_outstanding(payload, "TSM")[0], 25_900 // 5)  # ADR 1주 = 원주 5주
        self.assertIsNone(sec.parse_shares_outstanding({"facts": {}}))

    def test_fsc_latest_shares(self):
        rows = [{"trade_date": date(2026, 10, 19), "shares": 10}, {"trade_date": date(2026, 10, 20), "shares": 12},
                {"trade_date": date(2026, 10, 21), "shares": None}]
        self.assertEqual(fsc.latest_shares(rows), (12, date(2026, 10, 20)))
        self.assertIsNone(fsc.latest_shares([]))


class QuarterlyTest(unittest.TestCase):
    def test_dart_quarters(self):
        rev = "ifrs-full_Revenue"
        facts = [
            {"fiscal_year": 2024, "report_code": "11013", "account_id": rev, "amount": Decimal(10), "amount_ytd": None, "currency": "KRW"},
            {"fiscal_year": 2024, "report_code": "11012", "account_id": rev, "amount": Decimal(15), "amount_ytd": Decimal(25), "currency": "KRW"},
            {"fiscal_year": 2024, "report_code": "11014", "account_id": rev, "amount": Decimal(20), "amount_ytd": Decimal(45), "currency": "KRW"},
            {"fiscal_year": 2024, "report_code": "11011", "account_id": rev, "amount": Decimal(70), "amount_ytd": None, "currency": "KRW"},
            {"fiscal_year": 2024, "report_code": "11011", "account_id": "ifrs-full_Assets", "amount": Decimal(100), "amount_ytd": None, "currency": "KRW"},
            {"fiscal_year": 2024, "report_code": "11011", "account_id": "ifrs-full_Equity", "amount": Decimal(60), "amount_ytd": None, "currency": "KRW"},
        ]
        rows = {(r["fiscal_q"], r["metric"]): r["amount"] for r in quarterly.derive_dart(facts)}
        self.assertEqual([rows[(q, "revenue")] for q in (1, 2, 3, 4)], [10, 15, 20, 25])
        self.assertEqual(rows[(4, "liabilities")], 40)  # 자산 − 자본 보완

    def test_ttm_gaps(self):
        def r(m, y, mo, d):
            return {"metric": m, "period_end": date(y, mo, d)}
        full = [r("revenue", 2025, 9, 30), r("revenue", 2025, 12, 31), r("revenue", 2026, 3, 31), r("revenue", 2026, 6, 30)]
        holes = [r("net_income", 2025, 3, 31), r("net_income", 2025, 6, 30), r("net_income", 2026, 3, 31), r("net_income", 2026, 6, 30)]
        self.assertEqual(quarterly.ttm_gaps(full + holes), ["operating_income", "net_income"])
        self.assertEqual(quarterly.ttm_gaps([]), [])
        self.assertEqual(quarterly.ttm_gaps([r("assets", 2025, 12, 31)]), [])

    def test_sec_quarters(self):
        def f(code, val, end, tag="Revenues"):
            return {"fiscal_year": 2024, "report_code": code, "account_id": tag, "amount": val, "currency": "USD", "period_end": end}
        facts = [f("Q1", 20, "2024-03-31"), f("Q2", 25, "2024-06-30"), f("Q3", 30, "2024-09-30"), f("FY", 100, "2024-12-31")]
        rows = {r["fiscal_q"]: r for r in quarterly.derive_sec(facts) if r["metric"] == "revenue"}
        self.assertEqual(rows[4]["amount"], 25)
        self.assertEqual(rows[4]["period_end"], date(2024, 12, 31))

    def test_sec_parse_quarterly(self):
        payload = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
            {"form": "10-Q", "fp": "Q2", "fy": 2024, "val": 55, "start": "2024-01-01", "end": "2024-06-30", "accn": "a"},
            {"form": "10-Q", "fp": "Q2", "fy": 2024, "val": 25, "start": "2024-04-01", "end": "2024-06-30", "accn": "a"}]}}}}}
        rows = sec.parse_quarterly_facts(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["amount"], 25)


class FeatureTest(unittest.TestCase):
    def test_price_stats(self):
        closes = [100.0, 110.0, 99.0, 120.0]
        self.assertAlmostEqual(features.pct_return(closes, 1), 120 / 99 - 1)
        self.assertAlmostEqual(features.max_drawdown(closes), 99 / 110 - 1)
        self.assertIsNone(features.pct_return(closes, 10))
        self.assertIsNone(features.annualized_vol(closes, 10))

    def test_macro_rows(self):
        macro = {"ECOS_BASE_RATE": [(date(2025, 1, 1), Decimal("3.0")), (date(2026, 1, 1), Decimal("2.5"))]}
        rows = {r["feature"]: r["value"] for r in features.macro_rows(macro)}
        self.assertEqual(rows["kr_base_rate"], 2.5)
        self.assertEqual(rows["kr_base_rate_chg_12m"], -0.5)
        self.assertIsNone(rows["us_10y"])

    def test_peer_and_growth(self):
        peers = features.peer_medians([{"per_ttm": 10, "pbr": 2}, {"per_ttm": 30, "pbr": 4}, {"per_ttm": -5, "pbr": 3}])
        self.assertEqual(peers["per_ttm"], 20)
        self.assertEqual(peers["pbr"], 3)
        q = [{"metric": "revenue", "period_end": date(2026, 6, 30), "fiscal_year": 2026, "fiscal_q": 2, "amount": Decimal(150)},
             {"metric": "revenue", "period_end": date(2025, 6, 30), "fiscal_year": 2025, "fiscal_q": 2, "amount": Decimal(100)}]
        g, _ = features.growth_yoy(q, "revenue")
        self.assertAlmostEqual(g, 0.5)

    def test_compute_features_shape(self):
        prices = [{"trade_date": date(2026, 1, 1) + timedelta(days=i), "close": Decimal(100 + i % 7), "volume": 1000 + i} for i in range(300)]
        snap = {"per_ttm": 12, "pbr": 2, "ttm_end": date(2026, 6, 30), "ttm_currency": "KRW", "market_cap": 1000,
                "revenue_ttm": 500, "net_income_ttm": 50, "op_margin_ttm": 0.2, "roe_ttm": 0.1, "debt_ratio_ttm": 0.5}
        rows = features.compute_features(snap, prices, [], {}, {"per_ttm": 10, "pbr": 2, "n": 3})
        got = {(r["perspective"], r["feature"]): r["value"] for r in rows}
        self.assertAlmostEqual(got[("valuation", "per_ttm_vs_peer_median")], 0.2)
        self.assertEqual(got[("risk", "net_loss_ttm")], 0.0)
        self.assertIsNotNone(got[("risk", "volatility_250d")])
        self.assertEqual({r["perspective"] for r in rows}, {"valuation", "fundamentals", "supply_demand", "macro", "risk"})


if __name__ == "__main__":
    unittest.main()
