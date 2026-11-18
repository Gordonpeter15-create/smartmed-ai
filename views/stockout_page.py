"""Stockout risk page: how long current stock is expected to last, and why each risk level was given."""
from datetime import date

import pandas as pd
import streamlit as st

import stockout
from stockout import DEFAULT_LEAD_TIME_DAYS, RISK_ORDER, SIMULATION_DAYS
from ui import badge_styles, cached_forecast, page_header, stat_card

RISK_TONES = {"Critical": "red", "High": "orange", "Medium": "amber", "Low": "green", "Unknown": "slate"}
RISK_ICONS = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🟢", "Unknown": "⚪"}

page_header("⏳", "Stockout risk", "How long current stock is expected to last, and why each risk level was given.", "red")
st.warning(
    "🧪 Estimates from a **synthetic** demand forecast and current stock. They are prototype prompts for a "
    "responsible staff member to review, **not** guarantees or instructions."
)
st.info(
    "Open incoming orders (Inventory page → Incoming orders) are counted on their expected arrival date. "
    "**Overdue orders are not assumed to arrive**; they are flagged so they can be checked with the supplier."
)

result, history = cached_forecast()
if result is None:
    st.info("No demand history is available, so stockout risk cannot be estimated.")
    st.stop()

horizon = st.select_slider(
    "Planning horizon (days)", options=[14, 30, 60, 90], value=30,
    help="Medicines expected to run out within this many days (but after their lead time) are rated Medium.",
)
table, projections = stockout.assess_risk(result.forecast, history, horizon=horizon)
today = date.today()
history_end = result.data_summary["last_day"].date()
if (today - history_end).days > 1:
    st.warning(
        f"The demand history ends on {history_end:%d %b %Y}, {(today - history_end).days} days ago. "
        "The forecast may be out of date."
    )

counts = table["risk"].value_counts()
columns = st.columns(len(RISK_ORDER))
for column, level in zip(columns, RISK_ORDER):
    with column:
        stat_card(f"{RISK_ICONS[level]} {level}", int(counts.get(level, 0)), RISK_TONES[level] if counts.get(level, 0) else "slate", key=f"risk-{level}")

st.subheader("All medicines")
display = table.assign(
    risk_label=table["risk"].map(lambda level: f"{RISK_ICONS[level]} {level}"),
    lead=table.apply(lambda r: f"{r['lead_time']}" + (" (assumed)" if r["lead_time_assumed"] else ""), axis=1),
    expected=table["expected_days"].map(lambda d: f"> {SIMULATION_DAYS}" if pd.isna(d) else f"{int(d)}"),
    busy=table["busy_days"].map(lambda d: f"> {SIMULATION_DAYS}" if pd.isna(d) else f"{int(d)}"),
)
if "Unknown" in set(table["risk"]):
    display.loc[display["risk"] == "Unknown", ["expected", "busy"]] = "—"
st.dataframe(
    badge_styles(display[["medicine", "risk_label", "usable_stock", "forecast_week", "expected", "busy", "lead", "reason"]],
                 {"risk_label": RISK_TONES}),
    column_config={
        "medicine": "Medicine",
        "risk_label": "Risk",
        "usable_stock": st.column_config.NumberColumn("Usable stock", format="%d", help="Units in batches that have not expired."),
        "forecast_week": st.column_config.NumberColumn("Forecast demand / week", format="%.0f"),
        "expected": st.column_config.TextColumn("Days until stockout (expected)", help="From the day-by-day simulation with the forecast demand."),
        "busy": st.column_config.TextColumn("Days until stockout (busy period)", help="Same simulation with demand at the top of the forecast's likely range."),
        "lead": st.column_config.TextColumn("Lead time (days)"),
        "reason": st.column_config.TextColumn("Why", width="large"),
    },
    hide_index=True,
    width="stretch",
)
st.caption(
    "“> 90” means stock lasts longer than the 90-day simulation. Lead times can be edited on the Medicines page."
)

# ---------- one medicine ----------
st.subheader("Projected stock for one medicine")
options = table.loc[table["medicine_id"].isin(list(projections)), "medicine"].tolist()  # most at risk first
selected = st.selectbox("Medicine", options)
row = table[table["medicine"] == selected].iloc[0]
projection = projections[row["medicine_id"]]

st.markdown(f"**{RISK_ICONS[row['risk']]} {row['risk']}**: {row['reason']}")
c1, c2, c3, c4 = st.columns(4)
with c1:
    stat_card(f"Usable stock ({row['unit']})", f"{row['usable_stock']:,.0f}", "blue", key="one-usable")
with c2:
    stat_card("Forecast per day", f"{row['forecast_week'] / 7:,.1f}", "indigo", key="one-daily")
with c3:
    stat_card(
        "Simple estimate (days)", "—" if pd.isna(row["simple_days"]) else f"{row['simple_days']:.1f}", "slate",
        key="one-simple",
        help="Usable stock ÷ forecast daily demand. A rough check; the simulation below also accounts for weekdays and expiry.",
    )
with c4:
    stat_card("Simulated (days)", "> 90" if pd.isna(row["expected_days"]) else int(row["expected_days"]),
              RISK_TONES[row["risk"]], key="one-simulated")

chart_days = max(horizon, int(row["lead_time"]) + 7)  # show the planning horizon, and at least the lead time
chart = projection.iloc[:chart_days].assign(**{"Reorder level": float(row["reorder_level"])})
st.line_chart(chart, y_label=f"Usable stock ({row['unit']})", color=["#0D9488", "#F97316", "#94A3B8"])
lead_end = today + pd.Timedelta(days=int(row["lead_time"]))
st.caption(
    f"Projected usable stock at the end of each day, counting open orders on their expected date. A delivery ordered today would "
    f"arrive around {lead_end:%d %b %Y} ({row['lead_time']}-day lead time). "
    + (f"{row['expires_unused']:,.0f} {row['unit']} are projected to expire before use. " if row["expires_unused"] else "")
)

with st.expander("How the risk level is decided"):
    st.markdown(
        f"""
Rules are checked from top to bottom, and the first one that applies sets the level:

| Level | Rule |
|---|---|
| 🔴 **Critical** | No usable stock now, **or** stock is expected to run out **within the supplier lead time**, so a delivery ordered today would probably arrive too late. |
| 🟠 **High** | Usable stock is **at or below the reorder level**, **or** stock could run out within the lead time **if demand is at the top of its likely range** (busy period). |
| 🟡 **Medium** | Stock is expected to run out **after** the lead time but **within the planning horizon** ({horizon} days). |
| 🟢 **Low** | Stock is expected to last **beyond** the planning horizon. |
| ⚪ **Unknown** | No demand history, so no forecast. |

These are fixed rules applied to a forecast. They are not a separate AI prediction.
"""
    )

with st.expander("How the stock is simulated, and its assumptions"):
    st.markdown(
        f"""
- **Starting stock:** usable stock today, meaning batches that have not expired.
- **Demand:** the 7-day forecast from the Forecasting page, spread over the weekdays using the medicine's own pattern
  in the last 8 weeks. **After the first 7 days, demand is assumed to stay at the same weekly level.** This
  extrapolation gets less reliable the further ahead it goes.
- **Busy period:** the same simulation with weekly demand at the top of the forecast's likely range.
- **Each day:** batches expiring that day are removed; demand is taken from the earliest-expiring batch first (FEFO);
  the first day demand cannot be met is the projected stockout day.
- **Incoming deliveries:** open orders arrive in full on their expected date (assumed not to expire within the
  simulation). Overdue orders are **not** counted. Nothing else new is assumed to arrive.
- **Lead time:** from the Medicines page. If it is missing, {DEFAULT_LEAD_TIME_DAYS} days is assumed and flagged.
- **Simple estimate** = usable stock ÷ forecast daily demand. It is shown for comparison, and can be longer than the
  simulation when batches expire or demand is concentrated on certain weekdays.
- Simulated up to {SIMULATION_DAYS} days ahead. Data is synthetic, and this is not a clinical or supply guarantee.
"""
    )
