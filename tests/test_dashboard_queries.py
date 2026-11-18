"""Checks for the queries behind the dashboard alerts and charts.

Run from the smartmed_ai folder:
    python -m unittest discover tests
"""
from datetime import date, timedelta
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
import inventory  # noqa: E402

TODAY = date.today()


class DashboardQueriesTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.original_path = database.DB_PATH
        database.DB_PATH = Path(self.folder.name) / "test.db"
        database.initialize_database()
        self.med = inventory.add_medicine("Test Amoxicillin", "Antibiotic", "capsules", 20)
        inventory.receive_stock(self.med, "SOON", 10, TODAY + timedelta(days=30), TODAY - timedelta(days=5))
        inventory.receive_stock(self.med, "LATER", 10, TODAY + timedelta(days=200), TODAY - timedelta(days=5))

    def tearDown(self):
        database.DB_PATH = self.original_path
        self.folder.cleanup()

    def test_expiring_batches_respect_the_window(self):
        self.assertEqual([row["batch_code"] for row in database.expiring_batch_rows(90)], ["SOON"])
        self.assertEqual([row["batch_code"] for row in database.expiring_batch_rows(365)], ["SOON", "LATER"])
        self.assertEqual(database.expiring_batch_rows(90)[0]["days_left"], 30)

    def test_expired_batches_with_stock_are_included_with_negative_days(self):
        with database.connect() as connection:
            connection.execute("UPDATE batches SET expiry_date = ? WHERE batch_code = 'SOON'",
                               ((TODAY - timedelta(days=3)).isoformat(),))
        row = database.expiring_batch_rows(90)[0]
        self.assertEqual((row["batch_code"], row["days_left"]), ("SOON", -3))

    def test_emptied_batches_are_left_out(self):
        soon = next(b for b in database.batch_rows(self.med) if b["batch_code"] == "SOON")
        inventory.adjust_stock(soon["id"], -10, "Damaged", TODAY)
        self.assertEqual(database.expiring_batch_rows(90), [])

    def test_recorded_dispensing_is_totalled_per_day(self):
        inventory.dispense_stock(self.med, 12, TODAY)  # takes 10 from SOON and 2 from LATER
        inventory.dispense_stock(self.med, 3, TODAY - timedelta(days=1))
        rows = [(row["day"], row["dispensed"]) for row in database.recorded_dispensing_rows(self.med)]
        self.assertEqual(rows, [((TODAY - timedelta(days=1)).isoformat(), 3), (TODAY.isoformat(), 12)])


if __name__ == "__main__":
    unittest.main()
