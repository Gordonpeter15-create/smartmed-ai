"""Checks for incoming orders, their effect on stockout risk, and the reorder arithmetic.

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
import reorder  # noqa: E402
import stockout  # noqa: E402
from inventory import InventoryError  # noqa: E402

TODAY = date.today()


class TempDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.original_path = database.DB_PATH
        database.DB_PATH = Path(self.folder.name) / "test.db"
        database.initialize_database()
        self.med = inventory.add_medicine("Test Zinc", "Rehydration", "tablets", 50, lead_time_days=7)

    def tearDown(self):
        database.DB_PATH = self.original_path
        self.folder.cleanup()


class OrderRulesTest(TempDatabaseTest):
    def test_place_receive_and_cancel(self):
        first = inventory.place_order(self.med, 100, TODAY, TODAY + timedelta(days=7))
        second = inventory.place_order(self.med, 50, TODAY, TODAY + timedelta(days=10))
        self.assertEqual(len(database.order_rows(self.med)), 2)

        inventory.receive_stock(self.med, "Z-1", 100, TODAY + timedelta(days=300), TODAY, order_id=first)
        inventory.cancel_order(second, "Supplier out of stock")
        self.assertEqual(database.order_rows(self.med), [])  # no open orders left
        statuses = {row["id"]: row["status"] for row in database.order_rows(self.med, status=None)}
        self.assertEqual(statuses, {first: "received", second: "cancelled"})

    def test_order_validation(self):
        with self.assertRaises(InventoryError):
            inventory.place_order(self.med, 0, TODAY, TODAY)
        with self.assertRaises(InventoryError):
            inventory.place_order(self.med, 10, TODAY, TODAY - timedelta(days=1))  # arrives before ordered
        with self.assertRaises(InventoryError):
            inventory.place_order(self.med, 10, TODAY + timedelta(days=1), TODAY + timedelta(days=5))  # future order date
        order = inventory.place_order(self.med, 10, TODAY, TODAY)
        with self.assertRaises(InventoryError):
            inventory.cancel_order(order, "")  # reason required
        other = inventory.add_medicine("Other", "Test", "tablets", 0)
        with self.assertRaises(InventoryError):  # order belongs to a different medicine
            inventory.receive_stock(other, "O-1", 10, TODAY + timedelta(days=100), TODAY, order_id=order)

    def test_open_orders_block_deactivation_and_deletion(self):
        inventory.place_order(self.med, 10, TODAY, TODAY + timedelta(days=3))
        with self.assertRaises(InventoryError):
            inventory.set_medicine_active(self.med, False)
        with self.assertRaises(InventoryError):
            inventory.delete_medicine(self.med)


class StockoutWithOrdersTest(TempDatabaseTest):
    def run_risk(self):
        forecast = pd.DataFrame({"medicine_id": [self.med], "forecast": [70.0], "high": [70.0]})
        past = pd.date_range(end=TODAY - timedelta(days=1), periods=56, freq="D")
        history = pd.DataFrame({"medicine_id": self.med, "day": past, "demand": 10})
        return stockout.assess_risk(forecast, history, horizon=30, today=TODAY)[0].iloc[0]

    def test_orders_on_the_way_count_but_overdue_orders_do_not(self):
        inventory.receive_stock(self.med, "Z-1", 50, TODAY + timedelta(days=300), TODAY)
        self.assertEqual(self.run_risk()["risk"], "Critical")  # 5 days of stock, 7-day lead time

        inventory.place_order(self.med, 500, TODAY - timedelta(days=1), TODAY + timedelta(days=3))
        row = self.run_risk()
        self.assertEqual(row["on_order"], 500)
        self.assertEqual(row["expected_days"], 55)  # 550 units ÷ 10 a day

        inventory.cancel_order(database.order_rows(self.med)[0]["id"], "test")
        inventory.place_order(self.med, 500, TODAY - timedelta(days=20), TODAY - timedelta(days=2))  # overdue
        row = self.run_risk()
        self.assertEqual((row["on_order"], row["overdue_on_order"]), (0, 500))
        self.assertEqual(row["risk"], "Critical")
        self.assertIn("overdue", row["reason"])


def risk_row(**changes):
    row = {
        "medicine_id": 1, "medicine": "M", "unit": "tablets", "risk": "Low", "lead_time": 10,
        "lead_time_assumed": False, "usable_stock": 500, "on_order": 0, "overdue_on_order": 0,
        "forecast_week": 70.0,  # 10 a day
    }
    return {**row, **changes}


class ReorderArithmeticTest(unittest.TestCase):
    def suggest(self, **changes):
        return reorder.suggest_reorders(pd.DataFrame([risk_row(**changes)]), safety_days=5, cover_days=30,
                                        today=date(2026, 1, 1)).iloc[0]

    def test_reorder_point_and_quantity(self):
        row = self.suggest(usable_stock=100)
        self.assertEqual(row["reorder_point"], 150)        # 10/day × 10 days + 10/day × 5 safety days
        self.assertEqual(row["priority"], "Reorder now")
        self.assertEqual(row["quantity"], 350)              # 150 + 10 × 30 − 100
        self.assertEqual(row["reorder_date"], date(2026, 1, 1))

    def test_stock_on_order_raises_the_inventory_position(self):
        row = self.suggest(usable_stock=100, on_order=200)
        self.assertEqual(row["position"], 300)
        self.assertEqual(row["days_until_reorder"], 15)    # (300 − 150) ÷ 10
        self.assertEqual(row["priority"], "Not yet")

    def test_soon_urgent_and_unknown(self):
        self.assertEqual(self.suggest(usable_stock=250)["priority"], "Soon")   # 10 days until the reorder point
        urgent = self.suggest(usable_stock=20, overdue_on_order=500, risk="Critical")
        self.assertEqual(urgent["priority"], "Urgent")
        self.assertIn("Chase the overdue order", urgent["action"])
        self.assertEqual(self.suggest(risk="Unknown", forecast_week=None)["priority"], "Unknown")
        high = self.suggest(usable_stock=100, on_order=500, risk="High")  # covered by orders, but at risk
        self.assertEqual(high["priority"], "Not yet")
        self.assertIn("deliver sooner", high["action"])


if __name__ == "__main__":
    unittest.main()
