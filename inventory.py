"""Inventory rules for SmartMed AI.

Stock totals are never overwritten. Every change to a batch quantity is made
together with a row in stock_transactions that says what happened, when, and
why. Both are saved in ONE database transaction: if anything fails, neither is
saved. That way the current stock can always be rebuilt from its history.

Each function checks its input first and raises InventoryError with a
message that is safe to show on screen.
"""
from datetime import date
import sqlite3

from database import connect

# Batches expiring within this many days are flagged as "expiring soon" by default.
EXPIRY_WARNING_DAYS = 90

ADJUSTMENT_REASONS = [
    "Stock count correction",
    "Damaged",
    "Expired stock removed",
    "Lost or missing",
    "Returned to supplier",
    "Other",
]


class InventoryError(ValueError):
    """A problem with the user's input. The message can be shown to the user."""


# ---------- small helpers ----------

def _clean(text: str | None) -> str:
    return (text or "").strip()


def _whole_number(value, label: str) -> int:
    if isinstance(value, bool) or int(value) != value:
        raise InventoryError(f"{label} must be a whole number.")
    return int(value)


def _not_in_future(day: date, label: str) -> None:
    if day > date.today():
        raise InventoryError(f"{label} cannot be in the future.")


def _get_medicine(connection: sqlite3.Connection, medicine_id: int, must_be_active: bool = True) -> sqlite3.Row:
    medicine = connection.execute("SELECT * FROM medicines WHERE id = ?", (medicine_id,)).fetchone()
    if medicine is None:
        raise InventoryError("That medicine does not exist.")
    if must_be_active and not medicine["active"]:
        raise InventoryError(f"{medicine['name']} is deactivated. Reactivate it on the Medicines page first.")
    return medicine


def _record_change(connection, medicine_id, batch_id, day, kind, change, reason=None, note=None) -> None:
    """Change a batch quantity AND write the transaction that explains it."""
    connection.execute("UPDATE batches SET quantity = quantity + ? WHERE id = ?", (change, batch_id))
    connection.execute(
        """INSERT INTO stock_transactions
           (medicine_id, batch_id, transaction_date, transaction_type, quantity_change, reason, note)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (medicine_id, batch_id, day.isoformat(), kind, change, reason, _clean(note) or None),
    )


# ---------- medicine catalogue ----------

def _validate_medicine(connection, name, category, unit, reorder_level, lead_time_days=None, medicine_id=None):
    name, category, unit = _clean(name), _clean(category), _clean(unit)
    if not name:
        raise InventoryError("Medicine name is required.")
    if not category:
        raise InventoryError("Category is required.")
    if not unit:
        raise InventoryError("Unit is required (for example: tablets, vials, bottles).")
    reorder_level = _whole_number(reorder_level, "Reorder level")
    if reorder_level < 0:
        raise InventoryError("Reorder level cannot be negative.")
    if lead_time_days is not None:
        lead_time_days = _whole_number(lead_time_days, "Lead time")
        if not 1 <= lead_time_days <= 365:
            raise InventoryError("Lead time must be between 1 and 365 days (or left empty if unknown).")
    duplicate = connection.execute(
        "SELECT name FROM medicines WHERE lower(name) = lower(?) AND id IS NOT ?", (name, medicine_id)
    ).fetchone()
    if duplicate:
        raise InventoryError(f"A medicine called “{duplicate['name']}” already exists.")
    return name, category, unit, reorder_level, lead_time_days


def add_medicine(
    name: str, category: str, unit: str, reorder_level: int, lead_time_days: int | None = None
) -> int:
    """Register a new medicine and return its id. Lead time may be left empty (None)."""
    with connect() as connection:
        values = _validate_medicine(connection, name, category, unit, reorder_level, lead_time_days)
        return connection.execute(
            "INSERT INTO medicines (name, category, unit, reorder_level, lead_time_days) VALUES (?, ?, ?, ?, ?)",
            values,
        ).lastrowid


def update_medicine(
    medicine_id: int, name: str, category: str, unit: str, reorder_level: int, lead_time_days: int | None = None
) -> None:
    """Edit a medicine's details. The unit is locked once stock has been recorded."""
    with connect() as connection:
        medicine = _get_medicine(connection, medicine_id, must_be_active=False)
        values = _validate_medicine(connection, name, category, unit, reorder_level, lead_time_days, medicine_id)
        has_stock_history = connection.execute(
            "SELECT 1 FROM batches WHERE medicine_id = ? LIMIT 1", (medicine_id,)
        ).fetchone()
        if values[2] != medicine["unit"] and has_stock_history:
            raise InventoryError(
                "The unit cannot be changed after stock has been recorded, because existing "
                "quantities would change meaning. Register a new medicine instead."
            )
        connection.execute(
            "UPDATE medicines SET name = ?, category = ?, unit = ?, reorder_level = ?, lead_time_days = ? WHERE id = ?",
            (*values, medicine_id),
        )


def set_medicine_active(medicine_id: int, active: bool) -> None:
    """Deactivate (hide from stock forms and dashboard) or reactivate a medicine."""
    with connect() as connection:
        medicine = _get_medicine(connection, medicine_id, must_be_active=False)
        if not active:
            stock = connection.execute(
                "SELECT COALESCE(SUM(quantity), 0) FROM batches WHERE medicine_id = ?", (medicine_id,)
            ).fetchone()[0]
            if stock > 0:
                raise InventoryError(
                    f"{medicine['name']} still has {stock} {medicine['unit']} in stock. Dispense, return, "
                    "or adjust that stock out (with a reason) before deactivating it."
                )
            if connection.execute(
                "SELECT 1 FROM incoming_orders WHERE medicine_id = ? AND status = 'open' LIMIT 1", (medicine_id,)
            ).fetchone():
                raise InventoryError(
                    f"{medicine['name']} has open incoming orders. Receive or cancel them before deactivating it."
                )
        connection.execute("UPDATE medicines SET active = ? WHERE id = ?", (int(active), medicine_id))


def delete_medicine(medicine_id: int) -> None:
    """Permanently delete a medicine that has NO stock, history, or requests.

    Medicines with any records must be deactivated instead, so history is kept.
    """
    with connect() as connection:
        medicine = _get_medicine(connection, medicine_id, must_be_active=False)
        for table in ("batches", "stock_transactions", "daily_consumption", "unmet_requests", "incoming_orders"):
            if connection.execute(f"SELECT 1 FROM {table} WHERE medicine_id = ? LIMIT 1", (medicine_id,)).fetchone():
                raise InventoryError(
                    f"{medicine['name']} has stock or history records, so it cannot be deleted. Deactivate it instead."
                )
        connection.execute("DELETE FROM medicines WHERE id = ?", (medicine_id,))


# ---------- stock movements ----------

def receive_stock(
    medicine_id: int, batch_code: str, quantity: int, expiry_date: date, received_date: date, note: str = "",
    order_id: int | None = None,
) -> str:
    """Record a delivery. Creates the batch, or adds to an existing batch with the same code.

    If `order_id` is given, that open incoming order is marked as received.
    """
    batch_code = _clean(batch_code).upper()
    quantity = _whole_number(quantity, "Quantity")
    if not batch_code:
        raise InventoryError("Batch code is required. It is printed on the medicine packaging.")
    if quantity <= 0:
        raise InventoryError("Quantity received must be more than zero.")
    _not_in_future(received_date, "Date received")
    if expiry_date <= received_date:
        raise InventoryError("Expiry date must be after the date received. Do not receive expired stock.")

    with connect() as connection:
        medicine = _get_medicine(connection, medicine_id)
        if order_id is not None:
            order = connection.execute("SELECT * FROM incoming_orders WHERE id = ?", (order_id,)).fetchone()
            if order is None or order["medicine_id"] != medicine_id or order["status"] != "open":
                raise InventoryError("That order is not an open order for this medicine.")
            if received_date.isoformat() < order["order_date"]:
                raise InventoryError("The delivery cannot be received before the order was placed.")
            connection.execute(
                "UPDATE incoming_orders SET status = 'received', closed_date = ? WHERE id = ?",
                (received_date.isoformat(), order_id),
            )
        batch = connection.execute("SELECT * FROM batches WHERE batch_code = ?", (batch_code,)).fetchone()
        if batch is None:
            batch_id = connection.execute(
                "INSERT INTO batches (medicine_id, batch_code, quantity, expiry_date) VALUES (?, ?, 0, ?)",
                (medicine_id, batch_code, expiry_date.isoformat()),
            ).lastrowid
        elif batch["medicine_id"] != medicine_id:
            raise InventoryError(f"Batch code {batch_code} is already used by a different medicine.")
        elif batch["expiry_date"] != expiry_date.isoformat():
            raise InventoryError(
                f"Batch {batch_code} already exists with expiry {batch['expiry_date']}. Check the expiry date or batch code."
            )
        else:
            batch_id = batch["id"]
        _record_change(connection, medicine_id, batch_id, received_date, "receipt", quantity, note=note)
    message = f"Received {quantity} {medicine['unit']} of {medicine['name']} into batch {batch_code}."
    if order_id is not None and quantity != order["quantity"]:
        message += f" Order #{order_id} was for {order['quantity']}; it is now closed as received."
    elif order_id is not None:
        message += f" Order #{order_id} marked as received."
    return message


# ---------- incoming orders ----------

def place_order(medicine_id: int, quantity: int, order_date: date, expected_arrival: date, note: str = "") -> int:
    """Record an order placed with a supplier. It counts as stock on order until received or cancelled."""
    quantity = _whole_number(quantity, "Quantity ordered")
    if quantity <= 0:
        raise InventoryError("Quantity ordered must be more than zero.")
    _not_in_future(order_date, "Order date")
    if expected_arrival < order_date:
        raise InventoryError("Expected arrival cannot be before the order date.")
    with connect() as connection:
        _get_medicine(connection, medicine_id)
        return connection.execute(
            "INSERT INTO incoming_orders (medicine_id, quantity, order_date, expected_arrival, note) VALUES (?, ?, ?, ?, ?)",
            (medicine_id, quantity, order_date.isoformat(), expected_arrival.isoformat(), _clean(note) or None),
        ).lastrowid


def cancel_order(order_id: int, reason: str, cancelled_on: date | None = None) -> None:
    """Cancel an open order. A reason is required and kept with the order."""
    reason = _clean(reason)
    if not reason:
        raise InventoryError("Give a reason for cancelling the order.")
    cancelled_on = cancelled_on or date.today()
    with connect() as connection:
        order = connection.execute("SELECT * FROM incoming_orders WHERE id = ?", (order_id,)).fetchone()
        if order is None or order["status"] != "open":
            raise InventoryError("Only open orders can be cancelled.")
        note = "; ".join(part for part in (order["note"], f"Cancelled: {reason}") if part)
        connection.execute(
            "UPDATE incoming_orders SET status = 'cancelled', closed_date = ?, note = ? WHERE id = ?",
            (cancelled_on.isoformat(), note, order_id),
        )


def usable_stock(medicine_id: int, on_date: date) -> int:
    """Units in batches that have not expired by `on_date`."""
    with connect() as connection:
        return connection.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM batches WHERE medicine_id = ? AND quantity > 0 AND expiry_date > ?",
            (medicine_id, on_date.isoformat()),
        ).fetchone()[0]


def dispense_stock(
    medicine_id: int, quantity: int, dispense_date: date, unmet_quantity: int = 0, note: str = ""
) -> list[tuple[str, int]]:
    """Dispense stock First Expiry, First Out (FEFO) and return [(batch code, units taken)].

    `unmet_quantity` records units that were requested but could not be supplied.
    Expired batches are never dispensed.
    """
    quantity = _whole_number(quantity, "Quantity dispensed")
    unmet_quantity = _whole_number(unmet_quantity, "Unmet quantity")
    if quantity < 0 or unmet_quantity < 0:
        raise InventoryError("Quantities cannot be negative.")
    if quantity == 0 and unmet_quantity == 0:
        raise InventoryError("Enter a quantity dispensed, an unmet quantity, or both.")
    _not_in_future(dispense_date, "Date dispensed")

    with connect() as connection:
        medicine = _get_medicine(connection, medicine_id)
        batches = connection.execute(
            """SELECT id, batch_code, quantity FROM batches
               WHERE medicine_id = ? AND quantity > 0 AND expiry_date > ?
               ORDER BY expiry_date, id""",
            (medicine_id, dispense_date.isoformat()),
        ).fetchall()
        available = sum(batch["quantity"] for batch in batches)
        if quantity > available:
            raise InventoryError(
                f"Only {available} {medicine['unit']} of {medicine['name']} are usable (not expired) on "
                f"{dispense_date.isoformat()}. Dispense at most {available} and record the rest as unmet."
            )

        taken: list[tuple[str, int]] = []
        remaining = quantity
        for batch in batches:
            if remaining == 0:
                break
            take = min(batch["quantity"], remaining)
            _record_change(connection, medicine_id, batch["id"], dispense_date, "dispense", -take, note=note)
            taken.append((batch["batch_code"], take))
            remaining -= take

        if unmet_quantity:
            connection.execute(
                "INSERT INTO unmet_requests (medicine_id, request_date, quantity, note) VALUES (?, ?, ?, ?)",
                (medicine_id, dispense_date.isoformat(), unmet_quantity, _clean(note) or None),
            )
    return taken


def adjust_stock(batch_id: int, quantity_change: int, reason: str, adjustment_date: date, note: str = "") -> str:
    """Correct a batch quantity up or down. A reason is always required."""
    quantity_change = _whole_number(quantity_change, "Quantity change")
    if quantity_change == 0:
        raise InventoryError("The adjustment must change the quantity (it cannot be zero).")
    if reason not in ADJUSTMENT_REASONS:
        raise InventoryError("Choose a reason for the adjustment.")
    if reason == "Other" and not _clean(note):
        raise InventoryError("Explain the adjustment in the note when the reason is “Other”.")
    _not_in_future(adjustment_date, "Adjustment date")

    with connect() as connection:
        batch = connection.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if batch is None:
            raise InventoryError("That batch does not exist.")
        medicine = _get_medicine(connection, batch["medicine_id"])
        new_quantity = batch["quantity"] + quantity_change
        if new_quantity < 0:
            raise InventoryError(
                f"Batch {batch['batch_code']} only has {batch['quantity']} {medicine['unit']}; "
                "stock cannot go below zero."
            )
        _record_change(connection, medicine["id"], batch_id, adjustment_date, "adjustment", quantity_change, reason, note)
    return f"Batch {batch['batch_code']} adjusted by {quantity_change:+} {medicine['unit']} (now {new_quantity})."
