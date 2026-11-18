"""Reorder suggestions for SmartMed AI (Milestone 7).

PROTOTYPE SUGGESTIONS for a responsible staff member to review. They are
simple, explained arithmetic on the demand forecast, not orders and not advice.

    average daily demand = 7-day forecast ÷ 7
    safety stock         = average daily demand × safety days
    reorder point        = average daily demand × lead time + safety stock
    inventory position   = usable stock on hand
                           + open incoming orders (including overdue ones, which are still confirmed)
                           − outstanding commitments (not recorded yet, so 0)

    Reorder when inventory position ≤ reorder point.
    Suggested quantity   = reorder point + average daily demand × cover days − inventory position
    Reorder date         = today if already at the reorder point, otherwise the day the inventory
                           position is expected to fall to it at the average daily demand.

Priority:
    Urgent       the stockout check rates the medicine Critical: stock may run out before
                 a new order could arrive, so chasing an overdue order or emergency supply is needed
    Reorder now  inventory position is at or below the reorder point
    Soon         the reorder date is within SOON_DAYS days
    Not yet      the reorder date is later
    Unknown      no demand forecast

A medicine rated High by the stockout check, but not due for reordering, gets a note in its
action: a new order would not arrive sooner, but an earlier delivery of stock on order might help.
"""
from datetime import date, timedelta
import math

import pandas as pd

DEFAULT_SAFETY_DAYS = 7
DEFAULT_COVER_DAYS = 30
SOON_DAYS = 14
OUTSTANDING_COMMITMENTS = 0  # reserved or promised stock is not recorded yet
PRIORITY_ORDER = ["Urgent", "Reorder now", "Soon", "Not yet", "Unknown"]


def suggest_reorders(risk_table: pd.DataFrame, safety_days: int = DEFAULT_SAFETY_DAYS,
                     cover_days: int = DEFAULT_COVER_DAYS, today: date | None = None) -> pd.DataFrame:
    """One suggestion per medicine, built from the stockout risk table (stockout.assess_risk)."""
    today = today or date.today()
    rows = []
    for item in risk_table.to_dict("records"):
        base = {
            "medicine_id": item["medicine_id"], "medicine": item["medicine"], "unit": item["unit"],
            "risk": item["risk"], "lead_time": item["lead_time"], "lead_time_assumed": item["lead_time_assumed"],
            "usable_stock": item["usable_stock"],
            "on_order": item["on_order"] + item["overdue_on_order"], "overdue_on_order": item["overdue_on_order"],
        }
        weekly = item.get("forecast_week")
        if item["risk"] == "Unknown" or weekly is None or pd.isna(weekly):
            rows.append({**base, "priority": "Unknown", "action": "No demand history, so no suggestion. Review manually."})
            continue

        daily = weekly / 7
        safety_stock = daily * safety_days
        reorder_point = daily * item["lead_time"] + safety_stock
        position = item["usable_stock"] + base["on_order"] - OUTSTANDING_COMMITMENTS
        target = reorder_point + daily * cover_days

        if position <= reorder_point:
            reorder_date, days_until = today, 0
            quantity = math.ceil(target - position)
        else:
            days_until = math.floor((position - reorder_point) / daily) if daily > 0 else None
            reorder_date = today + timedelta(days=days_until) if days_until is not None else None
            quantity = math.ceil(daily * cover_days)  # position will equal the reorder point on that date

        if item["risk"] == "Critical":
            priority = "Urgent"
        elif days_until == 0:
            priority = "Reorder now"
        elif days_until is not None and days_until <= SOON_DAYS:
            priority = "Soon"
        else:
            priority = "Not yet"

        actions = []
        if priority == "Urgent":
            actions.append("Stock may run out before a new order could arrive.")
            if item["overdue_on_order"]:
                actions.append(f"Chase the overdue order ({item['overdue_on_order']:,.0f} {item['unit']}) with the supplier.")
            actions.append("Consider an emergency or faster supply route.")
        elif item["risk"] == "High" and days_until != 0:
            actions.append(
                "Stockout risk is High: in a busy period, stock could run out before stock on order arrives. "
                "Consider asking the supplier to deliver sooner."
            )
        if days_until == 0:
            actions.append(f"Order about {quantity:,} {item['unit']} now.")
        elif reorder_date is not None:
            actions.append(f"Expected to reach the reorder point around {reorder_date:%d %b %Y}; then order about {quantity:,}.")
        if item["lead_time_assumed"]:
            actions.append("Lead time not recorded (14 days assumed).")

        rows.append({
            **base, "priority": priority, "daily_demand": daily, "safety_days": safety_days,
            "safety_stock": safety_stock, "reorder_point": reorder_point, "position": position,
            "cover_days": cover_days, "target": target, "quantity": quantity,
            "reorder_date": reorder_date, "days_until_reorder": days_until, "action": " ".join(actions),
        })

    table = pd.DataFrame(rows)
    table["priority_rank"] = table["priority"].map(PRIORITY_ORDER.index)
    sort_columns = ["priority_rank", "days_until_reorder", "medicine"] if "days_until_reorder" in table else ["priority_rank", "medicine"]
    return table.sort_values(sort_columns, na_position="last").drop(columns="priority_rank").reset_index(drop=True)
