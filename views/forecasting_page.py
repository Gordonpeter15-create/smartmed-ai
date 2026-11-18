"""Forecasting page: 7-day demand forecasts and how well each method did on held-out data."""
import pandas as pd
import streamlit as st

import forecasting
from forecasting import HORIZON_DAYS, METHODS
from ui import cached_backtest, cached_forecast


st.title("📈 Forecasting")
st.warning(
    "🧪 Forecasts are **estimates** built from **synthetic** data. They show that the forecasting pipeline works, "
    "not how accurate it would be in a real facility. Do not use them for real purchasing or clinical decisions."
)
st.caption(
    f"Target: total **demand** for each medicine over the next **{HORIZON_DAYS} days**, where demand = units "
    "dispensed + units requested but not available. Source: the synthetic `daily_consumption` history."
)

result, _ = cached_forecast()
if result is None:
    st.info("No daily history is available to forecast from.")
    st.stop()
metrics = result.metrics
chosen = result.chosen_method

# ---------- forecast ----------
forecast = result.forecast
start, end = forecast["period_start"].min(), forecast["period_end"].max()
st.subheader(f"Next {HORIZON_DAYS} days: {start:%d %b} – {end:%d %b %Y}")
st.write(
    f"Method used: **{METHODS[chosen]}** (chosen by the held-out comparison below). "
    f"History ends {result.data_summary['last_day']:%d %b %Y}."
)
table = forecast.assign(
    likely_range=forecast.apply(lambda r: f"{r['low']:,.0f} – {r['high']:,.0f}", axis=1),
    recent_stockout=forecast["stockout_days_7"].map(lambda days: f"⚠️ {days:.0f} day(s)" if days > 0 else ""),
)
st.dataframe(
    table[["medicine", "last_7", "forecast", "likely_range", "unit", "recent_stockout"]],
    column_config={
        "medicine": "Medicine",
        "last_7": st.column_config.NumberColumn("Last 7 days (actual)", format="%d"),
        "forecast": st.column_config.NumberColumn(f"Forecast, next {HORIZON_DAYS} days", format="%.0f"),
        "likely_range": st.column_config.TextColumn(
            "Likely range", help="Middle 80% of how far actual demand landed from this method's forecasts "
            "for this medicine during the 8 held-out weeks. Rough, not guaranteed."
        ),
        "unit": "Unit",
        "recent_stockout": st.column_config.TextColumn("Stockout in last 7 days", help="Unmet demand is included in the forecast's history."),
    },
    hide_index=True,
    width="stretch",
)
st.caption(
    "The likely range comes from only 8 held-out weeks per medicine, so treat it as a rough guide. "
    "See the Stockout risk page for how long current stock is expected to last."
)

# ---------- evaluation ----------
st.subheader("How well each method did on held-out weeks")
attrs = metrics.attrs
st.write(
    f"Each method forecast every medicine's next {HORIZON_DAYS} days from each day between "
    f"**{attrs['test_first']:%d %b} and {attrs['test_last']:%d %b %Y}** ({attrs['test_rows']:,} forecasts). "
    f"The Random Forest learned only from forecast days up to {attrs['train_last']:%d %b %Y} "
    f"({attrs['train_rows']:,} examples), so it never saw the weeks it was tested on."
)
comparison = metrics.reset_index().assign(
    method=lambda d: d["method"].map(METHODS),
    wape=lambda d: d["wape"] * 100,
    bias=lambda d: d["bias"] * 100,
    used=lambda d: d["method"].map(lambda m: "✅" if m == METHODS[chosen] else ""),
)
st.dataframe(
    comparison[["method", "wape", "mae", "bias", "used"]],
    column_config={
        "method": "Method",
        "wape": st.column_config.NumberColumn("WAPE (%)", format="%.1f", help="Total absolute error ÷ total actual demand. Lower is better."),
        "mae": st.column_config.NumberColumn("MAE (units per week)", format="%.1f", help="Average absolute error per medicine-week. Dominated by high-volume medicines."),
        "bias": st.column_config.NumberColumn("Bias (%)", format="%+.1f", help="Positive = forecasts too high on average; negative = too low."),
        "used": "Used",
    },
    hide_index=True,
    width="stretch",
)
st.info(result.choice_reason)
for note in result.notes:
    st.warning(note)

# ---------- backtest ----------
st.subheader("Does the result hold in other periods?")
st.write(
    "The same comparison repeated on earlier 8-week blocks. In each block, methods are trained only on data from "
    "before it. If the best method changes from block to block, one held-out period is not enough to call it better."
)
periods = cached_backtest()
if periods.empty:
    st.info("Not enough history for a backtest.")
else:
    wide = periods.pivot_table(index=["period", "test_first", "test_last"], columns="method", values="wape").reset_index()
    available = [m for m in METHODS if m in wide.columns]
    wide["held_out"] = wide.apply(lambda r: f"{r['test_first']:%d %b} – {r['test_last']:%d %b %Y}", axis=1)
    wide["best"] = wide[available].idxmin(axis=1).map(METHODS)
    st.dataframe(
        wide[["held_out", *available, "best"]].assign(**{m: wide[m] * 100 for m in available}),
        column_config={
            "held_out": "Held-out forecast days",
            **{m: st.column_config.NumberColumn(f"{METHODS[m]} WAPE (%)", format="%.1f") for m in available},
            "best": "Best",
        },
        hide_index=True,
        width="stretch",
    )
    averages = periods.groupby("method")["wape"].mean()
    st.caption(
        "Average WAPE over all periods: " + " · ".join(f"{METHODS[m]} {averages[m]:.1%}" for m in available)
        + ". Full report: `python evaluate.py` → docs/EVALUATION.md."
    )

# ---------- one medicine ----------
st.subheader("Compare methods for one medicine")
medicine = st.selectbox("Medicine", sorted(result.test_predictions["medicine"].unique()))
rows = result.test_predictions[result.test_predictions["medicine"] == medicine]
unit = rows["unit"].iloc[0]
methods = [m for m in METHODS if m in rows.columns]
chart = rows.assign(week_starting=rows["origin"] + pd.Timedelta(days=1)).set_index("week_starting")[["target", *methods]]
chart = chart.rename(columns={"target": "Actual demand", **METHODS})
line_colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd"]  # actual, then one colour per method
st.line_chart(
    chart, y_label=f"{unit} in the next {HORIZON_DAYS} days", x_label="Forecast period starting",
    color=line_colors[: len(chart.columns)],
)
st.caption(
    "Each point is one forecast: the total for the 7 days starting on that date. The 7-day average tends to "
    "repeat last week's spike one week late; the 28-day average reacts slowly to seasonal change."
)
scores = result.per_medicine[result.per_medicine["medicine"] == medicine][methods].iloc[0]
st.write("WAPE for this medicine: " + " · ".join(f"{METHODS[m]} **{scores[m]:.0%}**" for m in methods))

# ---------- explanations ----------
with st.expander("Data checks"):
    s = result.data_summary
    st.markdown(
        f"""
- Medicines: **{s['medicines']}** · daily records: **{s['daily_rows']:,}** · {s['first_day']:%d %b %Y} to {s['last_day']:%d %b %Y}
- Missing days: **{s['missing_days']}** (left empty, never treated as zero demand)
- Duplicate rows removed: **{s['duplicate_rows_removed']}** · rows with negative values removed: **{s['negative_rows_removed']}**
- Stockout days in the history: **{s['stockout_days']}** (their unmet demand is included)
"""
    )

with st.expander("How the forecast works"):
    st.markdown(
        f"""
1. **History:** daily demand per medicine (dispensed + unmet) from the synthetic history.
2. **Features** for each forecast day, using only that day and earlier: demand in the last 7, 14, and 28 days;
   the change from the previous week; and stockout days in the last week. For the Random Forest, these are divided
   by the medicine's usual week (average over 8 weeks), so one model can learn from all 25 medicines.
3. **Methods:**
   - *Moving average (7 days)*: next week = last week.
   - *Moving average (28 days)*: next week = the average week over the last 4 weeks.
   - *Random Forest* (scikit-learn, 200 trees): learns next week as a multiple of the usual week.
4. **Evaluation:** the last 8 weeks are held out. Methods are scored on how far their forecasts were from what
   actually happened.
5. **Choice:** the Random Forest is used only if it cuts WAPE by at least {forecasting.REQUIRED_IMPROVEMENT:.0%}
   compared with the best moving average. Otherwise the simpler method is kept.
6. **Final forecast:** the chosen method forecasts the {HORIZON_DAYS} days after the history ends. A Random Forest
   would be retrained on all weeks with known results first.

XGBoost was not added. With one year of synthetic data, a second tree-based library adds a dependency without
evidence that it would help. It can be reconsidered with more, real data.
"""
    )

with st.expander("Limitations: when not to trust these forecasts"):
    st.markdown(
        """
- **Synthetic data.** The patterns were invented (see the Dashboard). Accuracy here says nothing about a real facility.
- **One year of history.** Yearly seasonality (such as the malaria season) is seen only once, so it cannot be
  learned reliably. The held-out weeks (September–November) cover part of a seasonal decline.
- **Short evaluation window.** 8 held-out weeks is a small sample. Results may change with a different period.
- **Unmet demand must be recorded.** In real records, unserved requests are often missing. Then a stockout looks
  like low demand, and forecasts will be too low.
- **Sudden events** (outbreaks, campaigns, supply disruptions, new services) are not predictable from past demand alone.
- **App-recorded dispensing is not included yet.** The forecast uses the synthetic history only.
- A responsible staff member must review any forecast before acting on it.
"""
    )
