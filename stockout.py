"""Stockout risk for SmartMed AI (Milestone 6).

For each medicine, stock is simulated forward day by day:

    1. Confirmed deliveries due that day arrive (none are recorded yet; the
       code accepts them so they can be added later).
    2. Batches that expire that day are removed from usable stock.
    3. That day's expected demand is taken, earliest-expiring batch first.
    4. The first day demand cannot be fully met is the projected stockout day.

Daily demand comes from the 7-day forecast (Milestone 5), spread over the
weekdays using the medicine's own pattern in the last 8 weeks. Beyond the
first 7 days, demand is ASSUMED to continue at the same weekly level.

Two scenarios are simulated: "expected" (the forecast) and "busy" (the top
of the forecast's likely range). Each medicine then gets a risk level from
fixed, explained rules (see classify). This is arithmetic on a forecast, not
a guarantee: real demand, deliveries, and expiry can differ.
"""
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from database import batch_rows, inventory_rows, order_rows

SIMULATION_DAYS = 90         # how far ahead stock is simulated
DEFAULT_LEAD_TIME_DAYS = 14  # used (and flagged) when a medicine has no lead time recorded
WEEKDAY_HISTORY_DAYS = 56    # weekday pattern is learned from the last 8 weeks
RISK_ORDER = ["Critical", "High", "Medium", "Low", "Unknown"]


# Incoming orders have no batch or expiry date until they arrive. For the simulation
# they are assumed not to expire within the simulated period.
ORDER_EXPIRY_ASSUMED = date(9999, 12, 31)


@dataclass
class Delivery:
    """A confirmed incoming delivery (from an open incoming order)."""

    arrival: date
    quantity: int
    expiry: date


@dataclass
class Projection:
    stockout_day: int | None   # days from the start until demand can't be met (None = not within the simulation)
    balances: list[float]      # usable stock at the end of each day
    expired_units: float       # stock thrown away because it expired during the simulation


def weekday_shares(history: pd.DataFrame) -> dict[int, list[float]]:
    """For each medicine, the share of a typical week's demand on each weekday (Monday first).

    Uses only the last WEEKDAY_HISTORY_DAYS days. Falls back to equal shares if no demand was recorded.
    """
    shares = {}
    for medicine_id, group in history.groupby("medicine_id"):
        recent = group[group["day"] > group["day"].max() - pd.Timedelta(days=WEEKDAY_HISTORY_DAYS)]
        by_weekday = recent.groupby(recent["day"].dt.weekday)["demand"].mean().reindex(range(7), fill_value=0)
        total = by_weekday.sum()
        shares[medicine_id] = (by_weekday / total).tolist() if total > 0 else [1 / 7] * 7
    return shares


def daily_demand(weekly_total: float, shares: list[float], start: date, days: int) -> list[float]:
    """Spread a weekly total over `days` days using the weekday shares."""
    return [weekly_total * shares[(start + timedelta(days=i)).weekday()] for i in range(days)]


def simulate_stock(
    batches: list[tuple[float, date]], demand: list[float], start: date, deliveries: list[Delivery] = ()
) -> Projection:
    """Simulate usable stock day by day. `batches` = [(quantity, expiry date)]."""
    stock = [[quantity, expiry] for quantity, expiry in batches]
    balances, expired, stockout_day = [], 0.0, None
    for i, needed in enumerate(demand):
        day = start + timedelta(days=i)
        stock += [[d.quantity, d.expiry] for d in deliveries if d.arrival == day]
        expired += sum(quantity for quantity, expiry in stock if expiry <= day)
        stock = sorted([b for b in stock if b[1] > day], key=lambda b: b[1])  # earliest expiry first
        for batch in stock:
            take = min(batch[0], needed)
            batch[0] -= take
            needed -= take
        stock = [b for b in stock if b[0] > 1e-9]
        if needed > 1e-9 and stockout_day is None:
            stockout_day = i
        balances.append(sum(quantity for quantity, _ in stock))
    return Projection(stockout_day, balances, expired)


def days(n: int) -> str:
    return f"{n} day" if n == 1 else f"{n} days"


def classify(
    usable_stock: float, reorder_level: int, lead_time: int, expected_day: int | None,
    busy_day: int | None, horizon: int, on_order: float = 0,
) -> tuple[str, str]:
    """Return (risk level, plain-language reason). Rules are checked from most to least serious.

    `on_order` = open orders expected to arrive (overdue orders are not counted)."""
    if usable_stock <= 0:
        return "Critical", "No usable (unexpired) stock now."
    if expected_day is not None and expected_day <= lead_time:
        return "Critical", (
            f"Expected to run out in {days(expected_day)}, before a delivery ordered today "
            f"could arrive ({lead_time}-day lead time)."
        )
    if usable_stock + on_order <= reorder_level:
        detail = f"Usable stock plus stock on order ({usable_stock + on_order:,.0f})" if on_order else f"Usable stock ({usable_stock:,.0f})"
        return "High", f"{detail} is at or below the reorder level ({reorder_level:,})."
    if busy_day is not None and busy_day <= lead_time:
        return "High", (
            f"If demand is at the top of the likely range, it could run out in {days(busy_day)}, "
            f"within the {lead_time}-day lead time."
        )
    if expected_day is not None and expected_day <= horizon:
        return "Medium", (
            f"Expected to run out in {days(expected_day)}: after the lead time, but within the {horizon}-day horizon."
        )
    return "Low", f"Expected stock lasts beyond the {horizon}-day horizon."


def assess_risk(forecast: pd.DataFrame, history: pd.DataFrame, horizon: int = 30, today: date | None = None,
                deliveries: dict[int, list[Delivery]] | None = None) -> tuple[pd.DataFrame, dict]:
    """Return (one row per active medicine with its risk, {medicine_id: daily projection table}).

    `deliveries` defaults to the open incoming orders in the database. Orders whose expected
    arrival has already passed (overdue) are NOT assumed to arrive, and are reported separately.
    """
    today = today or date.today()
    overdue: dict[int, float] = {}
    if deliveries is None:
        deliveries = {}
        for order in order_rows(status="open"):
            arrival = date.fromisoformat(order["expected_arrival"])
            if arrival < today:
                overdue[order["medicine_id"]] = overdue.get(order["medicine_id"], 0) + order["quantity"]
            else:
                deliveries.setdefault(order["medicine_id"], []).append(
                    Delivery(arrival, order["quantity"], ORDER_EXPIRY_ASSUMED)
                )
    shares = weekday_shares(history)
    forecasts = forecast.set_index("medicine_id")
    rows, projections = [], {}

    for medicine in inventory_rows():
        medicine_id = medicine["id"]
        usable = [(b["quantity"], date.fromisoformat(b["expiry_date"])) for b in batch_rows(medicine_id)
                  if date.fromisoformat(b["expiry_date"]) > today]
        usable_stock = sum(quantity for quantity, _ in usable)
        lead_assumed = medicine["lead_time_days"] is None
        lead_time = DEFAULT_LEAD_TIME_DAYS if lead_assumed else medicine["lead_time_days"]
        row = {
            "medicine_id": medicine_id, "medicine": medicine["name"], "unit": medicine["unit"],
            "usable_stock": usable_stock, "reorder_level": medicine["reorder_level"],
            "lead_time": lead_time, "lead_time_assumed": lead_assumed,
            "on_order": sum(d.quantity for d in deliveries.get(medicine_id, [])),
            "next_arrival": min((d.arrival for d in deliveries.get(medicine_id, [])), default=None),
            "overdue_on_order": overdue.get(medicine_id, 0),
        }
        if medicine_id not in forecasts.index:
            rows.append({**row, "risk": "Unknown", "reason": "No demand history to forecast from."})
            continue

        weekly, busy_weekly = forecasts.loc[medicine_id, "forecast"], forecasts.loc[medicine_id, "high"]
        pattern = shares.get(medicine_id, [1 / 7] * 7)
        incoming = deliveries.get(medicine_id, [])
        expected = simulate_stock(usable, daily_demand(weekly, pattern, today, SIMULATION_DAYS), today, incoming)
        busy = simulate_stock(usable, daily_demand(busy_weekly, pattern, today, SIMULATION_DAYS), today, incoming)
        risk, reason = classify(usable_stock, medicine["reorder_level"], lead_time,
                                expected.stockout_day, busy.stockout_day, horizon, row["on_order"])
        if row["on_order"]:
            reason += f" Counts {row['on_order']:,.0f} on order, expected {row['next_arrival']:%d %b}."
        if row["overdue_on_order"]:
            reason += f" ⚠️ {row['overdue_on_order']:,.0f} on an overdue order are not counted: check with the supplier."
        if lead_assumed:
            reason += f" (Lead time not recorded; {DEFAULT_LEAD_TIME_DAYS} days assumed.)"
        rows.append({
            **row, "risk": risk, "reason": reason,
            "forecast_week": weekly, "busy_week": busy_weekly,
            "simple_days": usable_stock / (weekly / 7) if weekly > 0 else None,
            "expected_days": expected.stockout_day, "busy_days": busy.stockout_day,
            "expires_unused": expected.expired_units,
        })
        projections[medicine_id] = pd.DataFrame(
            {"Expected demand": expected.balances, "Busy period": busy.balances},
            index=pd.date_range(today, periods=SIMULATION_DAYS, freq="D"),
        )

    table = pd.DataFrame(rows)
    table["risk_rank"] = table["risk"].map(RISK_ORDER.index)
    table = table.sort_values(["risk_rank", "expected_days", "medicine"], na_position="last").drop(columns="risk_rank")
    return table.reset_index(drop=True), projections
