"""Checks for the forecasting pipeline. No database needed: each test builds a
small made-up history in memory.

Run from the smartmed_ai folder:
    python -m unittest discover tests
"""
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import forecasting  # noqa: E402
from forecasting import HORIZON_DAYS  # noqa: E402


def make_history(daily_demand, medicine_id=1, name="Test", start="2026-01-01", unmet=None):
    days = pd.date_range(start, periods=len(daily_demand), freq="D")
    unmet = unmet if unmet is not None else [0] * len(daily_demand)
    return pd.DataFrame(
        {
            "medicine_id": medicine_id, "medicine": name, "unit": "tablets", "day": days.strftime("%Y-%m-%d"),
            "dispensed": daily_demand, "unmet": unmet, "stockout": [int(u > 0) for u in unmet],
        }
    )


class ForecastingTest(unittest.TestCase):
    def test_target_is_the_next_seven_days_and_features_use_only_the_past(self):
        history, _ = forecasting.prepare_history(make_history(list(range(1, 101))))  # demand = 1, 2, 3, ...
        features = forecasting.build_features(history).set_index("origin")
        row = features.loc[pd.Timestamp("2026-03-01")]  # day 60, demand 60
        self.assertEqual(row["last_7"], sum(range(54, 61)))      # days 54..60
        self.assertEqual(row["target"], sum(range(61, 68)))      # days 61..67
        self.assertTrue(np.isnan(features["target"].iloc[-1]))   # future unknown for the last day

    def test_unmet_requests_count_as_demand(self):
        raw = make_history([10] * 70, unmet=[0] * 60 + [5] * 10)
        history, _ = forecasting.prepare_history(raw)
        self.assertEqual(history["demand"].iloc[-1], 15)

    def test_missing_days_are_not_treated_as_zero_demand(self):
        raw = make_history([10] * 80).drop(index=[40])  # one day missing
        history, summary = forecasting.prepare_history(raw)
        self.assertEqual(summary["missing_days"], 1)
        features = forecasting.build_features(history)
        # No row whose 56-day look-back window includes the missing day survives.
        self.assertTrue((features["origin"] >= pd.Timestamp("2026-01-01") + pd.Timedelta(days=40 + 55)).all())

    def test_moving_averages_on_constant_demand(self):
        history, _ = forecasting.prepare_history(make_history([10] * 80))
        features = forecasting.build_features(history)
        self.assertTrue((forecasting.predict("ma7", features) == 70).all())
        self.assertTrue((forecasting.predict("ma28", features) == 70).all())

    def test_chronological_split_has_no_overlap(self):
        raw = pd.concat([make_history([10 + i % 7 for i in range(200)], medicine_id=m, name=f"M{m}") for m in (1, 2)])
        history, _ = forecasting.prepare_history(raw)
        labelled = forecasting.build_features(history).dropna(subset=["target"])
        train, test = forecasting.chronological_split(labelled)
        last_training_target_day = train["origin"].max() + pd.Timedelta(days=HORIZON_DAYS)
        first_test_target_day = test["origin"].min() + pd.Timedelta(days=1)
        self.assertLess(last_training_target_day, first_test_target_day)
        self.assertEqual(test["origin"].nunique(), forecasting.TEST_ORIGINS)

    def test_backtest_periods_do_not_overlap_and_never_train_on_the_future(self):
        raw = pd.concat([make_history([10 + i % 7 for i in range(330)], medicine_id=m, name=f"M{m}") for m in (1, 2, 3)])
        history, _ = forecasting.prepare_history(raw)
        labelled = forecasting.build_features(history).dropna(subset=["target"])
        periods = forecasting.backtest(labelled, periods=3).drop_duplicates("period").sort_values("period")
        self.assertEqual(list(periods["period"]), [1, 2, 3])
        # Each older period ends before the newer one starts.
        self.assertTrue((periods["test_last"].iloc[1:].to_numpy() < periods["test_first"].iloc[:-1].to_numpy()).all())
        for back in range(3):
            train, test = forecasting.chronological_split(labelled, periods_back=back)
            self.assertLessEqual(train["origin"].max() + pd.Timedelta(days=HORIZON_DAYS), test["origin"].min())
        # Period 1 is the same block the app uses for its main comparison.
        _, main_test = forecasting.chronological_split(labelled)
        self.assertEqual(periods["test_first"].iloc[0], main_test["origin"].min())

    def test_backtest_stops_when_history_runs_out(self):
        history, _ = forecasting.prepare_history(make_history([10] * 200))
        labelled = forecasting.build_features(history).dropna(subset=["target"])
        self.assertLess(forecasting.backtest(labelled, periods=10)["period"].max(), 10)

    def test_error_metrics(self):
        result = forecasting.error_metrics(pd.Series([10.0, 20.0]), pd.Series([12.0, 15.0]))
        self.assertAlmostEqual(result["wape"], 7 / 30)
        self.assertAlmostEqual(result["mae"], 3.5)
        self.assertAlmostEqual(result["bias"], -3 / 30)

    def test_random_forest_must_clearly_beat_the_baseline(self):
        metrics = pd.DataFrame({"wape": [0.20, 0.18, 0.175]}, index=["ma7", "ma28", "random_forest"])
        self.assertEqual(forecasting.choose_method(metrics)[0], "ma28")  # only ~3% better
        metrics.loc["random_forest", "wape"] = 0.15
        self.assertEqual(forecasting.choose_method(metrics)[0], "random_forest")

    def test_too_little_data_skips_random_forest(self):
        history, _ = forecasting.prepare_history(make_history([10 + i % 7 for i in range(140)]))
        labelled = forecasting.build_features(history).dropna(subset=["target"])
        metrics, _, methods, notes = forecasting.evaluate(labelled)
        self.assertNotIn("random_forest", methods)
        self.assertTrue(notes)

    def test_full_pipeline_produces_one_forecast_per_medicine(self):
        rng = np.random.default_rng(0)
        raw = pd.concat(
            [make_history(list(rng.poisson(level, 240)), medicine_id=m, name=f"M{m}") for m, level in [(1, 5), (2, 40), (3, 90)]]
        )
        result = forecasting.run_forecasting(raw)
        self.assertEqual(len(result.forecast), 3)
        self.assertTrue((result.forecast["low"] <= result.forecast["forecast"]).all())
        self.assertTrue((result.forecast["forecast"] <= result.forecast["high"]).all())
        self.assertEqual(result.forecast["period_start"].iloc[0], pd.Timestamp("2026-01-01") + pd.Timedelta(days=240))


if __name__ == "__main__":
    unittest.main()
