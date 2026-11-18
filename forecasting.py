"""7-day demand forecasting for SmartMed AI (Milestone 5).

WHAT IS FORECAST
    For each medicine: total DEMAND over the next 7 days, where
    demand = units dispensed + units requested but not available (unmet).
    Using demand rather than dispensing means a stockout is not mistaken for
    people needing less medicine.

THE PIPELINE (each step is one function below)
    1. load_demand_history()  - read daily records from the database
    2. prepare_history()      - check, clean, and sort them by date
    3. build_features()       - describe recent demand at each "forecast day"
    4. evaluate()             - compare methods on the most recent 8 weeks,
                                which the methods never see while training
    5. run_forecasting()      - do all of the above, choose a method, and
                                forecast the 7 days after the history ends

METHODS COMPARED
    - Moving average, 7 days:  next week = the last 7 days' demand
    - Moving average, 28 days: next week = average week over the last 28 days
    - Random Forest (scikit-learn), trained on all medicines together

The Random Forest is only used if it beats the best moving average by at
least 5% on held-out data; otherwise the simpler, more transparent method is
kept. The data used here is SYNTHETIC, so these results show that the
pipeline works, not how accurate it would be in a real facility.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor

from database import connect

HORIZON_DAYS = 7         # forecast the total for the next 7 days
SCALE_DAYS = 56          # each medicine's "usual week" = average week over the last 8 weeks
TEST_ORIGINS = 56        # hold out the last 56 forecast days (8 weeks) for evaluation
MIN_TRAIN_ROWS = 300     # below this, the Random Forest is not trained at all
REQUIRED_IMPROVEMENT = 0.05  # Random Forest must cut WAPE by 5% to replace a baseline
BACKTEST_PERIODS = 4     # the backtest repeats the evaluation on 4 consecutive 8-week blocks

# Features given to the Random Forest. All demand features are divided by the
# medicine's usual week, so "1.2" means "20% above its usual level". This lets
# one model learn from high-volume and low-volume medicines at the same time.
FEATURES = ["last_7_ratio", "last_14_ratio", "last_28_ratio", "trend_ratio", "stockout_days_7"]

METHODS = {
    "ma7": "Moving average (7 days)",
    "ma28": "Moving average (28 days)",
    "random_forest": "Random Forest",
}
BASELINES = ["ma7", "ma28"]


@dataclass
class ForecastResult:
    """Everything the Forecasting page needs to display."""

    data_summary: dict
    metrics: pd.DataFrame           # one row per method: WAPE, MAE, bias
    per_medicine: pd.DataFrame      # WAPE per medicine per method
    test_predictions: pd.DataFrame  # held-out forecasts next to the actual demand
    chosen_method: str
    choice_reason: str
    interval: tuple[float, float]   # typical range, as multiples of the forecast
    forecast: pd.DataFrame          # the forecast for the next 7 days
    notes: list[str] = field(default_factory=list)


# ---------- 1. load ----------

def load_demand_history() -> pd.DataFrame:
    """Read the simulated daily history of active medicines from the database."""
    with connect() as connection:
        return pd.read_sql_query(
            """
            SELECT c.medicine_id, m.name AS medicine, m.unit, c.usage_date AS day,
                   c.quantity_dispensed AS dispensed, c.unmet_quantity AS unmet, c.stockout
            FROM daily_consumption AS c
            JOIN medicines AS m ON m.id = c.medicine_id
            WHERE m.active = 1
            """,
            connection,
        )


def data_signature() -> tuple:
    """A short summary of the history that changes whenever the data changes.
    The Forecasting page uses it to know when to re-run the pipeline."""
    with connect() as connection:
        return tuple(
            connection.execute(
                """SELECT COUNT(*), MAX(c.usage_date), SUM(c.quantity_dispensed + c.unmet_quantity),
                          GROUP_CONCAT(DISTINCT m.id)
                   FROM daily_consumption AS c JOIN medicines AS m ON m.id = c.medicine_id
                   WHERE m.active = 1"""
            ).fetchone()
        )


# ---------- 2. validate, clean, sort ----------

def prepare_history(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Check the records and return one row per medicine per day, oldest first.

    Missing days are kept as empty (NaN), not filled with zero: a missing record
    is not the same as zero demand. Any 7/28/56-day window that touches a
    missing day is left out of training and evaluation.
    """
    history = raw.copy()
    history["day"] = pd.to_datetime(history["day"])
    problems = int((history[["dispensed", "unmet"]] < 0).any(axis=1).sum())
    history = history[(history["dispensed"] >= 0) & (history["unmet"] >= 0)]
    duplicates = int(history.duplicated(["medicine_id", "day"]).sum())
    history = history.drop_duplicates(["medicine_id", "day"]).sort_values(["medicine_id", "day"])
    history["demand"] = history["dispensed"] + history["unmet"]

    complete = []
    missing_days = 0
    for medicine_id, group in history.groupby("medicine_id"):
        all_days = pd.date_range(group["day"].min(), group["day"].max(), freq="D")
        filled = group.set_index("day").reindex(all_days).rename_axis("day").reset_index()
        missing_days += int(filled["demand"].isna().sum())
        filled[["medicine_id", "medicine", "unit"]] = [medicine_id, group["medicine"].iloc[0], group["unit"].iloc[0]]
        complete.append(filled)
    history = pd.concat(complete, ignore_index=True) if complete else history

    summary = {
        "medicines": int(history["medicine_id"].nunique()),
        "daily_rows": int(history["demand"].notna().sum()),
        "first_day": history["day"].min(),
        "last_day": history["day"].max(),
        "missing_days": missing_days,
        "duplicate_rows_removed": duplicates,
        "negative_rows_removed": problems,
        "stockout_days": int(history["stockout"].fillna(0).sum()),
    }
    return history, summary


# ---------- 3. features ----------

def build_features(history: pd.DataFrame) -> pd.DataFrame:
    """One row per medicine per "forecast day" (origin).

    Every feature uses only days up to and including the origin. The target is
    the total demand on the 7 days AFTER the origin (empty for the last 7 days,
    whose future is not known yet).
    """
    rows = []
    for medicine_id, group in history.groupby("medicine_id", sort=False):
        group = group.sort_values("day")
        demand = group["demand"].reset_index(drop=True)

        def past_total(days: int) -> pd.Series:
            return demand.rolling(days, min_periods=days).sum()

        features = pd.DataFrame(
            {
                "medicine_id": medicine_id,
                "medicine": group["medicine"].to_numpy(),
                "unit": group["unit"].to_numpy(),
                "origin": group["day"].to_numpy(),
                "last_7": past_total(7),
                "last_14_weekly": past_total(14) / 2,
                "last_28_weekly": past_total(28) / 4,
                "usual_week": past_total(SCALE_DAYS) / (SCALE_DAYS / 7),
                "previous_7": demand.shift(7).rolling(7, min_periods=7).sum(),
                "stockout_days_7": group["stockout"].reset_index(drop=True).rolling(7, min_periods=7).sum(),
                # demand on origin+1 ... origin+7
                "target": demand.shift(-HORIZON_DAYS).rolling(HORIZON_DAYS, min_periods=HORIZON_DAYS).sum(),
            }
        )
        rows.append(features)

    features = pd.concat(rows, ignore_index=True)
    features = features.dropna(subset=["last_7", "last_28_weekly", "usual_week", "previous_7", "stockout_days_7"])
    scale = features["usual_week"].clip(lower=1)  # avoid dividing by zero for unused medicines
    features["last_7_ratio"] = features["last_7"] / scale
    features["last_14_ratio"] = features["last_14_weekly"] / scale
    features["last_28_ratio"] = features["last_28_weekly"] / scale
    features["trend_ratio"] = (features["last_7"] - features["previous_7"]) / scale
    return features.reset_index(drop=True)


# ---------- methods ----------

def train_random_forest(train: pd.DataFrame) -> RandomForestRegressor:
    """Learn next week's demand as a multiple of each medicine's usual week."""
    model = RandomForestRegressor(n_estimators=200, min_samples_leaf=5, random_state=0, n_jobs=-1)
    model.fit(train[FEATURES], train["target"] / train["usual_week"].clip(lower=1))
    return model


def predict(method: str, rows: pd.DataFrame, model: RandomForestRegressor | None = None) -> np.ndarray:
    if method == "ma7":
        return rows["last_7"].to_numpy(dtype=float)
    if method == "ma28":
        return rows["last_28_weekly"].to_numpy(dtype=float)
    if method == "random_forest":
        return model.predict(rows[FEATURES]) * rows["usual_week"].clip(lower=1).to_numpy()
    raise ValueError(f"Unknown method {method}")


def error_metrics(actual: pd.Series, predicted: pd.Series) -> dict:
    """WAPE: total absolute error ÷ total actual demand (lower is better).
    MAE: average absolute error per medicine-week, in units.
    Bias: total over-forecast (+) or under-forecast (−) ÷ total actual demand."""
    error = predicted - actual
    total = actual.sum()
    return {
        "wape": float(error.abs().sum() / total) if total else float("nan"),
        "mae": float(error.abs().mean()),
        "bias": float(error.sum() / total) if total else float("nan"),
    }


# ---------- 4. evaluate ----------

def chronological_split(labelled: pd.DataFrame, periods_back: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Test = a block of TEST_ORIGINS consecutive forecast days. Train = only forecast days
    whose whole 7-day target period finished before the test block starts, so no future
    information leaks into training.

    periods_back=0 tests on the most recent block; 1 on the block before it; and so on.
    """
    origins = np.sort(labelled["origin"].unique())
    end = len(origins) - periods_back * TEST_ORIGINS
    if end - TEST_ORIGINS < 0:
        raise ValueError("Not enough history for this many evaluation periods.")
    test_start, test_end = origins[end - TEST_ORIGINS], origins[end - 1]
    test = labelled[(labelled["origin"] >= test_start) & (labelled["origin"] <= test_end)]
    train = labelled[labelled["origin"] + pd.Timedelta(days=HORIZON_DAYS) <= test_start]
    return train, test


def predict_period(train: pd.DataFrame, test: pd.DataFrame) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Add each method's forecast to a copy of `test`. Returns (test with forecasts, methods, notes)."""
    test = test.copy()
    methods, notes = list(BASELINES), []
    for method in BASELINES:
        test[method] = predict(method, test)
    if len(train) >= MIN_TRAIN_ROWS:
        test["random_forest"] = predict("random_forest", test, train_random_forest(train))
        methods.append("random_forest")
    else:
        notes.append(
            f"Random Forest skipped: only {len(train)} training rows (needs {MIN_TRAIN_ROWS}). "
            "The moving averages are used instead."
        )
    return test, methods, notes


def evaluate(labelled: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[str], list[str]]:
    """Return (metrics per method, held-out predictions, methods evaluated, notes) for the most recent block."""
    train, test = chronological_split(labelled)
    test, methods, notes = predict_period(train, test)
    metrics = pd.DataFrame(
        [{"method": method, **error_metrics(test["target"], test[method])} for method in methods]
    ).set_index("method")
    metrics.attrs.update(
        train_rows=len(train), test_rows=len(test),
        train_first=train["origin"].min(), train_last=train["origin"].max(),
        test_first=test["origin"].min(), test_last=test["origin"].max(),
    )
    return metrics, test, methods, notes


def backtest(labelled: pd.DataFrame, periods: int = BACKTEST_PERIODS) -> pd.DataFrame:
    """Repeat the evaluation on `periods` consecutive 8-week blocks, newest first.

    Each block is scored with methods trained only on data from before it. If the
    ranking of methods changes between blocks, a single held-out period is not
    enough to say one method is better. Returns one row per block per method.
    """
    rows = []
    for back in range(periods):
        try:
            train, test = chronological_split(labelled, periods_back=back)
        except ValueError:
            break
        scored, methods, _ = predict_period(train, test)
        for method in methods:
            rows.append({
                "period": back + 1, "test_first": test["origin"].min(), "test_last": test["origin"].max(),
                "train_rows": len(train), "method": method, **error_metrics(scored["target"], scored[method]),
            })
    return pd.DataFrame(rows)


def choose_method(metrics: pd.DataFrame) -> tuple[str, str]:
    best_baseline = metrics.loc[BASELINES, "wape"].idxmin()
    if "random_forest" not in metrics.index:
        return best_baseline, f"{METHODS[best_baseline]} had the lowest error of the moving averages."
    baseline_wape = metrics.loc[best_baseline, "wape"]
    forest_wape = metrics.loc["random_forest", "wape"]
    improvement = (baseline_wape - forest_wape) / baseline_wape
    if improvement >= REQUIRED_IMPROVEMENT:
        return "random_forest", (
            f"Random Forest reduced the held-out error by {improvement:.0%} compared with "
            f"{METHODS[best_baseline]} (at least {REQUIRED_IMPROVEMENT:.0%} was required)."
        )
    comparison = f"only {improvement:.0%} better than" if improvement > 0 else f"{-improvement:.0%} worse than"
    return best_baseline, (
        f"Random Forest was {comparison} {METHODS[best_baseline]} on the held-out weeks "
        f"(at least {REQUIRED_IMPROVEMENT:.0%} better was required), so the simpler, more transparent method is kept."
    )


# ---------- 5. everything together ----------

def run_forecasting(raw: pd.DataFrame | None = None) -> ForecastResult:
    """Run the full pipeline and forecast the 7 days after the history ends."""
    history, summary = prepare_history(load_demand_history() if raw is None else raw)
    features = build_features(history)
    labelled = features.dropna(subset=["target"])

    metrics, test, methods, notes = evaluate(labelled)
    chosen, reason = choose_method(metrics)

    per_medicine = (
        test.groupby("medicine")
        .apply(lambda rows: pd.Series({m: error_metrics(rows["target"], rows[m])["wape"] for m in methods}),
               include_groups=False)
        .reset_index()
    )

    # Likely range: how far actual demand landed from the chosen method's
    # forecast during the held-out weeks (middle 80% of cases). Worked out per
    # medicine, because low-volume medicines vary much more than high-volume ones.
    usable = test[test[chosen] > 0].assign(ratio=lambda rows: rows["target"] / rows[chosen])
    interval = (float(usable["ratio"].quantile(0.10)), float(usable["ratio"].quantile(0.90)))
    ranges = usable.groupby("medicine_id")["ratio"].quantile([0.10, 0.90]).unstack()
    ranges.columns = ["low_ratio", "high_ratio"]

    # Forecast from the last day of history. The Random Forest is retrained on
    # every week whose outcome is known, including the held-out weeks.
    latest = features.sort_values("origin").groupby("medicine_id").tail(1).copy()
    model = train_random_forest(labelled) if chosen == "random_forest" else None
    latest["forecast"] = np.round(predict(chosen, latest, model), 1)
    latest = latest.join(ranges, on="medicine_id")
    latest[["low_ratio", "high_ratio"]] = latest[["low_ratio", "high_ratio"]].fillna({"low_ratio": interval[0], "high_ratio": interval[1]})
    latest["low"] = np.floor(latest["forecast"] * latest["low_ratio"])
    latest["high"] = np.ceil(latest["forecast"] * latest["high_ratio"])
    latest["period_start"] = latest["origin"] + pd.Timedelta(days=1)
    latest["period_end"] = latest["origin"] + pd.Timedelta(days=HORIZON_DAYS)
    forecast = latest[
        ["medicine_id", "medicine", "unit", "last_7", "usual_week", "forecast", "low", "high",
         "period_start", "period_end", "stockout_days_7"]
    ].sort_values("medicine").reset_index(drop=True)

    return ForecastResult(summary, metrics, per_medicine, test, chosen, reason, interval, forecast, notes)
