"""Dashboard page: inventory overview, alerts, recent movements, and usage history.

Every number on this page comes straight from database records, and each
section says which table it uses. Nothing here is a prediction.
"""
import pandas as pd
import streamlit as st

from database import (
    dataset_info,
    expiring_batch_rows,
    inventory_rows,
    recorded_dispensing_rows,
    transaction_rows,
    unmet_request_rows,
)
from inventory import EXPIRY_WARNING_DAYS
from synthetic_data import DATASET_VERSION
from ui import (
    badge_styles, cached_expiry, cached_forecast, cached_risk, cached_usage_history, page_header, panel, stat_card,
)

STOCK_BADGES = {"Out of stock": "red", "At or below": "amber", "Above": "green"}
TYPE_BADGES = {"Received": "green", "Dispensed": "blue", "Adjustment": "amber", "Opening": "slate"}
TYPE_LABELS = {"opening_balance": "Opening balance", "receipt": "Received", "dispense": "Dispensed", "adjustment": "Adjustment"}

info = dataset_info()

page_header("📊", "Dashboard", "Stock levels, alerts, and recent activity at a glance.", "teal")
st.info("Prototype only: this dashboard does not provide clinical advice or validated supply decisions.", icon="🧪")

if info.get("generator_version") != DATASET_VERSION:
    st.warning(
        "This database was created by an older version of the sample-data generator, so some sample "
        "records (such as the daily history or orders on the way) are missing. Stop the app, run "
        "`python seed_data.py --reset`, and start it again. This deletes the local demo database and rebuilds it."
    )

rows = inventory_rows()
inventory = pd.DataFrame([dict(row) for row in rows])

if inventory.empty:
    st.warning("No medicine records found yet.")
    st.stop()


def stock_status(item) -> str:
    if item["stock"] == 0:
        return "Out of stock"
    if item["stock"] <= item["reorder_level"]:
        return "At or below reorder level"
    return "Above reorder level"


inventory["status"] = inventory.apply(stock_status, axis=1)
out_of_stock = inventory[inventory["status"] == "Out of stock"]
low_stock = inventory[inventory["stock"] <= inventory["reorder_level"]]  # includes out of stock

# ---------- headline numbers ----------
window = st.select_slider(
    "Expiry warning window (days)", options=[30, 60, 90, 180, 365], value=EXPIRY_WARNING_DAYS,
    help="Batches that expire within this many days are listed under “Needs attention”.",
)
expiring = pd.DataFrame([dict(row) for row in expiring_batch_rows(window)])
expired = expiring[expiring["days_left"] <= 0] if not expiring.empty else expiring
expiring_soon = expiring[expiring["days_left"] > 0] if not expiring.empty else expiring

# Card colours carry meaning: green = nothing to do, amber/orange = check soon, red = act now.
col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    stat_card("Medicines tracked", len(inventory), "teal", help="Active medicines in the `medicines` table.")
with col2:
    stat_card(
        "Units in stock", f"{int(inventory['stock'].sum()):,}", "blue",
        help="Sum of all batch quantities. This adds different units together (tablets, vials, bottles…), "
        "so treat it as a rough size indicator only.",
    )
with col3:
    stat_card("At or below reorder level", len(low_stock), "amber" if len(low_stock) else "green",
              help="Total stock ≤ the medicine's reorder level. Includes out-of-stock medicines.")
with col4:
    stat_card("Out of stock", len(out_of_stock), "red" if len(out_of_stock) else "green",
              help="Medicines whose batches add up to zero.")
with col5:
    stat_card(
        f"Batches expiring ≤ {window} days", len(expiring_soon), "orange" if len(expiring_soon) else "green",
        key="expiring",
        help="In-stock batches whose expiry date is within the window. Already-expired batches are counted separately below.",
    )

# ---------- alerts ----------
st.subheader("Needs attention")

with panel("amber", "stock"):
    st.markdown("**📉 Stock level** · source: `batches` totals compared with `medicines.reorder_level`")
    if low_stock.empty:
        st.success("No medicine is at or below its reorder level.")
    else:
        alert = low_stock.assign(shortfall=low_stock["reorder_level"] - low_stock["stock"]).sort_values(
            ["stock", "shortfall"], ascending=[True, False]
        )
        st.dataframe(
            badge_styles(
                alert[["name", "stock", "reorder_level", "shortfall", "unit", "status"]].rename(
                    columns={
                        "name": "Medicine", "stock": "In stock", "reorder_level": "Reorder level",
                        "shortfall": "Below level by", "unit": "Unit", "status": "Status",
                    }
                ),
                {"Status": STOCK_BADGES},
            ),
            hide_index=True,
            width="stretch",
        )
        st.caption(
            "This is a fixed threshold check, not a prediction, and it ignores stock on order. See "
            "Reorder suggestions for what is already on the way."
        )

with panel("orange", "expiry"):
    st.markdown(f"**🗓️ Expiry** · source: `batches.expiry_date` · window: {window} days")
    if not expired.empty:
        st.error(f"{len(expired)} expired batch(es) still hold stock. Remove them with an adjustment (reason: “Expired stock removed”).")
    if expiring.empty:
        st.success(f"No in-stock batch expires within {window} days.")
    else:
        table = expiring.copy()
        table["status"] = table["days_left"].apply(lambda days: "Expired" if days <= 0 else "Expiring soon")
        st.dataframe(
            badge_styles(
                table[["medicine", "batch_code", "quantity", "unit", "expiry_date", "days_left", "status"]].rename(
                    columns={
                        "medicine": "Medicine", "batch_code": "Batch", "quantity": "Quantity", "unit": "Unit",
                        "expiry_date": "Expiry date", "days_left": "Days left", "status": "Status",
                    }
                ),
                {"Status": {"Expired": "red", "Expiring soon": "amber"}},
            ),
            hide_index=True,
            width="stretch",
        )
    expiry_forecast, expiry_history = cached_forecast()
    if expiry_forecast is not None:
        batch_check = cached_expiry()
        at_risk = int((batch_check["status"] == "At risk").sum()) if not batch_check.empty else 0
        watch = int((batch_check["status"] == "Watch").sum()) if not batch_check.empty else 0
        if at_risk or watch:
            st.warning(
                f"Using the synthetic forecast, {at_risk} batch(es) may expire before they are used, "
                f"and {watch} more could in a quiet period."
            )
        else:
            st.caption("Using the synthetic forecast, every in-stock batch is expected to be used before it expires.")
    st.caption("Expiry alerts are stock-keeping prompts, not a clinical or regulatory determination.")
    st.page_link("views/expiry_page.py", label="See every batch, its use order, and why", icon="🗓️")

with panel("red", "shortages"):
    st.markdown("**⏳ Projected shortages** · source: synthetic demand forecast + current usable stock (30-day horizon)")
    forecast_result, demand_history = cached_forecast()
    if forecast_result is None:
        st.info("No demand history, so shortages cannot be projected.")
    else:
        risk_table, _ = cached_risk(30)
        urgent = risk_table[risk_table["risk"].isin(["Critical", "High"])]
        if urgent.empty:
            st.success("No medicine is rated Critical or High.")
        else:
            st.dataframe(
                badge_styles(urgent[["medicine", "risk", "expected_days", "lead_time", "reason"]],
                             {"risk": {"Critical": "red", "High": "orange"}}),
                column_config={
                    "medicine": "Medicine", "risk": "Risk",
                    "expected_days": st.column_config.NumberColumn("Days until stockout (expected)", format="%d"),
                    "lead_time": "Lead time (days)", "reason": st.column_config.TextColumn("Why", width="large"),
                },
                hide_index=True,
                width="stretch",
            )
        st.caption(
            "Estimates, not guarantees. Open orders are counted on their expected arrival date; overdue orders are not."
        )
        st.page_link("views/stockout_page.py", label="See all medicines and how risk is decided", icon="⏳")

# ---------- current inventory ----------
st.subheader("Current inventory")
status_filter = st.selectbox(
    "Show", ["All medicines", "At or below reorder level", "Out of stock", "Above reorder level"]
)
if status_filter == "All medicines":
    visible = inventory
elif status_filter == "At or below reorder level":
    visible = low_stock
else:
    visible = inventory[inventory["status"] == status_filter]

display = visible[["name", "category", "stock", "unit", "reorder_level", "nearest_expiry", "status"]].rename(
    columns={
        "name": "Medicine", "category": "Category", "stock": "Quantity",
        "unit": "Unit", "reorder_level": "Reorder level",
        "nearest_expiry": "Nearest batch expiry", "status": "Status",
    }
)
if display.empty:
    st.info("No medicines match this filter.")
else:
    st.dataframe(badge_styles(display, {"Status": STOCK_BADGES}), hide_index=True, width="stretch")
st.caption(
    "Reorder level = simple threshold (for the synthetic medicines: average weekday demand × (lead time + 10 days)). "
    "It is not a forecast. Deactivated medicines are not counted."
)

# ---------- recent movements ----------
st.subheader("Recent stock movements")
recent = pd.DataFrame([dict(row) for row in transaction_rows(limit=10)])
if recent.empty:
    st.info("No stock movements recorded yet.")
else:
    recent["transaction_type"] = recent["transaction_type"].map(TYPE_LABELS)
    st.dataframe(
        badge_styles(
            recent[["transaction_date", "medicine", "batch_code", "transaction_type", "quantity_change", "unit", "reason"]].rename(
                columns={
                    "transaction_date": "Date", "medicine": "Medicine", "batch_code": "Batch",
                    "transaction_type": "Type", "quantity_change": "Change", "unit": "Unit", "reason": "Reason",
                }
            ),
            {"Type": TYPE_BADGES},
        ),
        hide_index=True,
        width="stretch",
    )
st.caption("Latest 10 rows from `stock_transactions`. “Opening balance” is the synthetic starting stock.")
st.page_link("views/inventory_page.py", label="Record a movement or see the full history", icon="📦")

# ---------- usage history ----------
st.subheader("Usage history")
selected = st.selectbox("Medicine", inventory["name"].tolist())
medicine_id = int(inventory.loc[inventory["name"] == selected, "id"].iloc[0])
unit = inventory.loc[inventory["name"] == selected, "unit"].iloc[0]
view = st.radio("View", ["Daily", "Monthly"], horizontal=True)
colors = ["#0D9488", "#E11D48"]  # dispensed = teal, unmet = rose


def show_chart(chart: pd.DataFrame) -> None:
    if view == "Daily":
        st.line_chart(chart, y_label=f"{unit} per day", color=colors)
    else:
        monthly = chart.resample("MS").sum()
        monthly.index = monthly.index.strftime("%Y-%m")
        st.bar_chart(monthly, y_label=f"{unit} per month", color=colors)


st.markdown("#### 🧪 Synthetic history")
history = cached_usage_history()
medicine = history[history["medicine"] == selected] if not history.empty else history
if medicine.empty:
    st.info("No synthetic history for this medicine (for example, a medicine registered in the app).")
else:
    st.caption(
        f"SYNTHETIC DATA · simulated daily records from {info.get('start_date')} to {info.get('end_date')} "
        f"· generator version {info.get('generator_version')} · seed {info.get('seed')} · source: `daily_consumption`. "
        "Not based on any clinic, pharmacy, or patient."
    )
    m1, m2, m3, m4 = st.columns(4)
    stockout_days = int(medicine["stockout"].sum())
    with m1:
        stat_card("Days of history", len(medicine), "slate", key="history-days")
    with m2:
        stat_card(f"Dispensed ({unit})", f"{int(medicine['quantity_dispensed'].sum()):,}", "teal", key="history-dispensed")
    with m3:
        stat_card("Stockout days", stockout_days, "rose" if stockout_days else "green", key="history-stockout")
    with m4:
        stat_card(f"Unmet demand ({unit})", f"{int(medicine['unmet_quantity'].sum()):,}",
                  "rose" if stockout_days else "green", key="history-unmet")
    show_chart(
        medicine.set_index("usage_date")[["quantity_dispensed", "unmet_quantity"]].rename(
            columns={"quantity_dispensed": "Dispensed", "unmet_quantity": "Unmet (out of stock)"}
        )
    )
    if view == "Monthly":
        st.caption("The first and last months are partial because the history covers exactly 365 days.")
    st.caption(
        "Unmet demand is shown separately because, during a stockout, dispensing drops even though "
        "patients still need the medicine. Later forecasts must not mistake that drop for lower demand."
    )

st.markdown("#### 📝 Recorded in the app")
dispensed = pd.DataFrame([dict(row) for row in recorded_dispensing_rows(medicine_id)])
unmet = pd.DataFrame([dict(row) for row in unmet_request_rows(medicine_id)])
if dispensed.empty and unmet.empty:
    st.info("No dispensing has been recorded in the app for this medicine yet. Use the Inventory page → Dispense.")
else:
    # One column per source, one row per day (days with nothing recorded count as 0).
    parts = []
    if not dispensed.empty:
        parts.append(pd.Series(dispensed["dispensed"].to_numpy(), index=pd.to_datetime(dispensed["day"]), name="Dispensed"))
    if not unmet.empty:
        parts.append(unmet.groupby(pd.to_datetime(unmet["request_date"]))["quantity"].sum().rename("Unmet (requested, not available)"))
    recorded = (
        pd.concat(parts, axis=1)
        .reindex(columns=["Dispensed", "Unmet (requested, not available)"])
        .fillna(0)
        .astype("int64")
        .asfreq("D", fill_value=0)
    )
    st.caption("Source: `dispense` rows in `stock_transactions` and `unmet_requests`. Kept separate from the synthetic history.")
    show_chart(recorded)

with st.expander("How the synthetic data is generated"):
    st.markdown(
        """
Every number in the synthetic history is **invented** by `synthetic_data.py`. For each of the 25 medicines,
one year is simulated day by day:

- **Normal variation** — each day's demand is a random whole number around an expected value.
- **Weekday pattern** — Mondays are busiest, Saturdays are a half day, and Sundays are very quiet.
- **Illustrative malaria season** — antimalarials, malaria tests, and a few related items rise
  towards a mid-August peak and fall towards February. This curve is a simplification, not a
  model of malaria in any region of Cameroon.
- **Demand spikes** — about 1% of days start a 2–4 day spike at 1.8–3× normal demand.
- **Ordering and lead times** — when stock plus stock on order falls to the reorder level, an
  order is placed. It normally arrives after the supplier lead time (14–28 days, depending on the medicine).
- **Late deliveries and stockouts** — some deliveries are 5–21 days late. If stock runs out, the
  shortfall is recorded as unmet demand and the day is marked as a stockout.
- **Batches and expiry** — each delivery becomes a batch with an expiry date. About 10% arrive
  short-dated. Stock is used first-expiry-first-out, and expired stock is removed.

The same seed and end date always produce the same dataset. Synthetic data shows that the software
pipeline works. It cannot show how SmartMed AI would perform in a real facility.
"""
    )
