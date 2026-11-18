"""Medicines page: register, edit, deactivate, and (only if unused) delete medicines."""
import pandas as pd
import streamlit as st

import inventory
from database import inventory_rows, medicine_record_counts
from inventory import InventoryError
from ui import badge_styles, flash, form_key, page_header, show_flash

page_header("💊", "Medicines", "Register medicines and manage catalogue details. Stock is changed on the Inventory page.", "violet")
show_flash()

medicines = inventory_rows(include_inactive=True)
# A radio button (rather than st.tabs) remembers the chosen section after a form is submitted.
section = st.radio(
    "Section",
    ["Register medicine", "Edit or deactivate", "Catalogue"],
    horizontal=True,
    label_visibility="collapsed",
    key="medicines_section",
)
st.divider()

if section == "Register medicine":
    with st.form(form_key("register_medicine")):
        name = st.text_input("Medicine name and strength", placeholder="e.g. Paracetamol 500 mg")
        col1, col2, col3 = st.columns(3)
        category = col1.text_input("Category", placeholder="e.g. Analgesic")
        unit = col2.text_input("Unit", placeholder="e.g. tablets")
        reorder_level = col3.number_input("Reorder level (units)", min_value=0, step=1, value=0)
        lead_time = st.number_input(
            "Supplier lead time (days)", min_value=1, max_value=365, step=1, value=None, placeholder="Leave empty if unknown",
            help="Usual number of days between placing an order and receiving it. Used by the stockout risk check.",
        )
        st.caption("Reorder level: when total stock is at or below this number, the dashboard flags it.")
        if st.form_submit_button("Register medicine", type="primary"):
            try:
                inventory.add_medicine(name, category, unit, reorder_level, lead_time)
                flash(f"Registered {name.strip()}. Record its first delivery on the Inventory page.", "register_medicine")
            except InventoryError as error:
                st.error(str(error))

if section == "Edit or deactivate":
    if not medicines:
        st.info("No medicines registered yet.")
    else:
        by_id = {row["id"]: row for row in medicines}
        chosen_id = st.selectbox(
            "Medicine",
            list(by_id),
            format_func=lambda medicine_id: by_id[medicine_id]["name"]
            + ("" if by_id[medicine_id]["active"] else " (deactivated)"),
        )
        medicine = by_id[chosen_id]

        with st.form(f"edit_medicine_{chosen_id}"):
            name = st.text_input("Medicine name and strength", value=medicine["name"])
            col1, col2, col3 = st.columns(3)
            category = col1.text_input("Category", value=medicine["category"])
            unit = col2.text_input("Unit", value=medicine["unit"], help="Locked once stock has been recorded.")
            reorder_level = col3.number_input(
                "Reorder level (units)", min_value=0, step=1, value=int(medicine["reorder_level"])
            )
            lead_time = st.number_input(
                "Supplier lead time (days)", min_value=1, max_value=365, step=1,
                value=None if medicine["lead_time_days"] is None else int(medicine["lead_time_days"]),
                placeholder="Leave empty if unknown",
                help="Usual number of days between placing an order and receiving it. Used by the stockout risk check.",
            )
            if st.form_submit_button("Save changes", type="primary"):
                try:
                    inventory.update_medicine(chosen_id, name, category, unit, reorder_level, lead_time)
                    flash(f"Saved changes to {name.strip()}.")
                except InventoryError as error:
                    st.error(str(error))

        st.markdown("#### Status")
        if medicine["active"]:
            st.write(
                f"**Active** · {medicine['stock']} {medicine['unit']} in stock. Deactivating hides it from the "
                "dashboard and stock forms but keeps all of its history. Stock must be zero first."
            )
            if st.button("Deactivate medicine"):
                try:
                    inventory.set_medicine_active(chosen_id, False)
                    flash(f"{medicine['name']} deactivated. Its history is kept.")
                except InventoryError as error:
                    st.error(str(error))
        else:
            st.write("**Deactivated** · hidden from the dashboard and stock forms. History is kept.")
            if st.button("Reactivate medicine"):
                inventory.set_medicine_active(chosen_id, True)
                flash(f"{medicine['name']} reactivated.")

        counts = medicine_record_counts(chosen_id)
        if sum(counts.values()) == 0:
            st.markdown("#### Delete")
            st.write("This medicine has no stock or history records, so it can be deleted permanently (for example, a typo).")
            confirm = st.checkbox(f"I understand that deleting {medicine['name']} cannot be undone.")
            if st.button("Delete medicine", disabled=not confirm):
                try:
                    inventory.delete_medicine(chosen_id)
                    flash(f"Deleted {medicine['name']}.")
                except InventoryError as error:
                    st.error(str(error))
        else:
            st.caption(
                "Delete is not available: this medicine has stock or history records "
                f"({counts['stock_transactions']} transactions, {counts['daily_consumption']} daily history rows). "
                "Deactivate it instead so the history is kept."
            )

if section == "Catalogue":
    if medicines:
        table = pd.DataFrame([dict(row) for row in medicines])
        table["active"] = table["active"].map({1: "Active", 0: "Deactivated"})
        st.dataframe(
            table[["name", "category", "unit", "reorder_level", "lead_time_days", "stock", "active"]].rename(
                columns={
                    "name": "Medicine", "category": "Category", "unit": "Unit",
                    "reorder_level": "Reorder level", "lead_time_days": "Lead time (days)",
                    "stock": "In stock", "active": "Status",
                }
            ).pipe(badge_styles, {"Status": {"Active": "green", "Deactivated": "slate"}}),
            hide_index=True,
            width="stretch",
        )
