# SmartMed AI — methods: what every number, alert, and suggestion means

All rules below are implemented in plain Python and can be read in the named file. Only the Random Forest
(Section 3) is a learned model, and it is **not** currently used because it did not clearly beat a simple
moving average. Everything else is transparent arithmetic or fixed rules, **not AI predictions**.

## 1. Stock records (`inventory.py`)

Stock is never typed over. Each change updates one batch **and** writes a `stock_transactions` row in a single
database transaction. If anything fails, neither is saved.

| Action | What happens | Checks |
|---|---|---|
| Receive stock | Creates the batch (or adds to the same batch code); records a `receipt`; optionally closes an open order | Quantity > 0; date not in the future; expiry after the receipt date; batch code not used by another medicine |
| Dispense | Uses batches earliest-expiry first (FEFO); one `dispense` per batch used; optional unmet quantity → `unmet_requests` | Cannot exceed usable (unexpired) stock; expired batches are never used |
| Adjust | Adds to or removes from one batch; records an `adjustment` | Reason required (plus a note for "Other"); a batch cannot go below zero |
| Record / cancel order | Adds an open `incoming_orders` row / closes it | Quantity > 0; expected arrival not before the order date; cancelling needs a reason |
| Edit medicine | Name, category, unit, reorder level, lead time | Names unique (ignoring capitals); unit locked once stock exists; lead time 1–365 days or empty |
| Deactivate | Hides the medicine from the dashboard and forms; keeps its history | Stock must be zero and no orders open |
| Delete | Removes a medicine permanently | Only if it has no records at all (for example, a typo) |

**Opening balance:** stock that existed before transactions were recorded (the synthetic starting stock) is explained by one `opening_balance` transaction per batch.

## 2. Dashboard (`views/dashboard_page.py`)

| Item | Meaning |
|---|---|
| Medicines tracked | Active medicines |
| Units in stock | Sum of all batch quantities. This adds different units together, so it is a rough size indicator only |
| At or below reorder level | Total stock ≤ the catalogue reorder level. A **fixed threshold**, ignoring stock on order. Not a forecast |
| Out of stock | Total stock is zero |
| Batches expiring ≤ N days | In-stock batches expiring within the chosen window (default 90 days) |
| Expired batches still in stock | Must be removed with an adjustment; never dispensed |
| Batches that may expire before use | From the batch expiry check (Section 6) |
| Projected shortages | Medicines rated Critical or High by the stockout check (Section 4) |
| Recent stock movements | Latest 10 transactions, by movement date |
| Usage history | Synthetic history and app-recorded dispensing, shown **separately** |

## 3. Demand forecast (`forecasting.py`)

- **Target:** total demand (dispensed + unmet) per medicine over the **next 7 days**.
- **Data preparation:** sort by date; remove duplicates and negative values; keep missing days **empty**, never zero. Any window touching a missing day is left out.
- **Features** for each forecast day, using only that day and earlier: demand in the last 7, 14, and 28 days; change from the previous week; stockout days in the last week. For the Random Forest they are divided by the medicine's usual week (8-week average), so one model can learn from all medicines.
- **Methods:** 7-day moving average (next week = last week), 28-day moving average (next week = average of the last 4 weeks), and Random Forest (scikit-learn, 200 trees, min. 5 samples per leaf).
- **Evaluation:** chronological. The last 8 weeks of forecast days are held out. The Random Forest trains only on forecast days whose 7-day outcome ended **before** that period, so no future information leaks in. A **backtest** repeats this on 4 consecutive 8-week periods.
- **Metrics:** **WAPE** = Σ|forecast − actual| ÷ Σ actual (main measure); **MAE** = average absolute error per medicine-week, in units; **Bias** = Σ(forecast − actual) ÷ Σ actual.
- **Choice:** the Random Forest is used only if it cuts WAPE by at least **5%** compared with the best moving average on the main held-out period; otherwise the simpler method is kept. Currently the **28-day moving average** is used. Results are in [EVALUATION.md](EVALUATION.md).
- **Likely range:** the middle 80% of how far actual demand landed from the chosen method's forecasts for that medicine in the held-out weeks. It is rough, being based on about 8 weeks.
- **XGBoost** was not added: with one synthetic year there is no evidence a second tree-based library would help.
- Forecasts are recomputed (and cached) when the history changes; they are not stored in the database.

## 4. Stockout risk (`stockout.py`)

Usable stock (unexpired batches) is simulated **day by day for 90 days**:
1. Open orders arrive on their expected date (assumed not to expire within 90 days). **Overdue orders are not assumed to arrive.**
2. Batches expiring that day are removed.
3. That day's demand is taken earliest-expiry first. Daily demand = the 7-day forecast spread over weekdays using the medicine's last 8 weeks; after 7 days it is **assumed to continue at the same weekly level**.
4. The first day demand cannot be met is the projected stockout day.

A **busy-period** scenario uses the top of the likely range. A **simple estimate** (usable stock ÷ forecast daily demand) is shown for comparison.

| Level | Rule (first match wins) |
|---|---|
| Critical | No usable stock, **or** expected to run out within the supplier lead time (a delivery ordered today would arrive too late) |
| High | Usable stock + stock on order ≤ reorder level, **or** the busy-period scenario runs out within the lead time |
| Medium | Expected to run out after the lead time but within the planning horizon (default 30 days) |
| Low | Expected stock lasts beyond the horizon |
| Unknown | No demand history |

A missing lead time is replaced by 14 days and flagged.

## 5. Reorder suggestions (`reorder.py`)

```text
average daily demand = 7-day forecast ÷ 7
safety stock         = average daily demand × safety days                (default 7)
reorder point        = average daily demand × lead time + safety stock
inventory position   = usable on hand + open orders (including overdue) − commitments (not recorded → 0)
order target         = reorder point + average daily demand × cover days  (default 30)
suggested quantity   = order target − inventory position (rounded up; on the reorder date if later)
reorder date         = today if position ≤ reorder point, else when the position is expected to reach it
```

| Priority | Meaning |
|---|---|
| Urgent | Stockout risk is Critical. A new order would arrive too late; chase any overdue order or consider a faster supply route |
| Reorder now | Inventory position ≤ reorder point |
| Soon | Reorder point expected within 14 days |
| Not yet | Later. If the stockout risk is High, the action suggests asking for earlier delivery of stock on order |
| Unknown | No forecast |

The forecast-based **reorder point** is different from the fixed **reorder level** in the catalogue; the reorder level is never changed automatically. Suggestions are **prototype suggestions for a responsible staff member to review**. They ignore budget, pack sizes, minimum order quantities, storage, and supplier reliability, and never place orders.

## 6. Batch expiry (`expiry.py`)

Each medicine's batches are used day by day, earliest-expiry first, at the forecast demand; each batch is followed separately. The page also shows the simple comparison:

```text
days needed to use = (this batch + batches ahead of it in FEFO order) ÷ forecast daily demand
compared with      = days until this batch expires
```

| Status | Meaning |
|---|---|
| Expired | In stock after its expiry date: do not dispense; adjust it out |
| At risk | Units expected to remain on the expiry date at forecast demand |
| Watch | Only in a quiet period (bottom of the likely range); low demand is what causes expiry |
| OK | Expected to be used up in time |
| No forecast | No demand history |

Assumptions: new deliveries expire later than current batches; long-range demand is very uncertain; no stock is moved between facilities. Expiry flags are stock-keeping prompts, **not clinical or regulatory determinations**. Units projected to expire unused are not subtracted from reorder suggestions.
