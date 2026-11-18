"""Checks for the stock simulation and risk rules (no database needed, except the last test).

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
import inventory  # noqa: E402
import stockout  # noqa: E402
from stockout import Delivery, classify, simulate_stock  # noqa: E402

START = date(2026, 1, 5)  # a Monday
FAR = START + timedelta(days=1000)


class SimulationTest(unittest.TestCase):
    def test_constant_demand_runs_out_on_the_expected_day(self):
        result = simulate_stock([(100, FAR)], [10] * 30, START)
        self.assertEqual(result.stockout_day, 10)  # days 0-9 use 100 units; day 10 is short
        self.assertEqual(result.balances[:3], [90, 80, 70])

    def test_expiring_batch_makes_stock_run_out_sooner(self):
        result = simulate_stock([(50, START + timedelta(days=2)), (50, FAR)], [10] * 30, START)
        # FEFO uses the early batch first (20 units on days 0-1), the other 30 expire on day 2.
        self.assertEqual(result.expired_units, 30)
        self.assertEqual(result.stockout_day, 7)

    def test_confirmed_delivery_delays_the_stockout(self):
        delivery = Delivery(START + timedelta(days=5), 100, FAR)
        result = simulate_stock([(100, FAR)], [10] * 30, START, [delivery])
        self.assertEqual(result.stockout_day, 20)

    def test_no_stockout_within_the_simulation(self):
        self.assertIsNone(simulate_stock([(1000, FAR)], [1] * 30, START).stockout_day)

    def test_weekday_shares_follow_recent_history(self):
        days = pd.date_range("2026-01-05", periods=56, freq="D")  # starts on a Monday
        demand = [20 if day.weekday() == 0 else 0 for day in days]
        history = pd.DataFrame({"medicine_id": 1, "day": days, "demand": demand})
        shares = stockout.weekday_shares(history)[1]
        self.assertEqual(shares[0], 1.0)
        self.assertEqual(stockout.daily_demand(70, shares, START, 7), [70, 0, 0, 0, 0, 0, 0])


class ClassifyTest(unittest.TestCase):
    def test_risk_levels(self):
        self.assertEqual(classify(0, 10, 14, 0, 0, 30)[0], "Critical")        # no stock
        self.assertEqual(classify(100, 10, 14, 12, 8, 30)[0], "Critical")     # runs out within lead time
        self.assertEqual(classify(50, 60, 14, 20, 15, 30)[0], "High")         # at/below reorder level
        self.assertEqual(classify(100, 10, 14, 20, 13, 30)[0], "High")        # busy scenario within lead time
        self.assertEqual(classify(100, 10, 14, 25, 18, 30)[0], "Medium")      # within horizon, after lead time
        self.assertEqual(classify(100, 10, 14, None, 40, 30)[0], "Low")       # lasts beyond the horizon
        self.assertIn("1 day,", classify(100, 10, 14, 1, 1, 30)[1])


class AssessRiskTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.original_path = database.DB_PATH
        database.DB_PATH = Path(self.folder.name) / "test.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_PATH = self.original_path
        self.folder.cleanup()

    def test_assess_risk_uses_usable_stock_and_flags_missing_lead_time(self):
        today = date.today()
        known = inventory.add_medicine("Known", "Test", "tablets", 10, lead_time_days=7)
        unknown = inventory.add_medicine("No lead time", "Test", "tablets", 10)
        no_history = inventory.add_medicine("New medicine", "Test", "tablets", 10, lead_time_days=7)
        inventory.receive_stock(known, "K-1", 140, today + timedelta(days=365), today)
        inventory.receive_stock(unknown, "U-1", 140, today + timedelta(days=365), today)
        forecast = pd.DataFrame({"medicine_id": [known, unknown], "forecast": [70.0, 70.0], "high": [70.0, 70.0]})
        past = pd.date_range(end=today - timedelta(days=1), periods=56, freq="D")  # 8 weeks of steady demand
        history = pd.concat(
            [pd.DataFrame({"medicine_id": medicine_id, "day": past, "demand": 10}) for medicine_id in (known, unknown)]
        )

        table, projections = stockout.assess_risk(forecast, history, horizon=30, today=today)
        by_name = table.set_index("medicine")
        self.assertEqual(by_name.loc["Known", "expected_days"], 14)   # 140 units ÷ 10 a day
        self.assertEqual(by_name.loc["Known", "risk"], "Medium")
        self.assertTrue(by_name.loc["No lead time", "lead_time_assumed"])
        self.assertEqual(by_name.loc["No lead time", "risk"], "Critical")  # 14 days ≤ assumed 14-day lead time
        self.assertEqual(by_name.loc["New medicine", "risk"], "Unknown")
        self.assertIn(known, projections)
        self.assertNotIn(no_history, projections)


if __name__ == "__main__":
    unittest.main()
