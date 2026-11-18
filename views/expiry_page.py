"""Batch expiry page: which batch to use first, and which batches may expire before they are used."""
import pandas as pd
import streamlit as st

import expiry
from expiry import STATUS_ORDER
from ui import badge_styles, cached_forecast, page_header, stat_card

STATUS_TONES = {"Expired": "red", "At risk": "rose", "Watch": "amber", "OK": "green", "No forecast": "slate"}
STATUS_BADGES = {"Expired": "red", "At risk": "red", "Watch": "amber", "OK": "green", "No forecast": "slate"}
STATUS_ICONS = {"Expired": "⛔", "At risk": "🔴", "Watch": "🟡", "OK": "🟢", "No forecast": "⚪"}

page_header("🗓️", "Batch expiry", "Which batch to use first, and which batches may expire before they are used.", "rose")
st.warning(
    "🧪 Projections use a **synthetic** demand forecast. Expiry flags are stock-keeping prompts for a responsible "
    "staff member, **not** a clinical or regulatory determination. Always check the label and local rules."
)

result, history = cached_forecast()
if result is None:
    st.info("No demand history, so batch use cannot be projected.")
    st.stop()

table = expiry.assess_expiry(result.forecast, history)
if table.empty:
    st.info("No batches in stock.")
    st.stop()

counts = table["status"].value_counts()
for column, status in zip(st.columns(len(STATUS_ORDER)), STATUS_ORDER):
    with column:
        stat_card(f"{STATUS_ICONS[status]} {status}", int(counts.get(status, 0)), STATUS_TONES[status] if counts.get(status, 0) else "slate", key=f"status-{status}")
st.caption(
    "⛔ Expired = still in stock after its expiry date · 🔴 At risk = units expected to expire unused at forecast demand · "
    "🟡 Watch = only in a quiet period (bottom of the forecast range) · 🟢 OK = expected to be used up in time."
)

# ---------- all batches ----------
st.subheader("All batches")
show = st.radio("Show", ["Needs attention", "All batches"], horizontal=True)
visible = table if show == "All batches" else table[table["status"].isin(["Expired", "At risk", "Watch"])]
if visible.empty:
    st.success(
        "No batch is expired or projected to expire before it is used, even in a quiet period. "
        "(In the synthetic data, each order covers only about 45 days of demand, so expiry problems are rare.)"
    )
else:
    display = visible.assign(status_label=visible["status"].map(lambda s: f"{STATUS_ICONS[s]} {s}"))
    st.dataframe(
        badge_styles(display[["medicine", "batch_code", "fefo_order", "quantity", "unit", "expiry_date", "days_to_expiry",
                              "days_needed", "expected_unused", "status_label", "explanation"]],
                     {"status_label": STATUS_BADGES}),
        column_config={
            "medicine": "Medicine", "batch_code": "Batch",
            "fefo_order": st.column_config.NumberColumn("Use order", help="1 = use first (First Expiry, First Out)."),
            "quantity": st.column_config.NumberColumn("Quantity", format="%d"), "unit": "Unit",
            "expiry_date": st.column_config.DateColumn("Expiry date"),
            "days_to_expiry": st.column_config.NumberColumn("Days to expiry", format="%d"),
            "days_needed": st.column_config.NumberColumn(
                "Days needed to use", format="%.0f",
                help="Units in this batch and the batches used before it ÷ forecast daily demand.",
            ),
            "expected_unused": st.column_config.NumberColumn("May expire unused", format="%.0f"),
            "status_label": "Status",
            "explanation": st.column_config.TextColumn("Why", width="large"),
        },
        hide_index=True,
        width="stretch",
    )

# ---------- one medicine ----------
st.subheader("Use order for one medicine (FEFO)")
selected = st.selectbox("Medicine", sorted(table["medicine"].unique()))
batches = table[table["medicine"] == selected].sort_values("fefo_order")
unit = batches["unit"].iloc[0]
first = batches[batches["status"] != "Expired"].head(1)
if not first.empty:
    st.markdown(f"**Use next:** batch **{first['batch_code'].iloc[0]}** (expires {first['expiry_date'].iloc[0]:%d %b %Y}).")
for _, batch in batches.iterrows():
    st.markdown(
        f"{batch['fefo_order']}. {STATUS_ICONS[batch['status']]} **{batch['batch_code']}** · "
        f"{batch['quantity']:,} {unit} · expires {batch['expiry_date']:%d %b %Y} ({batch['days_to_expiry']} days) — "
        f"{batch['explanation']}"
    )

with st.expander("How the check works, and its assumptions"):
    st.markdown(
        """
- **FEFO (First Expiry, First Out):** the batch that expires soonest is used first. Dispensing on the Inventory
  page already follows this order.
- **The comparison:** for each batch, the **days needed to use it** (this batch plus every batch ahead of it in
  FEFO order, divided by forecast daily demand) is compared with its **days to expiry**. The flag itself comes
  from a day-by-day projection that also accounts for weekday patterns and batches ahead of it expiring.
- **At risk** means units are expected to remain on the expiry date at forecast demand. **Watch** means that only
  happens in a quiet period (demand at the bottom of the forecast's likely range).
- **Assumptions:** demand continues at the 7-day forecast's weekly level for the whole period, which becomes very
  uncertain months ahead. New deliveries are assumed to expire later than current batches. Stock is not moved
  to another facility.
- **Possible responses** (for staff to decide): use the at-risk batch first, check whether demand is expected to
  change, avoid ordering more for now, or ask about redistribution. Redistribution would need facility approval,
  storage and transport checks, and applicable rules, so it is never suggested automatically.
- Expired stock must not be dispensed. Remove it with an adjustment (reason: “Expired stock removed”).
"""
    )
