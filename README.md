# SmartMed AI — prototype

A beginner-friendly web prototype for **medicine inventory management and demand forecasting**, aimed at clinics, pharmacies, health centres, and hospitals, especially in Cameroon. Built with Python, Streamlit, SQLite, pandas, and scikit-learn.

> **Educational prototype.** All included records are **synthetic** (invented). It is not clinically validated, not ready for operational use, and not a substitute for professional judgment. Every forecast, risk level, and suggestion is a prompt for a responsible staff member to review.

## What it does

| | |
|---|---|
| 💊 **Medicines** | Catalogue with reorder level and supplier lead time; deactivate instead of deleting |
| 📦 **Inventory** | Receipts, dispensing (First Expiry, First Out), adjustments with reasons, incoming orders. Every change is a transaction, so stock can always be explained |
| 📊 **Dashboard** | Stock totals, low-stock and expiry alerts, projected shortages, recent movements, usage charts |
| 📈 **Forecasting** | 7-day demand forecast; moving averages vs. Random Forest compared on held-out periods |
| ⏳ **Stockout risk** | Day-by-day stock projection with Critical / High / Medium / Low levels and reasons |
| 🛒 **Reorder suggestions** | Reorder point, inventory position, quantity, date, and priority, with the arithmetic shown |
| 🗓️ **Batch expiry** | Use order per medicine, and batches that may expire before they are used |

Current evaluation on synthetic data: the 28-day moving average (about 13.5% WAPE averaged over 4 held-out periods) is used, because the Random Forest was not clearly better. See [docs/EVALUATION.md](docs/EVALUATION.md).

## Quick start

```bash
cd work/smartmed_ai
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
streamlit run app.py
```

Open the address Streamlit prints (usually `http://localhost:8501`). Synthetic sample data is created on first run.

```bash
python -m unittest discover tests   # automated checks (temporary database)
python evaluate.py                  # regenerate docs/EVALUATION.md
python inspect_db.py                # look inside the database (read-only)
python seed_data.py --reset         # DELETE the local database and rebuild the sample data
```

## Documentation

| Document | Contents |
|---|---|
| [docs/GUIDE.md](docs/GUIDE.md) | Install, run, screens, everyday tasks, tests, evaluation, reset, inspecting the database, project structure |
| [docs/DATA.md](docs/DATA.md) | What data is used, database tables, how the synthetic data is generated, guidance for real data |
| [docs/METHODS.md](docs/METHODS.md) | What every number, alert, and suggestion means, and how forecasting is evaluated |
| [docs/EVALUATION.md](docs/EVALUATION.md) | Generated evaluation report (data checks, held-out results, backtest, per-medicine errors) |
| [docs/LIMITATIONS.md](docs/LIMITATIONS.md) | Status of each feature, when not to trust the outputs, possible future research |

The same status and limitations are shown in the app on the **About** page.

## Architecture

```text
Browser → Streamlit pages (views/) → Python logic (inventory, forecasting, stockout, reorder, expiry) → SQLite (data/smartmed.db)
```

Simple by design: no web API, cloud services, or deep learning. A larger architecture would only be considered if real use required it.
