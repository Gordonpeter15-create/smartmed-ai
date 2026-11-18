"""Inventory page: record stock movements and inspect batches and history.

Every form here calls a function in inventory.py, which saves the change
together with a transaction explaining it. Nothing overwrites stock directly.
"""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

import inventory
from database import (
    batch_rows, inventory_rows, order_rows, reconciliation_mismatches, transaction_rows, unmet_request_rows,
)
from inventory import ADJUSTMENT_REASONS, EXPIRY_WARNING_DAYS, InventoryError
from ui import badge_styles, flash, form_key, page_header, pick_medicine, show_flash, stat_card

TYPE_BADGES = {"Received": "green", "Dispensed": "blue", "Adjustment": "amber", "Opening": "slate"}
TYPE_LABELS = {
    "opening_balance": "Opening balance",
    "receipt": "Received",
    "dispense": "Dispensed",
    "adjustment": "Adjustment",
}

page_header("📦", "Inventory", "Record stock received, dispensed, and corrected. Every change is kept in the history.", "blue")
show_flash()

medicine = pick_medicine(inventory_rows(), "Working with medicine")
if medicine is None:
    st.stop()

today = date.today()
unit = medicine["unit"]
usable = inventory.usable_stock(medicine["id"], today)
open_orders = order_rows(medicine["id"])
on_order = sum(o["quantity"] for o in open_orders)
col1, col2, col3, col4 = st.columns(4)
with col1:
    stat_card(f"In stock ({unit})", f"{medicine['stock']:,}", "blue", key="in-stock")
with col2:
    stat_card(f"Usable today ({unit})", f"{usable:,}", "teal" if usable else "red", key="usable",
              help="Excludes batches that have expired.")
with col3:
    stat_card(f"On order ({unit})", f"{on_order:,}", "violet", key="on-order", help="Open incoming orders.")
with col4:
    stat_card(f"Reorder level ({unit})", f"{medicine['reorder_level']:,}",
              "amber" if usable + on_order <= medicine["reorder_level"] else "slate", key="reorder-level")

# A radio button (rather than st.tabs) remembers the chosen section after a form is submitted.
section = st.radio(
    "Section",
    ["Receive stock", "Dispense", "Adjust", "Incoming orders", "Batches", "Transaction history"],
    horizontal=True,
    label_visibility="collapsed",
    key="inventory_section",
)
st.divider()

if section == "Receive stock":
    with st.form(form_key("receive")):
        col1, col2 = st.columns(2)
        batch_code = col1.text_input("Batch code", placeholder="As printed on the packaging")
        quantity = col2.number_input(f"Quantity received ({unit})", min_value=0, step=1, value=0)
        col1, col2 = st.columns(2)
        received = col1.date_input("Date received", value=today, max_value=today)
        expiry = col2.date_input("Expiry date", value=today + timedelta(days=365), min_value=today - timedelta(days=3650))
        note = st.text_input("Note (optional)", placeholder="e.g. supplier, delivery note number")
        orders_by_id = {o["id"]: o for o in open_orders}
        order_id = st.selectbox(
            "This delivery is for order (optional)", [None, *orders_by_id],
            format_func=lambda i: "Not linked to an order" if i is None else
            f"#{i} · {orders_by_id[i]['quantity']:,} {unit} ordered {orders_by_id[i]['order_date']}, expected {orders_by_id[i]['expected_arrival']}",
            help="Linking marks the order as received, so it no longer counts as stock on order.",
        )
        st.caption("If the batch code already exists for this medicine, the quantity is added to that batch.")
        if st.form_submit_button("Record delivery", type="primary"):
            try:
                flash(inventory.receive_stock(medicine["id"], batch_code, quantity, expiry, received, note, order_id), "receive")
            except InventoryError as error:
                st.error(str(error))

if section == "Dispense":
    st.write(
        "Stock is taken **First Expiry, First Out** (FEFO): the batch that expires soonest is used first. "
        "Expired batches are never dispensed."
    )
    with st.form(form_key("dispense")):
        col1, col2, col3 = st.columns(3)
        quantity = col1.number_input(f"Quantity dispensed ({unit})", min_value=0, step=1, value=0)
        unmet = col2.number_input(
            f"Requested but not available ({unit})", min_value=0, step=1, value=0,
            help="Record demand you could not serve. Forecasts need this so that a stockout is not mistaken for low demand.",
        )
        dispensed_on = col3.date_input("Date", value=today, max_value=today)
        note = st.text_input("Note (optional)", placeholder="e.g. ward, programme. Do not enter patient details.")
        if st.form_submit_button("Record dispensing", type="primary"):
            try:
                taken = inventory.dispense_stock(medicine["id"], quantity, dispensed_on, unmet, note)
                parts = [f"{units} from {code}" for code, units in taken]
                messages = []
                if quantity:
                    messages.append(f"Dispensed {quantity} {unit} ({', '.join(parts)}).")
                if unmet:
                    messages.append(f"Recorded {unmet} {unit} as requested but not available.")
                flash(" ".join(messages), "dispense")
            except InventoryError as error:
                st.error(str(error))

if section == "Adjust":
    st.write(
        "Use an adjustment to correct a batch, for example after a stock count, or to remove damaged or "
        "expired stock. A reason is always required."
    )
    batches = batch_rows(medicine["id"], include_empty=True)
    if not batches:
        st.info("This medicine has no batches yet. Record a delivery first.")
    else:
        by_id = {row["id"]: row for row in batches}
        with st.form(form_key("adjust")):
            batch_id = st.selectbox(
                "Batch",
                list(by_id),
                format_func=lambda i: f"{by_id[i]['batch_code']} · {by_id[i]['quantity']} {unit} · expires {by_id[i]['expiry_date']}",
            )
            col1, col2, col3 = st.columns(3)
            direction = col1.radio("Direction", ["Remove stock (−)", "Add stock (+)"])
            amount = col2.number_input(f"Quantity ({unit})", min_value=0, step=1, value=0)
            adjusted_on = col3.date_input("Date", value=today, max_value=today)
            reason = st.selectbox("Reason", ADJUSTMENT_REASONS, index=None, placeholder="Choose a reason")
            note = st.text_input("Note", placeholder="Required when the reason is “Other”")
            if st.form_submit_button("Record adjustment", type="primary"):
                change = -amount if direction.startswith("Remove") else amount
                try:
                    flash(inventory.adjust_stock(batch_id, change, reason or "", adjusted_on, note), "adjust")
                except InventoryError as error:
                    st.error(str(error))

if section == "Incoming orders":
    st.write(
        "Record orders placed with a supplier. Open orders count as **stock on order** in the stockout risk check "
        "and reorder suggestions until they are received (on the Receive stock form) or cancelled."
    )
    lead = medicine["lead_time_days"]
    with st.form(form_key("order")):
        col1, col2, col3 = st.columns(3)
        quantity = col1.number_input(f"Quantity ordered ({unit})", min_value=0, step=1, value=0)
        ordered_on = col2.date_input("Order date", value=today, max_value=today)
        expected = col3.date_input(
            "Expected arrival", value=today + timedelta(days=lead or 14),
            help=f"Defaults to today + the medicine's lead time ({lead or 'not recorded; 14'} days).",
        )
        note = st.text_input("Note (optional)", placeholder="e.g. supplier, order reference")
        if st.form_submit_button("Record order", type="primary"):
            try:
                order_id = inventory.place_order(medicine["id"], quantity, ordered_on, expected, note)
                flash(f"Recorded order #{order_id}: {quantity:,} {unit} expected {expected:%d %b %Y}.", "order")
            except InventoryError as error:
                st.error(str(error))

    st.markdown("#### Open orders")
    if not open_orders:
        st.info("No open orders for this medicine.")
    else:
        orders = pd.DataFrame([dict(o) for o in open_orders])
        orders["status"] = orders["expected_arrival"].map(
            lambda d: "⚠️ Overdue" if date.fromisoformat(d) < today else "On the way"
        )
        st.dataframe(
            orders[["id", "quantity", "order_date", "expected_arrival", "status", "note"]].rename(
                columns={"id": "Order", "quantity": f"Quantity ({unit})", "order_date": "Ordered",
                         "expected_arrival": "Expected", "status": "Status", "note": "Note"}
            ).pipe(badge_styles, {"Status": {"Overdue": "red", "On the way": "blue"}}),
            hide_index=True,
            width="stretch",
        )
        st.caption("Overdue orders still count in reorder suggestions (they are confirmed), but not in the stockout check.")
        with st.form(form_key("cancel_order")):
            col1, col2 = st.columns([1, 2])
            cancel_id = col1.selectbox("Order to cancel", [o["id"] for o in open_orders], format_func=lambda i: f"#{i}")
            reason = col2.text_input("Reason", placeholder="Required, e.g. supplier cannot deliver")
            if st.form_submit_button("Cancel order"):
                try:
                    inventory.cancel_order(cancel_id, reason)
                    flash(f"Order #{cancel_id} cancelled.", "cancel_order")
                except InventoryError as error:
                    st.error(str(error))

if section == "Batches":
    batches = batch_rows(medicine["id"])
    if not batches:
        st.info("No stock in any batch.")
    else:
        table = pd.DataFrame([dict(row) for row in batches])
        table["days_to_expiry"] = (pd.to_datetime(table["expiry_date"]) - pd.Timestamp(today)).dt.days
        table["status"] = table["days_to_expiry"].apply(
            lambda days: "Expired — do not use" if days <= 0 else (f"Expires within {EXPIRY_WARNING_DAYS} days" if days <= EXPIRY_WARNING_DAYS else "OK")
        )
        table.insert(0, "use_order", range(1, len(table) + 1))
        st.dataframe(
            table[["use_order", "batch_code", "quantity", "expiry_date", "days_to_expiry", "status"]].rename(
                columns={
                    "use_order": "FEFO order", "batch_code": "Batch", "quantity": f"Quantity ({unit})",
                    "expiry_date": "Expiry date", "days_to_expiry": "Days to expiry", "status": "Status",
                }
            ).pipe(badge_styles, {"Status": {"Expired": "red", "Expires within": "amber", "OK": "green"}}),
            hide_index=True,
            width="stretch",
        )
        st.caption("FEFO order 1 is dispensed first. Expiry status is a stock-keeping prompt, not a regulatory determination.")

if section == "Transaction history":
    show_all = st.checkbox("Show all medicines")
    rows = transaction_rows(None if show_all else medicine["id"])
    if not rows:
        st.info("No transactions recorded yet.")
    else:
        history = pd.DataFrame([dict(row) for row in rows])
        history["transaction_type"] = history["transaction_type"].map(TYPE_LABELS)
        st.dataframe(
            history[["transaction_date", "medicine", "batch_code", "transaction_type", "quantity_change", "unit",
                     "reason", "note", "recorded_at"]].rename(
                columns={
                    "transaction_date": "Date", "medicine": "Medicine", "batch_code": "Batch",
                    "transaction_type": "Type", "quantity_change": "Change", "unit": "Unit",
                    "reason": "Reason", "note": "Note", "recorded_at": "Recorded at",
                }
            ).pipe(badge_styles, {"Type": TYPE_BADGES}),
            hide_index=True,
            width="stretch",
        )
        st.caption("Newest first · showing up to 500 rows. “Opening balance” is the synthetic starting stock.")

    unmet_rows = unmet_request_rows(None if show_all else medicine["id"])
    if unmet_rows:
        st.markdown("#### Requested but not available")
        st.dataframe(
            pd.DataFrame([dict(row) for row in unmet_rows]).rename(
                columns={
                    "request_date": "Date", "medicine": "Medicine", "quantity": "Quantity not supplied",
                    "unit": "Unit", "note": "Note", "recorded_at": "Recorded at",
                }
            ),
            hide_index=True,
            width="stretch",
        )

    mismatches = reconciliation_mismatches()
    if mismatches:
        st.error(
            f"Consistency check failed for {len(mismatches)} batch(es): their quantity does not equal the sum of their "
            "transactions. " + ", ".join(f"{row['batch_code']} ({row['quantity']} vs {row['transaction_total']})" for row in mismatches)
        )
    else:
        st.success("Consistency check passed: every batch quantity equals the sum of its transactions.")
