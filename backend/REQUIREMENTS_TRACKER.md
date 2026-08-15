# Traffic Forecasting Module - Backend Requirements Tracker

![System Design](../SystemDEsign.png)

Last Updated: 2026-02-28

## Progress: ~35% Complete (7/29 items)

---

## Core Short-Term Forecasting

| # | Item | Status |
|---|---|---|
| 1.1 | Prophet-based daily forecasts | DONE |
| 1.2 | Branch-level predictions | DONE |
| 1.3 | Category-level predictions | DONE |
| 1.4 | Day-of-week / holiday effects | DONE |
| 1.5 | Special events integration (Ramadan, Eid) | DONE |
| 1.6 | Weather conditions as regressor | NOT DONE |
| 1.7 | Other external regressors | NOT DONE |

---

## New Branch Handling

| # | Item | Status |
|---|---|---|
| 2.1 | Bootstrap from similar branches | NOT DONE |
| 2.2 | Regional averages as fallback | NOT DONE |

---

## Retraining Automation

| # | Item | Status |
|---|---|---|
| 3.1 | Automated weekly retraining | NOT DONE |
| 3.2 | Daily data ingestion pipeline | NOT DONE |

---

## Evaluation and Monitoring

| # | Item | Status |
|---|---|---|
| 4.1 | MAPE / RMSE calculation | PARTIAL |
| 4.2 | Directional accuracy | NOT DONE |
| 4.3 | Drift detection | NOT DONE |
| 4.4 | Automated recalibration | NOT DONE |
| 4.5 | Model versioning + rollback | NOT DONE |
| 4.6 | Threshold-based alerts | NOT DONE |

---

## Long-Term Forecasting

| # | Item | Status |
|---|---|---|
| 5.1 | Monthly predictions (12 months) | DONE |
| 5.2 | Macroeconomic indicators (GDP, immigration) | NOT DONE |
| 5.3 | Operator/staffing forecasting | NOT DONE |
| 5.4 | Hierarchical model (national + branch) | NOT DONE |

---

## Database and Integration

| # | Item | Status |
|---|---|---|
| 6.1 | Design DB schema (branches, categories, forecasts, etc.) | NOT DONE |
| 6.2 | Create tables (SQLAlchemy / raw SQL) | NOT DONE |
| 6.3 | Write predictions to DB after training | NOT DONE |
| 6.4 | Write weekly patterns to DB after training | NOT DONE |
| 6.5 | Training runs audit table | NOT DONE |
| 6.6 | Migrate events from JSON file to DB | NOT DONE |
| 6.7 | Serving API reads from DB instead of pkl | NOT DONE |
| 6.8 | Eliminate pkl file dependency | NOT DONE |
