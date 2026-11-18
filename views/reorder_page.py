"""Reorder suggestions page: when to reorder, how much, and which medicines first. Prototype suggestions only."""
import pandas as pd
import streamlit as st

import reorder
import stockout
from reorder import PRIORITY_ORDER, SOON_DAYS
from ui import badge_styles, cached_forecast, page_header, stat_card

PRIORITY_TONES = {"Urgent": "red", "Reorder now": "orange", "Soon": "amber", "Not yet": "green", "Unknown": "slate"}
PRIORITY_ICONS = {"Urgent": "🔴", "Reorder now": "🟠", "Soon": "🟡", "Not yet": "🟢", "Unknown": "⚪"}

page_header("🛒", "Reorder suggestions", "When to reorder, how much, and which medicines first, with the arithmetic shown.", "amber")
st.warning(
    "🧪 **Prototype suggestions** built from a **synthetic** demand forecast. A responsible staff member must review "
    "every suggestion before ordering. They are not purchase orders, and they do not account for budget, pack "
    "sizes, minimum order quantities, storage space, or supplier availability."
)

result, history = cached_forecast()
if result is None:
    st.info("No demand history, so no suggestions can be made.")
    st.stop()

col1, col2 = st.columns(2)
safety_days = col1.slider(
    "Safety stock (days of demand)", 0, 30, reorder.DEFAULT_SAFETY_DAYS,
    help="Extra stock held for demand spikes and late deliveries, as days of average demand.",
)
cover_days = col2.slider(
    "Each order should cover (days)", 7, 90, reorder.DEFAULT_COVER_DAYS,
    help="How many days of average demand an order adds on top of the reorder point.",
)

risk, _ = stockout.assess_risk(result.forecast, history, horizon=30)
suggestions = reorder.suggest_reorders(risk, safety_days=safety_days, cover_days=cover_days)

counts = suggestions["priority"].value_counts()
for column, level in zip(st.columns(len(PRIORITY_ORDER)), PRIORITY_ORDER):
    with column:
        stat_card(f"{PRIORITY_ICONS[level]} {level}", int(counts.get(level, 0)), PRIORITY_TONES[level] if counts.get(level, 0) else "slate", key=f"priority-{level}")

table = suggestions.assign(
    priority_label=suggestions["priority"].map(lambda p: f"{PRIORITY_ICONS[p]} {p}"),
    reorder_by=suggestions.apply(
        lambda r: "—" if pd.isna(r.get("reorder_date")) else ("Now" if r["days_until_reorder"] == 0 else f"{r['reorder_date']:%d %b %Y}"),
        axis=1,
    ),
)
st.dataframe(
    badge_styles(
        table[["medicine", "priority_label", "risk", "reorder_by", "quantity", "unit", "position", "reorder_point", "daily_demand", "lead_time", "action"]],
        {"priority_label": PRIORITY_TONES,
         "risk": {"Critical": "red", "High": "orange", "Medium": "amber", "Low": "green", "Unknown": "slate"}},
    ),
    column_config={
        "medicine": "Medicine",
        "priority_label": "Priority",
        "risk": st.column_config.TextColumn("Stockout risk", help="From the Stockout risk page (30-day horizon)."),
        "reorder_by": "Reorder by",
        "quantity": st.column_config.NumberColumn("Suggested quantity", format="%d"),
        "unit": "Unit",
        "position": st.column_config.NumberColumn("Inventory position", format="%d", help="Usable stock + open orders − commitments (not recorded, so 0)."),
        "reorder_point": st.column_config.NumberColumn("Reorder point", format="%d", help="Average daily demand × lead time + safety stock."),
        "daily_demand": st.column_config.NumberColumn("Avg demand / day", format="%.1f"),
        "lead_time": "Lead time (days)",
        "action": st.column_config.TextColumn("Suggested action", width="large"),
    },
    hide_index=True,
    width="stretch",
)
st.caption(
    f"Priority: 🔴 Urgent = stock may run out before a new order could arrive (Critical on the Stockout risk page) · "
    f"🟠 Reorder now = inventory position at or below the reorder point · 🟡 Soon = reorder point expected within "
    f"{SOON_DAYS} days · 🟢 Not yet = later. Record placed orders on the Inventory page → Incoming orders."
)

# ---------- one medicine ----------
st.subheader("How a suggestion is calculated")
options = suggestions.loc[suggestions["priority"] != "Unknown", "medicine"].tolist()
selected = st.selectbox("Medicine", options)
row = suggestions[suggestions["medicine"] == selected].iloc[0]
u = row["unit"]
st.markdown(
    f"""
| Step | Calculation | Result |
|---|---|---|
| Average daily demand | 7-day forecast ÷ 7 | **{row['daily_demand']:,.1f}** {u}/day |
| Safety stock | {row['daily_demand']:,.1f} × {row['safety_days']} safety days | **{row['safety_stock']:,.0f}** {u} |
| Reorder point | {row['daily_demand']:,.1f} × {row['lead_time']}-day lead time + {row['safety_stock']:,.0f} | **{row['reorder_point']:,.0f}** {u} |
| Inventory position | {row['usable_stock']:,.0f} usable on hand + {row['on_order']:,.0f} on order − 0 commitments | **{row['position']:,.0f}** {u} |
| Order target | {row['reorder_point']:,.0f} + {row['daily_demand']:,.1f} × {row['cover_days']} cover days | **{row['target']:,.0f}** {u} |
| Suggested quantity | target − inventory position {"(now)" if row['days_until_reorder'] == 0 else "(when the reorder point is reached)"} | **{row['quantity']:,}** {u} |
"""
)
st.markdown(f"**{PRIORITY_ICONS[row['priority']]} {row['priority']}**: {row['action']}")
if row["overdue_on_order"]:
    st.warning(
        f"{row['overdue_on_order']:,.0f} {u} are on an overdue order. They are counted in the inventory position "
        "(the order is still confirmed), so cancel that order on the Inventory page if it will not arrive."
    )

with st.expander("Assumptions and limitations"):
    st.markdown(
        """
- **Demand** is the 7-day forecast from the Forecasting page, assumed to stay at the same daily average over
  the lead time and cover period. It is based on synthetic data.
- **Reorder point vs. reorder level:** the *reorder point* here comes from the forecast and lead time. The
  *reorder level* on the Medicines page is a fixed number used by the dashboard's threshold check. They can
  differ; the reorder level is not changed automatically.
- **Inventory position** counts open orders, including overdue ones, because they are still confirmed. The
  stockout check is more cautious and does not count overdue orders.
- **Outstanding commitments** (stock reserved or promised elsewhere) are not recorded yet, so they count as 0.
- **Expiry** is handled only through *usable* stock (expired batches are excluded). Units the Batch expiry page
  projects to expire unused are **not** subtracted from the inventory position; check that page before ordering.
- **Not considered:** budget, pack sizes, minimum order quantities, storage capacity, supplier reliability, or
  ordering schedules. Quantities are rounded up to whole units.
- Suggestions are never placed automatically. A responsible staff member decides and records any order.
"""
    )
