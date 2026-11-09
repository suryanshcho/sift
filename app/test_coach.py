from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from app import coach
from app.detector import build_analysis, parse_csv


SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample_transactions.csv"


class CoachTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        transactions, meta = parse_csv(SAMPLE.read_text(encoding="utf-8"), SAMPLE.name)
        cls.analysis = build_analysis(transactions, meta, today=date(2026, 11, 10))
        cls.by_name = {sub["merchant"]: sub for sub in cls.analysis["subscriptions"]}

    def test_extracts_json_from_a_fenced_response(self):
        parsed = coach.extract_json('Here you go:\n```json\n{"headline": "H", "recommendations": []}\n```')
        self.assertEqual(parsed["headline"], "H")

    def test_extracts_json_with_chatter(self):
        parsed = coach.extract_json('Sure. {"headline": "H", "summary": "S"} Hope that helps.')
        self.assertEqual(parsed["summary"], "S")

    def test_model_cannot_override_a_keep_decision(self):
        sub = self.by_name["Netflix"]
        sub["decision"] = "keep"
        fallback = coach.local_insights(self.analysis)
        raw = {
            "headline": "Test",
            "summary": "Test",
            "recommendations": [
                {"merchant": "Netflix", "action": "cancel", "reason": "Test", "confidence": "high"},
                {"merchant": "Adobe Creative Cloud", "action": "review", "reason": "Test", "confidence": "medium"},
            ],
            "watchouts": [],
        }
        result = coach._validated_ai(raw, self.analysis, fallback)
        netflix = next(item for item in result["recommendations"] if item["merchant"] == "Netflix")
        self.assertEqual(netflix["action"], "keep")
        self.assertEqual(netflix["annual_savings"], 0)

    def test_unknown_merchants_are_dropped(self):
        fallback = coach.local_insights(self.analysis)
        raw = {
            "headline": "Test",
            "summary": "Test",
            "recommendations": [
                {"merchant": "Invented Service", "action": "cancel", "reason": "Should not survive"},
            ],
            "watchouts": [],
        }
        result = coach._validated_ai(raw, self.analysis, fallback)
        self.assertNotIn("Invented Service", [item["merchant"] for item in result["recommendations"]])

    def test_local_email_is_safe_and_editable(self):
        email = coach.local_email(self.by_name["Adobe Creative Cloud"], "I no longer use it")
        self.assertIn("Adobe Creative Cloud", email["subject"])
        self.assertIn("confirm", email["body"].lower())
        self.assertIn("[Your name]", email["body"])


if __name__ == "__main__":
    unittest.main()
