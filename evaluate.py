"""Re-run the SmartMed AI evaluation and write docs/EVALUATION.md.

Usage (from the smartmed_ai folder, with the virtual environment active):

    python evaluate.py

The report records exactly what data was used, how each forecasting method
performed on held-out periods, and a snapshot of the alerts. Everything is
computed from the current local database, which holds SYNTHETIC data, so the
report describes the software pipeline, not real-world accuracy.
"""
from datetime import date, datetime
from pathlib import Path

import pandas as pd

import expiry
import forecasting
import reorder
import stockout
from database import dataset_info, reconciliation_mismatches
from forecasting import METHODS

REPORT_PATH = Path(__file__).parent / "docs" / "EVALUATION.md"


def markdown_table(frame: pd.DataFrame) -> str:
    """A small Markdown table writer (avoids an extra dependency)."""
    header = "| " + " | ".join(str(c) for c in frame.columns) + " |"
    divider = "|" + "|".join("---" for _ in frame.columns) + "|"
    rows = ["| " + " | ".join(str(v) for v in row) + " |" for row in frame.itertuples(index=False)]
    return "\n".join([header, divider, *rows])


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def build_report() -> str:
    info = dataset_info()
    raw = forecasting.load_demand_history()
    history, summary = forecasting.prepare_history(raw)
    labelled = forecasting.build_features(history).dropna(subset=["target"])
    result = forecasting.run_forecasting(raw)
    periods = forecasting.backtest(labelled)
    today = date.today()

    # --- main comparison ---
    main = result.metrics.reset_index()
    main_table = pd.DataFrame({
        "Method": main["method"].map(METHODS),
        "WAPE": main["wape"].map(pct),
        "MAE (units/week)": main["mae"].map(lambda v: f"{v:.1f}"),
        "Bias": main["bias"].map(lambda v: f"{v * 100:+.1f}%"),
        "Used": main["method"].map(lambda m: "✅" if m == result.chosen_method else ""),
    })
    attrs = result.metrics.attrs

    # --- backtest ---
    wide = periods.pivot_table(index=["period", "test_first", "test_last", "train_rows"], columns="method", values="wape").reset_index()
    methods = [m for m in METHODS if m in wide.columns]
    backtest_table = pd.DataFrame({
        "Period": wide["period"],
        "Held-out forecast days": wide.apply(lambda r: f"{r['test_first']:%d %b %Y} – {r['test_last']:%d %b %Y}", axis=1),
        "Training rows": wide["train_rows"].map(lambda v: f"{v:,}"),
        **{METHODS[m]: wide[m].map(pct) for m in methods},
        "Best": wide[methods].idxmin(axis=1).map(METHODS),
    })
    averages = periods.groupby("method")[["wape", "bias"]].mean()
    average_table = pd.DataFrame({
        "Method": [METHODS[m] for m in methods],
        "Average WAPE": [pct(averages.loc[m, "wape"]) for m in methods],
        "Average bias": [f"{averages.loc[m, 'bias'] * 100:+.1f}%" for m in methods],
        "Periods won": [int((wide[methods].idxmin(axis=1) == m).sum()) for m in methods],
    })

    # --- per medicine ---
    per_medicine = result.per_medicine.copy()
    per_medicine_table = pd.DataFrame({
        "Medicine": per_medicine["medicine"],
        **{METHODS[m]: per_medicine[m].map(pct) for m in methods if m in per_medicine},
    }).sort_values(METHODS[result.chosen_method], key=lambda col: col.str.rstrip("%").astype(float), ascending=False)

    # --- snapshot of alerts ---
    risk, _ = stockout.assess_risk(result.forecast, history, horizon=30)
    suggestions = reorder.suggest_reorders(risk)
    batches = expiry.assess_expiry(result.forecast, history)
    counts = lambda series, order: ", ".join(f"{level} {int((series == level).sum())}" for level in order)  # noqa: E731

    rf_wins = int((wide[methods].idxmin(axis=1) == "random_forest").sum()) if "random_forest" in methods else 0
    ranked = averages["wape"].sort_values()
    best, runner_up = ranked.index[0], ranked.index[1]
    gap_points = (ranked.iloc[1] - ranked.iloc[0]) * 100
    return f"""# SmartMed AI — evaluation report

> **Generated** {datetime.now():%Y-%m-%d %H:%M} by `python evaluate.py`.
> **Data:** {info.get('source', 'unknown').upper()} — generator version {info.get('generator_version')}, seed {info.get('seed')},
> {summary['first_day']:%d %b %Y} to {summary['last_day']:%d %b %Y}. These results show that the pipeline works on
> invented data. **They are not evidence of accuracy in any real facility.**

## 1. Data checks

| Check | Result |
|---|---|
| Active medicines | {summary['medicines']} |
| Daily records | {summary['daily_rows']:,} |
| Missing days (kept empty, not treated as zero) | {summary['missing_days']} |
| Duplicate / negative rows removed | {summary['duplicate_rows_removed']} / {summary['negative_rows_removed']} |
| Stockout days (their unmet demand is included in demand) | {summary['stockout_days']} |
| Batches whose quantity does not match their transactions | {len(reconciliation_mismatches())} |

## 2. Forecast target and methods

- **Target:** total demand (dispensed + unmet) per medicine over the next {forecasting.HORIZON_DAYS} days.
- **Methods:** {", ".join(METHODS.values())}.
- **Metrics:** WAPE = total absolute error ÷ total actual demand (main measure; lower is better). MAE = average
  absolute error per medicine-week, in units. Bias = total over-forecast (+) or under-forecast (−) ÷ total actual demand.

## 3. Main held-out comparison (used by the app to choose a method)

Forecast days {attrs['test_first']:%d %b %Y} – {attrs['test_last']:%d %b %Y} ({attrs['test_rows']:,} forecasts).
Random Forest trained on {attrs['train_rows']:,} examples whose outcomes ended before that period.

{markdown_table(main_table)}

**Choice:** {result.choice_reason}

## 4. Backtest over {len(wide)} consecutive periods

The same comparison repeated on earlier 8-week blocks, each with methods trained only on data from before it.

{markdown_table(backtest_table)}

{markdown_table(average_table)}

**Reading this:** Random Forest was best in {rf_wins} of {len(wide)} periods. Averaged over all periods, the best
method was {METHODS[best]} and the runner-up {METHODS[runner_up]}, {gap_points:.2f} percentage points of WAPE apart.
A gap this size, which also changes direction between periods, is not evidence that one of them is reliably better.
The 7-day moving average was {"the worst in every period" if (wide[methods].idxmax(axis=1) == "ma7").all() else "not consistently the worst"}.

## 5. Error by medicine (main held-out period, WAPE, hardest first)

{markdown_table(per_medicine_table)}

Seasonal items (such as malaria tests) and low-volume items tend to be the hardest to forecast; check the top rows above.

## 6. Snapshot of alerts on {today:%d %b %Y} (not evaluated against outcomes)

These rule-based outputs use the forecast above. They have **not** been checked against what happened afterwards,
because no future data exists for them yet.

| Output | Counts |
|---|---|
| Stockout risk (30-day horizon) | {counts(risk['risk'], stockout.RISK_ORDER)} |
| Reorder priority | {counts(suggestions['priority'], reorder.PRIORITY_ORDER)} |
| Batch expiry status | {counts(batches['status'], expiry.STATUS_ORDER) if not batches.empty else "no batches"} |

## 7. What this evaluation cannot tell you

- How accurate forecasts would be with **real facility records** (none have been used).
- Whether stockout, reorder, or expiry alerts would have prevented real shortages or waste.
- How the methods behave over a **full seasonal cycle** in held-out data: only one synthetic year exists.
- How results change when unmet demand is **not** recorded, as is common in real records.

See `docs/LIMITATIONS.md` for when not to trust these outputs.
"""


if __name__ == "__main__":
    REPORT_PATH.parent.mkdir(exist_ok=True)
    REPORT_PATH.write_text(build_report(), encoding="utf-8")
    print(f"Evaluation report written to {REPORT_PATH}")
