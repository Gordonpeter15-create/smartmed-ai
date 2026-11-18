"""Automated checks for the inventory rules.

Run from the smartmed_ai folder:
    python -m unittest discover tests

Each test uses a fresh, temporary database, so your demo data is never touched.
"""
from datetime import date, timedelta
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
import inventory  # noqa: E402
from inventory import InventoryError  # noqa: E402

TODAY = date.today()


class InventoryRulesTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.original_path = database.DB_PATH
        database.DB_PATH = Path(self.folder.name) / "test.db"
        database.initialize_database()
        self.med = inventory.add_medicine("Test Paracetamol", "Analgesic", "tablets", 50)

    def tearDown(self):
        database.DB_PATH = self.original_path
        self.folder.cleanup()

    def stock(self):
        return sum(batch["quantity"] for batch in database.batch_rows(self.med))

    def test_receive_creates_batch_and_transaction(self):
        inventory.receive_stock(self.med, "b-1", 100, TODAY + timedelta(days=300), TODAY)
        self.assertEqual(self.stock(), 100)
        self.assertEqual(database.batch_rows(self.med)[0]["batch_code"], "B-1")  # stored in capitals
        self.assertEqual(database.transaction_rows(self.med)[0]["transaction_type"], "receipt")
        self.assertEqual(database.reconciliation_mismatches(), [])

    def test_receive_rejects_bad_input(self):
        with self.assertRaises(InventoryError):
            inventory.receive_stock(self.med, "B-1", 0, TODAY + timedelta(days=30), TODAY)
        with self.assertRaises(InventoryError):
            inventory.receive_stock(self.med, "B-1", 10, TODAY, TODAY)  # already expired
        with self.assertRaises(InventoryError):
            inventory.receive_stock(self.med, "B-1", 10, TODAY + timedelta(days=30), TODAY + timedelta(days=1))

    def test_dispense_uses_first_expiry_first_and_skips_expired(self):
        inventory.receive_stock(self.med, "LATE", 50, TODAY + timedelta(days=400), TODAY - timedelta(days=10))
        inventory.receive_stock(self.med, "EARLY", 30, TODAY + timedelta(days=60), TODAY - timedelta(days=10))
        inventory.receive_stock(self.med, "OLD", 20, TODAY + timedelta(days=5), TODAY - timedelta(days=10))
        taken = inventory.dispense_stock(self.med, 40, TODAY, 0)
        self.assertEqual(taken, [("OLD", 20), ("EARLY", 20)])  # earliest expiry first, LATE untouched

    def test_dispense_never_uses_an_expired_batch(self):
        inventory.receive_stock(self.med, "SOON", 20, TODAY + timedelta(days=1), TODAY - timedelta(days=5))
        with database.connect() as connection:  # simulate time passing
            connection.execute("UPDATE batches SET expiry_date = ? WHERE batch_code = 'SOON'", (TODAY.isoformat(),))
        with self.assertRaises(InventoryError):
            inventory.dispense_stock(self.med, 1, TODAY)

    def test_cannot_dispense_more_than_available_and_nothing_changes(self):
        inventory.receive_stock(self.med, "B-1", 10, TODAY + timedelta(days=100), TODAY)
        with self.assertRaises(InventoryError):
            inventory.dispense_stock(self.med, 11, TODAY)
        self.assertEqual(self.stock(), 10)
        self.assertEqual(len(database.transaction_rows(self.med)), 1)

    def test_unmet_requests_are_recorded_even_with_no_stock(self):
        inventory.dispense_stock(self.med, 0, TODAY, unmet_quantity=15, note="stockout")
        self.assertEqual(database.unmet_request_rows(self.med)[0]["quantity"], 15)

    def test_adjustment_needs_reason_and_cannot_go_negative(self):
        inventory.receive_stock(self.med, "B-1", 10, TODAY + timedelta(days=100), TODAY)
        batch_id = database.batch_rows(self.med)[0]["id"]
        with self.assertRaises(InventoryError):
            inventory.adjust_stock(batch_id, -2, "", TODAY)
        with self.assertRaises(InventoryError):
            inventory.adjust_stock(batch_id, -2, "Other", TODAY, note="")
        with self.assertRaises(InventoryError):
            inventory.adjust_stock(batch_id, -11, "Damaged", TODAY)
        inventory.adjust_stock(batch_id, -2, "Damaged", TODAY, note="dropped box")
        self.assertEqual(self.stock(), 8)
        self.assertEqual(database.reconciliation_mismatches(), [])

    def test_duplicate_names_are_rejected_ignoring_capitals(self):
        with self.assertRaises(InventoryError):
            inventory.add_medicine("test paracetamol", "Analgesic", "tablets", 10)

    def test_unit_is_locked_after_stock_is_recorded(self):
        inventory.update_medicine(self.med, "Test Paracetamol", "Analgesic", "caplets", 50)  # allowed: no stock yet
        inventory.receive_stock(self.med, "B-1", 10, TODAY + timedelta(days=100), TODAY)
        with self.assertRaises(InventoryError):
            inventory.update_medicine(self.med, "Test Paracetamol", "Analgesic", "tablets", 50)

    def test_deactivate_and_delete_rules(self):
        inventory.receive_stock(self.med, "B-1", 10, TODAY + timedelta(days=100), TODAY)
        with self.assertRaises(InventoryError):
            inventory.set_medicine_active(self.med, False)  # still has stock
        batch_id = database.batch_rows(self.med)[0]["id"]
        inventory.adjust_stock(batch_id, -10, "Returned to supplier", TODAY)
        inventory.set_medicine_active(self.med, False)
        with self.assertRaises(InventoryError):
            inventory.receive_stock(self.med, "B-2", 5, TODAY + timedelta(days=100), TODAY)  # inactive
        with self.assertRaises(InventoryError):
            inventory.delete_medicine(self.med)  # has history
        spare = inventory.add_medicine("Typo medicine", "Other", "tablets", 0)
        inventory.delete_medicine(spare)  # no records, so deleting is safe

    def test_opening_balances_explain_old_batches(self):
        with database.connect() as connection:  # a batch from before transactions existed
            connection.execute(
                "INSERT INTO batches (medicine_id, batch_code, quantity, expiry_date) VALUES (?, 'OLD-1', 40, '2030-01-01')",
                (self.med,),
            )
        self.assertEqual(len(database.reconciliation_mismatches()), 1)
        database.initialize_database()  # the upgrade step adds the opening balance
        self.assertEqual(database.reconciliation_mismatches(), [])
        database.initialize_database()  # running it again must not add a second one
        self.assertEqual(len(database.transaction_rows(self.med)), 1)


if __name__ == "__main__":
    unittest.main()
