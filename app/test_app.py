from __future__ import annotations

import csv
import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from app import create_app


ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "sample_transactions.csv"


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="sift-test-")
        self.data_dir = Path(self.temp)
        shutil.copy(SAMPLE, self.data_dir / "sample_transactions.csv")
        self.old_env = {
            key: os.environ.get(key)
            for key in ("SIFT_DATA_DIR", "SIFT_DB_PATH", "SIFT_SAMPLE_PATH", "AI_MODE")
        }
        os.environ["SIFT_DATA_DIR"] = str(self.data_dir)
        os.environ["SIFT_DB_PATH"] = str(self.data_dir / "sift.db")
        os.environ["SIFT_SAMPLE_PATH"] = str(self.data_dir / "sample_transactions.csv")
        os.environ["AI_MODE"] = "local"
        self.app = create_app({"TESTING": True})
        self.client = self.app.test_client()
        self.state = self.client.get("/api/state").get_json()["analysis"]
        self.sub = self.state["subscriptions"][0]

    def tearDown(self):
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_demo_is_seeded(self):
        self.assertTrue(self.state["is_demo"])
        self.assertEqual(len(self.state["subscriptions"]), 10)
        self.assertEqual(self.state["insights"]["mode"], "local")

    def test_decision_and_usage_persist(self):
        response = self.client.post(
            "/api/decision",
            json={"id": self.sub["id"], "decision": "cancel", "usage": "never"},
        )
        self.assertEqual(response.status_code, 200)
        analysis = response.get_json()["analysis"]
        changed = next(item for item in analysis["subscriptions"] if item["id"] == self.sub["id"])
        self.assertEqual(changed["decision"], "cancel")
        self.assertEqual(changed["usage"], "never")
        self.assertGreater(analysis["plan"]["confirmed_annual_savings"], 0)

        # Updating the decision must not wipe the usage signal.
        response = self.client.post("/api/decision", json={"id": self.sub["id"], "decision": "keep"})
        changed = next(
            item for item in response.get_json()["analysis"]["subscriptions"] if item["id"] == self.sub["id"]
        )
        self.assertEqual(changed["decision"], "keep")
        self.assertEqual(changed["usage"], "never")

    def test_local_ai_and_email_fallback(self):
        brief = self.client.post("/api/insights").get_json()["analysis"]["insights"]
        self.assertEqual(brief["mode"], "local")
        self.assertTrue(brief["recommendations"])

        email = self.client.post(
            "/api/email", json={"id": self.sub["id"], "reason": "I no longer use it"}
        ).get_json()["email"]
        self.assertEqual(email["mode"], "local")
        self.assertIn("cancel", email["subject"].lower())
        self.assertIn("confirm", email["body"].lower())

    def test_export_is_a_valid_csv(self):
        response = self.client.get("/api/export.csv")
        self.assertEqual(response.status_code, 200)
        rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True).lstrip("\ufeff"))))
        self.assertEqual(len(rows), 10)
        self.assertIn("merchant", rows[0])
        self.assertIn("annual_cost", rows[0])

    def test_upload_replaces_demo(self):
        csv_text = (
            "date,description,amount\n"
            "2026-08-01,NETFLIX SUBSCRIPTION,649\n"
            "2026-09-01,NETFLIX SUBSCRIPTION,649\n"
            "2026-10-01,NETFLIX SUBSCRIPTION,649\n"
            "2026-10-04,SWIGGY ORDER,450\n"
        )
        response = self.client.post(
            "/api/analyze",
            data={"file": (io.BytesIO(csv_text.encode()), "my_bank.csv")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        analysis = response.get_json()["analysis"]
        self.assertFalse(analysis["is_demo"])
        self.assertEqual(len(analysis["subscriptions"]), 1)
        self.assertEqual(analysis["subscriptions"][0]["merchant"], "Netflix")

    def test_rejects_bad_upload(self):
        response = self.client.post(
            "/api/analyze",
            data={"file": (io.BytesIO(b"not a csv"), "notes.txt")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)

    def test_health(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)


if __name__ == "__main__":
    unittest.main()
