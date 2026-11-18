"""Checks for the batch expiry projection.

Run from the smartmed_ai folder:
    python -m unittest discover tests
"""
from datetime import date, timedelta
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
import expiry  # noqa: E402
import inventory  # noqa: E402
from expiry import project_batches, status_for  # noqa: E402

START = date(2026, 1, 5)


def day(n: int) -> date:
    return START + timedelta(days=n)


class ProjectBatchesTest(unittest.TestCase):
    def test_batch_used_up_before_expiry(self):
        result = project_batches([("A", 50, day(30))], [10] * 40, START)
        self.assertEqual(result["A"], {"used_up_day": 4, "expires_unused": 0.0})

    def test_leftover_on_expiry_is_reported(self):
        result = project_batches([("A", 100, day(5))], [10] * 40, START)
        self.assertEqual(result["A"]["expires_unused"], 50)  # 5 days × 10 used, 50 left on day 5
        self.assertIsNone(result["A"]["used_up_day"])

    def test_fefo_means_later_batch_waits_for_the_earlier_one(self):
        # B expires first so it is used first; A only starts on day 3 and has 10 days left to use 100 units.
        result = project_batches([("A", 100, day(13)), ("B", 30, day(5))], [10] * 40, START)
        self.assertEqual(result["A"]["used_up_day"], 12)
        self.assertEqual(result["A"]["expires_unused"], 0.0)
        result = project_batches([("A", 100, day(10)), ("B", 30, day(5))], [10] * 40, START)
        self.assertEqual(result["B"]["used_up_day"], 2)
        self.assertEqual(result["A"]["expires_unused"], 30)  # starts day 3, expires day 10: 70 used

    def test_zero_demand_means_everything_expires_unused(self):
        result = project_batches([("A", 20, day(3))], [0] * 10, START)
        self.assertEqual(result["A"]["expires_unused"], 20)

    def test_status_rules(self):
        self.assertEqual(status_for(0, 0, 0), "Expired")
        self.assertEqual(status_for(30, 5, 10), "At risk")
        self.assertEqual(status_for(30, 0, 10), "Watch")
        self.assertEqual(status_for(30, 0, 0), "OK")


class AssessExpiryTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.original_path = database.DB_PATH
        database.DB_PATH = Path(self.folder.name) / "test.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_PATH = self.original_path
        self.folder.cleanup()

    def test_overstocked_short_dated_batch_is_flagged(self):
        today = date.today()
        med = inventory.add_medicine("Test Oxytocin", "Maternal health", "ampoules", 10, lead_time_days=21)
        inventory.receive_stock(med, "OK-1", 20, today + timedelta(days=300), today)
        inventory.receive_stock(med, "SHORT-1", 200, today + timedelta(days=40), today)
        forecast = pd.DataFrame({"medicine_id": [med], "forecast": [21.0], "low": [14.0], "high": [28.0]})
        past = pd.date_range(end=today - timedelta(days=1), periods=56, freq="D")
        history = pd.DataFrame({"medicine_id": med, "day": past, "demand": 3})

        table = expiry.assess_expiry(forecast, history, today=today).set_index("batch_code")
        short = table.loc["SHORT-1"]
        self.assertEqual(short["fefo_order"], 1)               # expires first, so used first
        self.assertEqual(short["status"], "At risk")           # 3 a day × 40 days = 120 of 200 used
        self.assertAlmostEqual(short["expected_unused"], 80, delta=3)
        self.assertGreater(short["quiet_unused"], short["expected_unused"])
        self.assertEqual(table.loc["OK-1", "status"], "OK")


if __name__ == "__main__":
    unittest.main()
