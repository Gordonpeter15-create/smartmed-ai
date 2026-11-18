# SmartMed AI — user and developer guide

How to install, run, test, evaluate, reset, and inspect the prototype, and what each screen is for.

## 1. Install and run

Requires Python 3.10 or newer. From the `smartmed_ai` folder:

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
streamlit run app.py
```

Streamlit prints a local address (usually `http://localhost:8501`). Open it in a browser. When the database is empty, the app fills it with **synthetic** sample data on startup. Stop the app with **Ctrl+C** in the terminal.

After editing a `.py` file **outside** `views/`, stop Streamlit and start it again. Streamlit only reloads page files by itself.

## 2. The screens

| Screen | Use it to | Main file |
|---|---|---|
| 📊 Dashboard | See stock totals, low-stock and expiry alerts, projected shortages, recent movements, and usage charts | `views/dashboard_page.py` |
| 💊 Medicines | Register, edit, deactivate, or (if unused) delete medicines; set reorder level and supplier lead time | `views/medicines_page.py` |
| 📦 Inventory | Receive, dispense, and adjust stock; record and cancel incoming orders; view batches and the transaction history | `views/inventory_page.py` |
| 📈 Forecasting | See 7-day demand forecasts and how each method performed on held-out data | `views/forecasting_page.py` |
| ⏳ Stockout risk | See how long stock is expected to last and each medicine's risk level, with the reason | `views/stockout_page.py` |
| 🛒 Reorder suggestions | See when and how much to reorder, with the calculation shown step by step | `views/reorder_page.py` |
| 🗓️ Batch expiry | See which batch to use first, and batches that may expire before they are used | `views/expiry_page.py` |
| ℹ️ About & limitations | Read what works, what is synthetic, what is not validated, and the limitations | `views/about_page.py` |

What each alert and suggestion means is explained in [METHODS.md](METHODS.md).

## 3. Everyday tasks

- **A delivery arrives:** Inventory → Receive stock. Enter the batch code, quantity, and expiry date. If it is for an open order, pick the order so it is marked received.
- **Stock is dispensed:** Inventory → Dispense. Batches are used earliest-expiry first. If some of the request could not be supplied, enter it under *Requested but not available*. This matters for forecasting.
- **A count is wrong, or stock is damaged or expired:** Inventory → Adjust, with a reason.
- **An order is placed with a supplier:** Inventory → Incoming orders → Record order.
- **Check what needs attention:** Dashboard → *Needs attention*, then the Stockout risk, Reorder suggestions, and Batch expiry pages.

Never enter patient names or identifiers in notes.

## 4. Automated checks

```bash
python -m unittest discover tests
```

The tests create a temporary database, so your data is not touched. They cover the inventory rules, dashboard queries, forecasting (including leakage checks), stockout risk, orders and reorder arithmetic, and batch expiry.

## 5. Re-run the evaluation

```bash
python evaluate.py
```

This writes [EVALUATION.md](EVALUATION.md) from the current database: data checks, the held-out comparison, a 4-period backtest, error per medicine, and a snapshot of the alerts.

## 6. Reset the demo data

Stop the app first, then:

```bash
python seed_data.py --reset
```

This **deletes** `data/smartmed.db`, including anything you entered in the app, and rebuilds the synthetic dataset. Options: `--seed 7` (different random numbers) and `--end-date 2026-06-30` (fix the last day of history; the default is yesterday). Without `--reset`, `python seed_data.py` only fills an empty database.

If the database was made by an older generator version, the dashboard shows a warning until you reset. Version 3 added the synthetic open orders; its daily history is identical to version 2.

Older databases are otherwise **upgraded automatically on startup without losing data**. Missing columns (`active`, `lead_time_days`), opening-balance transactions, and new tables are added.

## 7. Inspect the database

```bash
python inspect_db.py                          # row counts, data source, consistency check
python inspect_db.py medicines                # first 10 rows of a table
python inspect_db.py stock_transactions 25    # first 25 rows
```

The script only reads. You can also open `data/smartmed.db` with any SQLite viewer. Table contents are described in [DATA.md](DATA.md).

## 8. Project structure

```text
Browser → app.py (navigation) → views/*_page.py (screens)
             │                     ├─ reads  → database.py ─────────────┐
             │                     ├─ writes → inventory.py (stock rules) ┴→ SQLite: data/smartmed.db
             │                     └─ analysis → forecasting.py → stockout.py → reorder.py
             │                                                  └→ expiry.py
             └─ on startup: database.initialize_database() + seed_data.seed_database()
seed_data.py → synthetic_data.py (simulation)
```

| File | Purpose |
|---|---|
| `app.py` | Start here: page setup, database preparation, navigation |
| `database.py` | Table definitions, automatic upgrades, read queries |
| `inventory.py` | Rules for every stock change (validated, saved with a transaction) |
| `synthetic_data.py`, `seed_data.py` | Generate and save the synthetic dataset; reset |
| `forecasting.py` | Load → clean → features → evaluate (incl. backtest) → forecast |
| `stockout.py` | Day-by-day stock simulation and risk rules |
| `reorder.py` | Reorder point, inventory position, quantity, priority |
| `expiry.py` | Batch-by-batch use-before-expiry projection |
| `ui.py` | Shared page helpers and the cached forecast |
| `evaluate.py`, `inspect_db.py` | Evaluation report; read-only database inspection |
| `tests/` | Automated checks |
| `docs/` | This guide, data, methods, limitations, evaluation report |
