# SmartMed AI — evaluation report

> **Generated** 2026-11-18 15:37 by `python evaluate.py`.
> **Data:** SYNTHETIC — generator version 3, seed 2026,
> 18 Nov 2025 to 17 Nov 2026. These results show that the pipeline works on
> invented data. **They are not evidence of accuracy in any real facility.**

## 1. Data checks

| Check | Result |
|---|---|
| Active medicines | 25 |
| Daily records | 9,125 |
| Missing days (kept empty, not treated as zero) | 0 |
| Duplicate / negative rows removed | 0 / 0 |
| Stockout days (their unmet demand is included in demand) | 95 |
| Batches whose quantity does not match their transactions | 0 |

## 2. Forecast target and methods

- **Target:** total demand (dispensed + unmet) per medicine over the next 7 days.
- **Methods:** Moving average (7 days), Moving average (28 days), Random Forest.
- **Metrics:** WAPE = total absolute error ÷ total actual demand (main measure; lower is better). MAE = average
  absolute error per medicine-week, in units. Bias = total over-forecast (+) or under-forecast (−) ÷ total actual demand.

## 3. Main held-out comparison (used by the app to choose a method)

Forecast days 16 Sep 2026 – 10 Nov 2026 (1,400 forecasts).
Random Forest trained on 6,025 examples whose outcomes ended before that period.

| Method | WAPE | MAE (units/week) | Bias | Used |
|---|---|---|---|---|
| Moving average (7 days) | 16.4% | 29.2 | +0.0% |  |
| Moving average (28 days) | 15.2% | 27.1 | +1.3% | ✅ |
| Random Forest | 14.9% | 26.4 | +2.7% |  |

**Choice:** Random Forest was only 2% better than Moving average (28 days) on the held-out weeks (at least 5% better was required), so the simpler, more transparent method is kept.

## 4. Backtest over 4 consecutive periods

The same comparison repeated on earlier 8-week blocks, each with methods trained only on data from before it.

| Period | Held-out forecast days | Training rows | Moving average (7 days) | Moving average (28 days) | Random Forest | Best |
|---|---|---|---|---|---|---|
| 1 | 16 Sep 2026 – 10 Nov 2026 | 6,025 | 16.4% | 15.2% | 14.9% | Random Forest |
| 2 | 22 Jul 2026 – 15 Sep 2026 | 4,625 | 14.5% | 12.9% | 12.4% | Random Forest |
| 3 | 27 May 2026 – 21 Jul 2026 | 3,225 | 15.3% | 12.4% | 12.9% | Moving average (28 days) |
| 4 | 01 Apr 2026 – 26 May 2026 | 1,825 | 16.8% | 13.6% | 14.2% | Moving average (28 days) |

| Method | Average WAPE | Average bias | Periods won |
|---|---|---|---|
| Moving average (7 days) | 15.7% | -0.5% | 0 |
| Moving average (28 days) | 13.5% | -1.1% | 2 |
| Random Forest | 13.6% | -1.0% | 2 |

**Reading this:** Random Forest was best in 2 of 4 periods. Averaged over all periods, the best
method was Moving average (28 days) and the runner-up Random Forest, 0.04 percentage points of WAPE apart.
A gap this size, which also changes direction between periods, is not evidence that one of them is reliably better.
The 7-day moving average was the worst in every period.

## 5. Error by medicine (main held-out period, WAPE, hardest first)

| Medicine | Moving average (7 days) | Moving average (28 days) | Random Forest |
|---|---|---|---|
| Malaria rapid diagnostic test | 40.1% | 36.7% | 32.8% |
| Amoxicillin 250 mg/5 mL suspension | 20.5% | 26.7% | 25.1% |
| Ceftriaxone 1 g injection | 32.3% | 25.4% | 25.9% |
| Sodium chloride 0.9% 500 mL | 24.1% | 24.1% | 26.5% |
| Salbutamol 100 mcg inhaler | 28.5% | 23.9% | 25.8% |
| Artemether-lumefantrine 20/120 mg | 23.7% | 22.8% | 23.0% |
| Zinc 20 mg | 18.3% | 19.4% | 19.3% |
| Paracetamol 500 mg | 18.1% | 19.2% | 13.5% |
| Hydrochlorothiazide 25 mg | 25.0% | 18.8% | 22.2% |
| Quinine 300 mg | 16.3% | 18.7% | 16.9% |
| Artesunate 60 mg injection | 21.0% | 18.4% | 29.5% |
| Gentamicin 80 mg injection | 21.3% | 16.9% | 21.7% |
| Mebendazole 500 mg | 19.4% | 16.5% | 15.5% |
| Oxytocin 10 IU injection | 15.8% | 14.9% | 21.9% |
| Metformin 500 mg | 12.8% | 14.2% | 12.7% |
| Amlodipine 5 mg | 14.4% | 13.6% | 9.6% |
| Amoxicillin 500 mg | 17.6% | 12.5% | 13.4% |
| Diclofenac 50 mg | 10.3% | 12.1% | 13.6% |
| Ferrous sulfate + folic acid | 13.5% | 10.7% | 12.3% |
| Metronidazole 250 mg | 11.5% | 10.6% | 12.1% |
| Oral rehydration salts | 15.0% | 10.5% | 16.0% |
| Ibuprofen 400 mg | 10.3% | 10.4% | 10.6% |
| Ciprofloxacin 500 mg | 9.8% | 8.4% | 8.4% |
| Omeprazole 20 mg | 7.1% | 6.2% | 8.8% |
| Co-trimoxazole 480 mg | 8.7% | 5.7% | 7.4% |

Seasonal items (such as malaria tests) and low-volume items tend to be the hardest to forecast; check the top rows above.

## 6. Snapshot of alerts on 18 Nov 2026 (not evaluated against outcomes)

These rule-based outputs use the forecast above. They have **not** been checked against what happened afterwards,
because no future data exists for them yet.

| Output | Counts |
|---|---|
| Stockout risk (30-day horizon) | Critical 4, High 1, Medium 0, Low 20, Unknown 0 |
| Reorder priority | Urgent 4, Reorder now 0, Soon 6, Not yet 15, Unknown 0 |
| Batch expiry status | Expired 0, At risk 0, Watch 0, OK 34, No forecast 0 |

## 7. What this evaluation cannot tell you

- How accurate forecasts would be with **real facility records** (none have been used).
- Whether stockout, reorder, or expiry alerts would have prevented real shortages or waste.
- How the methods behave over a **full seasonal cycle** in held-out data: only one synthetic year exists.
- How results change when unmet demand is **not** recorded, as is common in real records.

See `docs/LIMITATIONS.md` for when not to trust these outputs.
