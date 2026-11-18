# SmartMed AI — status, limitations, and future directions

SmartMed AI is an **educational prototype**. It is **not clinically validated**, **not ready for operational use**, and **not a substitute** for professional inventory management or clinical judgment. It does not guarantee medicine availability.

## Status at a glance

| Area | Status |
|---|---|
| Medicine catalogue, stock receipts, dispensing, adjustments, transaction history, consistency check | ✅ **Working** (tested in the app and by automated tests) |
| Incoming orders (record, cancel, receive against an order) | ✅ **Working** |
| Dashboard summaries and threshold alerts | ✅ **Working**: values come straight from database records |
| Sample data | 🧪 **Synthetic**: invented by `synthetic_data.py`; every chart and result built on it is illustrative |
| 7-day demand forecast and its evaluation | 🧪 **Working on synthetic data only**: no real facility data has been evaluated |
| Stockout risk, reorder suggestions, batch expiry flags | 🧪 **Working on synthetic data**: rule-based outputs that have **not** been checked against real outcomes |
| Results evaluated against real, authorized operational data | ❌ **None yet** |
| Transfers between facilities, user accounts, supplier records, commitments, partial deliveries | ⏳ **Not built** |
| Seasonal analysis, unusual-usage detection, supplier-delay analysis, multi-facility patterns, redistribution | 💡 **Possible future research only** (see below) |

## When not to trust the outputs

- **Always, for real decisions, until validated.** Forecast accuracy (about 13–16% WAPE) was measured on **invented** data. Real demand is messier and may behave very differently.
- **When unmet demand is not recorded.** Stockouts then look like low demand, and forecasts, stockout risk, and reorder quantities will all be **too low** for exactly the medicines that ran out.
- **Far ahead.** The forecast is for 7 days. The stockout check (up to 90 days), reorder date, and expiry check (up to years) assume demand stays at that weekly level. The further ahead, the less reliable.
- **Around seasonal changes and sudden events.** One year of history shows the malaria season only once. Outbreaks, campaigns, new services, or supply disruptions cannot be predicted from past demand.
- **For seasonal or low-volume medicines.** With the method in use, errors were largest for malaria tests (37% WAPE) and for low-volume items such as the amoxicillin suspension, ceftriaxone injection, IV fluid, and inhalers (24–27%).
- **When stock records are incomplete.** Unrecorded dispensing, receipts, or expired-stock removals make every downstream number wrong. Check the consistency message on the Inventory page.
- **When lead times or orders are missing or out of date.** Risk levels and reorder dates depend on them. Overdue orders are not assumed to arrive in the stockout check, but they *are* counted in reorder suggestions.

## Specific limitations

- **Forecasting:** one synthetic year; 8-week held-out periods; likely ranges based on about 8 weeks per medicine; the forecast ignores dispensing recorded in the app; no holiday, outbreak, or price effects.
- **Model choice:** Random Forest and the 28-day moving average were essentially tied over 4 periods (13.6% vs 13.5% average WAPE), so the simpler method is used. No claim is made that the Random Forest is "AI that works better".
- **Stockout and reorder:** demand assumed constant beyond 7 days; incoming orders assumed to arrive in full on the expected date; commitments not recorded (counted as 0); no pack sizes, budgets, minimum order quantities, storage limits, or supplier reliability.
- **Expiry:** assumes new deliveries expire later than current stock; does not subtract projected waste from reorder quantities. With the default synthetic data no batch is at risk, because orders are small.
- **Inventory:** a single facility; no user accounts or audit of *who* made a change; partial deliveries close the order; no transfers.
- **Alerts have not been evaluated** against what actually happened. A simulation-based evaluation, or real data, would be needed to measure missed or false alarms.

## Possible future research directions (not requirements)

These are ideas to explore only once real, authorized data and expert input are available:

- **Seasonal-demand analysis** with several years of facility data (for example, malaria-related seasonality by region).
- **Unusual consumption detection:** flagging sudden rises or falls that may signal outbreaks, data-entry errors, or leakage.
- **Supplier-delay analysis:** learning real lead times and late-delivery rates per supplier.
- **Anonymized multi-facility patterns:** comparing demand across facilities without patient-level data.
- **Redistribution suggestions** when one facility has excess or short-dated stock and another has a shortage. These would need to account for expiry, storage and cold chain, transport, facility approval, and applicable regulations, and must **never be automatic actions**.
- **Evaluating alerts** against outcomes (missed stockouts, false alarms, avoided waste).
- **Richer models** (for example XGBoost or hierarchical models) only if real data shows a clear, evaluated benefit over simple baselines.

## Responsible use

- Use only data the facility has authorized; do not use patient-level data.
- Treat every forecast, risk level, reorder suggestion, and expiry flag as a **prompt for review by a responsible staff member**.
- Keep synthetic and real results clearly separated, and re-run `python evaluate.py` before drawing any conclusion from new data.
