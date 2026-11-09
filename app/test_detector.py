from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from app.detector import build_analysis, normalize_merchant, parse_csv


SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample_transactions.csv"
TODAY = date(2026, 11, 10)


class DetectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.transactions, cls.meta = parse_csv(SAMPLE.read_text(encoding="utf-8"), SAMPLE.name)
        cls.analysis = build_analysis(cls.transactions, cls.meta, today=TODAY)
        cls.by_name = {sub["merchant"]: sub for sub in cls.analysis["subscriptions"]}

    def test_finds_the_expected_subscriptions(self):
        self.assertEqual(self.analysis["metrics"]["active_count"], 10)
        self.assertIn("Netflix", self.by_name)
        self.assertIn("Adobe Creative Cloud", self.by_name)
        self.assertIn("Amazon Prime", self.by_name)

    def test_monthly_and_yearly_cadence(self):
        self.assertEqual(self.by_name["Netflix"]["cadence"], "monthly")
        self.assertEqual(self.by_name["Amazon Prime"]["cadence"], "yearly")
        self.assertAlmostEqual(self.by_name["Amazon Prime"]["annual_cost"], 1499.0)

    def test_detects_a_price_increase(self):
        spotify = self.by_name["Spotify"]
        self.assertEqual(spotify["price_increase"]["from"], 99.0)
        self.assertEqual(spotify["price_increase"]["to"], 119.0)
        self.assertEqual(spotify["price_increase"]["percent"], 20)

    def test_does_not_call_noise_a_subscription(self):
        names = set(self.by_name)
        self.assertNotIn("Swiggy Order", names)
        self.assertNotIn("Uber Ride", names)
        self.assertNotIn("Bigbasket Grocery", names)

    def test_renewals_are_in_the_future(self):
        for sub in self.analysis["subscriptions"]:
            renewal = date.fromisoformat(sub["next_renewal"])
            self.assertGreaterEqual(renewal, TODAY)

    def test_metrics_are_consistent(self):
        metrics = self.analysis["metrics"]
        self.assertGreater(metrics["annual_total"], 0)
        self.assertAlmostEqual(
            metrics["annual_total"],
            sum(sub["annual_cost"] for sub in self.analysis["subscriptions"]),
            places=2,
        )

    def test_merchant_normalisation(self):
        self.assertEqual(normalize_merchant("UPI-NETFLIX SUBSCRIPTION 882191"), "NETFLIX")
        self.assertEqual(normalize_merchant("ADOBE CREATIVE CLOUD*IN12345"), "ADOBE CREATIVE CLOUD")

    def test_rejects_an_unusable_csv(self):
        with self.assertRaises(ValueError):
            parse_csv("hello,world\n1,2\n")


if __name__ == "__main__":
    unittest.main()
