"""Batch expiry check for SmartMed AI (Milestone 8).

Question: will each batch be used up before it expires, at the forecast demand?

For each medicine, its in-stock batches are used day by day, earliest expiry
first (FEFO), at the demand from the 7-day forecast (spread over weekdays,
then ASSUMED to continue at the same weekly level). Each batch is followed
separately:

    - if it is used up before its expiry date  -> OK
    - if units are left on its expiry date     -> those units are projected to expire unused

Two scenarios are run: "expected" (the forecast) and "quiet period" (the bottom
of the forecast's likely range). Expiry is a risk when demand is LOW, so the
quiet scenario is the cautious one.

Assumptions: no new stock arrives earlier in FEFO order than the existing
batches (new deliveries usually expire later); expired stock is removed on its
expiry date. This is a stock-keeping prompt, not a clinical or regulatory
determination, and long-range demand far beyond 7 days is very uncertain.
"""
from datetime import date, timedelta

import pandas as pd

from database import batch_rows, inventory_rows
from stockout import daily_demand, weekday_shares

MAX_PROJECTION_DAYS = 3 * 365  # stop projecting after about three years
STATUS_ORDER = ["Expired", "At risk", "Watch", "OK", "No forecast"]


def project_batches(batches: list[tuple[str, float, date]], demand: list[float], start: date) -> dict[str, dict]:
    """Use batches FEFO against daily `demand` starting on `start`.

    `batches` = [(batch code, quantity, expiry date)]. Returns, per batch code:
      used_up_day    - day (0 = start) the batch runs out, or None if it never does
      expires_unused - units left in the batch on its expiry date (0 if used up first)
    """
    stock = sorted(([code, quantity, expiry] for code, quantity, expiry in batches), key=lambda b: (b[2], b[0]))
    result = {code: {"used_up_day": None, "expires_unused": 0.0} for code, _, _ in batches}
    for i, needed in enumerate(demand):
        day = start + timedelta(days=i)
        for batch in stock:
            if batch[2] <= day and batch[1] > 1e-9:  # expires today with stock left
                result[batch[0]]["expires_unused"] = batch[1]
                batch[1] = 0
        stock = [b for b in stock if b[2] > day and b[1] > 1e-9]
        if not stock:
            break
        for batch in stock:
            take = min(batch[1], needed)
            batch[1] -= take
            needed -= take
            if batch[1] <= 1e-9 and result[batch[0]]["used_up_day"] is None:
                result[batch[0]]["used_up_day"] = i
            if needed <= 1e-9:
                break
    # Batches still holding stock after the projection ends: count what remains as at risk.
    for code, quantity, _ in stock:
        if quantity > 1e-9 and result[code]["used_up_day"] is None and not result[code]["expires_unused"]:
            result[code]["expires_unused"] = quantity
    return result


def status_for(days_to_expiry: int, expected_unused: float, quiet_unused: float) -> str:
    if days_to_expiry <= 0:
        return "Expired"
    if expected_unused > 0:
        return "At risk"
    if quiet_unused > 0:
        return "Watch"
    return "OK"


def assess_expiry(forecast: pd.DataFrame, history: pd.DataFrame, today: date | None = None) -> pd.DataFrame:
    """One row per in-stock batch of every active medicine, with its expiry status and explanation."""
    today = today or date.today()
    shares = weekday_shares(history)
    forecasts = forecast.set_index("medicine_id")
    rows = []
    for medicine in inventory_rows():
        batches = [(b["batch_code"], b["quantity"], date.fromisoformat(b["expiry_date"])) for b in batch_rows(medicine["id"])]
        if not batches:
            continue
        has_forecast = medicine["id"] in forecasts.index
        if has_forecast:
            weekly = float(forecasts.loc[medicine["id"], "forecast"])
            quiet_weekly = float(forecasts.loc[medicine["id"], "low"])
            last_expiry = max(expiry for _, _, expiry in batches)
            days = min(max((last_expiry - today).days + 1, 1), MAX_PROJECTION_DAYS)
            pattern = shares.get(medicine["id"], [1 / 7] * 7)
            usable = [b for b in batches if b[2] > today]
            expected = project_batches(usable, daily_demand(weekly, pattern, today, days), today)
            quiet = project_batches(usable, daily_demand(quiet_weekly, pattern, today, days), today)

        cumulative = 0.0
        for order, (code, quantity, expiry) in enumerate(sorted(batches, key=lambda b: (b[2], b[0])), 1):
            days_to_expiry = (expiry - today).days
            row = {
                "medicine_id": medicine["id"], "medicine": medicine["name"], "unit": medicine["unit"],
                "fefo_order": order, "batch_code": code, "quantity": quantity, "expiry_date": expiry,
                "days_to_expiry": days_to_expiry,
            }
            if days_to_expiry > 0:
                cumulative += quantity  # stock that must be used up to finish this batch (FEFO)
            if not has_forecast:
                rows.append({**row, "status": "Expired" if days_to_expiry <= 0 else "No forecast",
                             "explanation": "Expired: remove it with an adjustment." if days_to_expiry <= 0
                             else "No demand forecast, so use before expiry cannot be checked."})
                continue

            daily = weekly / 7
            days_needed = cumulative / daily if daily > 0 and days_to_expiry > 0 else None
            exp, qui = expected.get(code, {}), quiet.get(code, {})
            expected_unused = round(exp.get("expires_unused", 0.0), 1) if days_to_expiry > 0 else quantity
            quiet_unused = round(qui.get("expires_unused", 0.0), 1) if days_to_expiry > 0 else quantity
            status = status_for(days_to_expiry, expected_unused, quiet_unused)
            needed_text = "never at zero demand" if days_needed is None else f"about {days_needed:,.0f} days"
            explanation = {
                "Expired": f"Expired on {expiry:%d %b %Y}. Do not dispense; remove it with an adjustment (reason: “Expired stock removed”).",
                "At risk": (
                    f"At {daily:,.1f}/day, using this batch"
                    + (f" and the {order - 1} batch(es) ahead of it" if order > 1 else "")
                    + f" takes {needed_text}, "
                    f"but it expires in {days_to_expiry} days. About {expected_unused:,.0f} {medicine['unit']} may expire unused."
                ),
                "Watch": (
                    f"Used up in time at forecast demand, but in a quiet period about {quiet_unused:,.0f} "
                    f"{medicine['unit']} could expire unused (expires in {days_to_expiry} days)."
                ),
                "OK": f"Expected to be used up in {needed_text}, before it expires in {days_to_expiry} days.",
            }[status]
            rows.append({
                **row, "status": status, "daily_demand": daily, "days_needed": days_needed,
                "expected_used_up_day": exp.get("used_up_day"), "expected_unused": expected_unused,
                "quiet_unused": quiet_unused, "explanation": explanation,
            })

    table = pd.DataFrame(rows)
    if table.empty:
        return table
    table["status_rank"] = table["status"].map(STATUS_ORDER.index)
    return table.sort_values(["status_rank", "expiry_date", "medicine"]).drop(columns="status_rank").reset_index(drop=True)
