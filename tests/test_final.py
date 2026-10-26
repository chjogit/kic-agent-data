"""최종 정리에서 추가한 기능 테스트 (DB·네트워크 불필요)."""
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from kic_data import features, status
from kic_data.universe import KR_UNIVERSE, PEER_THEMES, US_UNIVERSE


class FxValuationTest(unittest.TestCase):
    macro = {"DEXUSEU": [(date(2026, 10, 1), Decimal("1.10")), (date(2026, 10, 16), Decimal("1.20"))],
             "DEXTAUS": [(date(2026, 10, 16), Decimal("32.0"))]}

    def test_usd_per_unit(self):
        self.assertEqual(features.usd_per_unit(self.macro, "USD"), (1.0, None))
        self.assertEqual(features.usd_per_unit(self.macro, "EUR"), (1.2, date(2026, 10, 16)))
        self.assertAlmostEqual(features.usd_per_unit(self.macro, "TWD")[0], 1 / 32)
        self.assertIsNone(features.usd_per_unit(self.macro, "JPY"))
        self.assertIsNone(features.usd_per_unit({}, "EUR"))

    def test_fill_eur(self):
        snap = {"market": "US", "fin_currency": "EUR", "market_cap": 300.0, "per_annual": None,
                "net_income_annual": Decimal("10"), "equity_annual": Decimal("50")}
        out = features.fill_fx_valuation(snap, self.macro)
        self.assertAlmostEqual(out["per_annual"], 300 / 12)   # 10 EUR = 12 USD
        self.assertAlmostEqual(out["pbr_annual"], 300 / 60)
        self.assertIn("EUR→USD", out["fx_note"])
        self.assertIsNone(snap["per_annual"])  # 원본은 그대로

    def test_fill_twd_and_loss(self):
        snap = {"market": "US", "fin_currency": "TWD", "market_cap": 100.0, "per_annual": None,
                "net_income_annual": -320, "equity_annual": 3200}
        out = features.fill_fx_valuation(snap, self.macro)
        self.assertIsNone(out["per_annual"])           # 적자면 PER 비움
        self.assertAlmostEqual(out["pbr_annual"], 1.0)   # 3200 TWD = 100 USD

    def test_skip_when_not_needed(self):
        for snap in ({"market": "KR", "fin_currency": "KRW", "market_cap": 1, "per_annual": None},
                     {"market": "US", "fin_currency": "USD", "market_cap": 1, "per_annual": None},
                     {"market": "US", "fin_currency": "EUR", "market_cap": 1, "per_annual": 5.0}):
            self.assertIs(features.fill_fx_valuation(snap, self.macro), snap)

    def test_compute_uses_annual_pbr_and_note(self):
        snap = {"market": "US", "fin_currency": "EUR", "market_cap": 300.0, "per_annual": 25.0, "pbr_annual": 5.0,
                "fx_note": "EUR→USD 환산", "fiscal_year": 2025}
        rows = {r["feature"]: r for r in features.compute_features(snap, [], [], {}, {"pbr": 4.0, "n": 3})}
        self.assertEqual(rows["pbr"]["value"], 5.0)
        self.assertIn("EUR→USD", rows["pbr"]["note"])
        self.assertAlmostEqual(rows["pbr_vs_peer_median"]["value"], 0.25)

    def test_peer_median_pbr_fallback(self):
        peers = features.peer_medians([{"pbr": None, "pbr_annual": 6.0}, {"pbr": 2.0}, {"pbr": 4.0}])
        self.assertEqual(peers["pbr"], 4.0)


class UniverseTest(unittest.TestCase):
    def test_every_stock_has_a_peer_theme(self):
        members = {tuple(k) for spec in PEER_THEMES.values() for k in spec["members"]}
        for market, uni in (("KR", KR_UNIVERSE), ("US", US_UNIVERSE)):
            for ticker, _ in uni:
                self.assertIn((market, ticker), members, f"{market}:{ticker} 비교군 없음")

    def test_bases_are_members(self):
        for spec in PEER_THEMES.values():
            for b in spec["bases"]:
                self.assertIn(b, spec["members"])


class StatusTest(unittest.TestCase):
    def test_freshness(self):
        today = date(2026, 10, 22)
        self.assertEqual(status.freshness(None, today, 3), "없음")
        self.assertEqual(status.freshness(date(2026, 10, 20), today, 3), "OK")
        self.assertEqual(status.freshness(date(2026, 10, 1), today, 3), "오래됨(21일 전)")
        self.assertEqual(status.freshness(datetime(2026, 10, 22, 1, tzinfo=timezone.utc), today, 0), "OK")

    def test_render_empty_and_filled(self):
        st = {"checked_at": "2026-10-22", "companies": [], "indices": [], "macro": [], "documents": [],
              "disclosures": [], "trend_topics": [], "peer_groups": []}
        text = status.render(st)
        self.assertIn("[종목] 0개", text)
        self.assertIn("없음 (collect-indices)", text)
        st["companies"] = [{"market": "KR", "ticker": "005930", "name": "삼성전자", "corp_code": True, "cik": False,
                            "sector": True, "shares": True, "price_rows": 10, "price_from": date(2026, 10, 1),
                            "price_to": date(2026, 10, 21), "news_docs": 5, "news_last": date(2026, 10, 22),
                            "features_filled": 40, "features_total": 50, "feature_as_of": date(2026, 10, 22)}]
        text = status.render(st)
        self.assertIn("KR:005930", text)
        self.assertIn("40/50", text)
        self.assertIn("DART 고유번호 1/1", text)


if __name__ == "__main__":
    unittest.main()
