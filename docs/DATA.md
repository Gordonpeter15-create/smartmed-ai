# SmartMed AI — data

## 1. What data the prototype uses

| Data | Source | Used for |
|---|---|---|
| Medicine catalogue, batches, opening stock, open orders | **Synthetic** (invented by `synthetic_data.py`) | Every screen |
| Daily dispensing, unmet demand, stockout days for one year | **Synthetic** (`daily_consumption`) | Charts, forecasting, and everything built on the forecast |
| Receipts, dispensing, adjustments, orders, unmet requests entered in the app | **Typed in by the user** | Stock levels, history, charts ("Recorded in the app") |

No real facility records, public health statistics, or patient data are used. **Forecasts use only the synthetic daily history.** Dispensing entered in the app is shown separately and is not yet fed into the forecast.

## 2. Database tables

| Table | What it holds |
|---|---|
| `medicines` | Name, category, unit, reorder level, supplier lead time (days; may be empty), active flag |
| `batches` | Batch code, quantity, expiry date |
| `stock_transactions` | Every stock movement: opening balance, receipt, dispense, or adjustment, with date, signed change, reason, note, and time recorded |
| `unmet_requests` | Quantities requested but not available (from the Dispense form) |
| `incoming_orders` | Orders placed with suppliers: quantity, order date, expected arrival, status (open / received / cancelled) |
| `daily_consumption` | **Simulated** history: one row per medicine per day, with units dispensed, unmet demand, and a stockout flag |
| `dataset_info` | Where the data came from: `source = synthetic`, generator version, seed, date range, creation time |

For every batch, its quantity equals the sum of its transactions. `python inspect_db.py` and the Inventory page's Transaction history both check this.

## 3. How the synthetic data is generated

`synthetic_data.py` simulates **25 medicines × 365 days** (9,125 daily records), ending yesterday. For each medicine and each day:

1. **Deliveries** ordered earlier arrive and become new batches with an expiry date. About 10% are short-dated (3–8 months of shelf life).
2. **Expired batches** are removed from usable stock.
3. **Demand** is a random whole number (Poisson) around
   `base weekday demand × weekday factor × seasonal factor × spike factor`:
   - **Weekday factors:** Mon 1.20, Tue 1.05, Wed 1.00, Thu 1.00, Fri 1.05, Sat 0.65, Sun 0.35.
   - **Illustrative malaria season:** a smooth yearly curve peaking in mid-August. It is strong (up to ±50%) for antimalarials and malaria tests, weaker for paracetamol, ORS, zinc, and IV fluids, and absent for most other medicines. **This is a simplification, not an epidemiological model of Cameroon.**
   - **Spikes:** about 1% of days start a 2–4 day spike at 1.8–3× demand.
4. **Dispensing** uses First Expiry, First Out. A shortfall is saved as **unmet demand**, and the day is flagged as a stockout.
5. **Reordering:** when stock plus stock on order falls to the reorder level (average weekday demand × (lead time + 10 days)), about 45 days of cover is ordered. It arrives after the lead time (14–28 days, ±2 days). Deliveries are late (5–21 extra days) with a chance of 10–30% depending on the medicine.
6. At the end, the **orders still on the way** are saved as open incoming orders, with expected arrival = order date + usual lead time. Late ones therefore appear as overdue.

With the default seed (2026): 11 of 25 medicines have at least one stockout (95 stockout days, about 1% of medicine-days), and 11 orders are open (3 overdue).

**Repeatability:** the same seed and end date always give exactly the same data. Each medicine has its own random stream.

**What synthetic data cannot do:** it shows that the pipeline works end to end. It cannot show how accurate forecasts or alerts would be in a real facility, because the patterns were chosen by the developer.

## 4. Why unmet demand is recorded

During a stockout, dispensing falls even though patients still need the medicine. A model that saw only dispensing would learn that demand drops. The prototype therefore forecasts **demand = dispensed + unmet**. In real use, this needs records of requests that could not be served, or at least of stockout periods. Without them, forecasts will be too low exactly where shortages happened.

## 5. If real data is considered later

- Use only data the facility has **authorized** for this project.
- Use **facility-level dispensing records** (by medicine and day), not patient records. **Do not** collect patient names, identifiers, or unnecessary personal data.
- Record **unmet requests or stockout periods** where feasible.
- General public health statistics can give context, but they are **not a substitute** for facility consumption records when validating forecasts.
- Re-run `python evaluate.py` on the real history, and review the results with responsible staff before any operational use.
