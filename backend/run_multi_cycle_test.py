import os
import shutil
import sqlite3
import subprocess
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICP_DATA_DIR = os.path.join(BASE_DIR, "..", "Data", "icp_data")
STAGING_DIR = os.path.join(BASE_DIR, "..", "Data", "all_parquets")
DB_FILE = os.path.join(BASE_DIR, "forecast_app.db")
PYTHON_BIN = os.path.join(BASE_DIR, "fore", "bin", "python")

# Definition of the 6 test cycles
CYCLES = [
    {
        "cycle": 1,
        "name": "Cycle 1: Short Dataset (4 Months - Jan to Apr 2025)",
        "files_2025": ["Jan-2025", "Feb-2025", "Mar-2025", "Apr-2025"],
        "files_2026": [],
        "holdout_month": ("2026-01-01", "2026-01-31", "Jan 2026"),
        "expected_seasonality": False
    },
    {
        "cycle": 2,
        "name": "Cycle 2: Full Year Dataset (12 Months - Jan to Dec 2025)",
        "files_2025": ["Jan-2025", "Feb-2025", "Mar-2025", "Apr-2025", "May-2025", "Jun-2025", "Jul-2025", "Aug-2025", "Sep-2025", "Oct-2025", "Nov-2025", "Dec-2025"],
        "files_2026": [],
        "holdout_month": ("2026-01-01", "2026-01-31", "Jan 2026"),
        "expected_seasonality": True
    },
    {
        "cycle": 3,
        "name": "Cycle 3: Incremental Month 1 (13 Months - 2025 + Jan 2026)",
        "files_2025": ["Jan-2025", "Feb-2025", "Mar-2025", "Apr-2025", "May-2025", "Jun-2025", "Jul-2025", "Aug-2025", "Sep-2025", "Oct-2025", "Nov-2025", "Dec-2025"],
        "files_2026": ["Jan-2026"],
        "holdout_month": ("2026-02-01", "2026-02-28", "Feb 2026"),
        "expected_seasonality": True
    },
    {
        "cycle": 4,
        "name": "Cycle 4: Incremental Month 2 (14 Months - 2025 + Jan-Feb 2026)",
        "files_2025": ["Jan-2025", "Feb-2025", "Mar-2025", "Apr-2025", "May-2025", "Jun-2025", "Jul-2025", "Aug-2025", "Sep-2025", "Oct-2025", "Nov-2025", "Dec-2025"],
        "files_2026": ["Jan-2026", "Feb-2026"],
        "holdout_month": ("2026-03-01", "2026-03-31", "Mar 2026"),
        "expected_seasonality": True
    },
    {
        "cycle": 5,
        "name": "Cycle 5: Incremental Month 3 (15 Months - 2025 + Jan-Mar 2026)",
        "files_2025": ["Jan-2025", "Feb-2025", "Mar-2025", "Apr-2025", "May-2025", "Jun-2025", "Jul-2025", "Aug-2025", "Sep-2025", "Oct-2025", "Nov-2025", "Dec-2025"],
        "files_2026": ["Jan-2026", "Feb-2026", "Mar-2026"],
        "holdout_month": ("2026-04-01", "2026-04-30", "Apr 2026"),
        "expected_seasonality": True
    },
    {
        "cycle": 6,
        "name": "Cycle 6: Incremental Month 4 (16 Months - 2025 + Jan-Apr 2026)",
        "files_2025": ["Jan-2025", "Feb-2025", "Mar-2025", "Apr-2025", "May-2025", "Jun-2025", "Jul-2025", "Aug-2025", "Sep-2025", "Oct-2025", "Nov-2025", "Dec-2025"],
        "files_2026": ["Jan-2026", "Feb-2026", "Mar-2026", "Apr-2026"],
        "holdout_month": ("2026-05-01", "2026-05-31", "May 2026"),
        "expected_seasonality": True
    }
]

def reset_icp_data_dir(c):
    if os.path.exists(ICP_DATA_DIR):
        shutil.rmtree(ICP_DATA_DIR)
    dir_2025 = os.path.join(ICP_DATA_DIR, "2025")
    dir_2026 = os.path.join(ICP_DATA_DIR, "2026")
    os.makedirs(dir_2025, exist_ok=True)
    os.makedirs(dir_2026, exist_ok=True)

    # Copy specified 2025 files
    for f in c["files_2025"]:
        src = os.path.join(STAGING_DIR, "2025", f"DailyTicket_Log_{f}.parquet")
        dst = os.path.join(dir_2025, f"DailyTicket_Log_{f}.parquet")
        shutil.copy(src, dst)

    # Copy specified 2026 files
    for f in c["files_2026"]:
        src = os.path.join(STAGING_DIR, "2026", f"DailyTicket_Log_{f}.parquet")
        dst = os.path.join(dir_2026, f"DailyTicket_Log_{f}.parquet")
        shutil.copy(src, dst)

def run_cmd(cmd):
    res = subprocess.run(f"{PYTHON_BIN} {cmd}", shell=True, cwd=BASE_DIR, capture_output=True, text=True)
    return res

results_summary = []

for c in CYCLES:
    print(f"\n=======================================================")
    print(f"  RUNNING {c['name']}")
    print(f"=======================================================")
    
    # 1. Reset DB and setup icp_data files
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    reset_icp_data_dir(c)
    
    # 2. Run Ingestion
    print("Running ingest_pipeline.py...")
    res_ingest = run_cmd("ingest_pipeline.py")
    if res_ingest.returncode != 0:
        print("  Ingest Error:", res_ingest.stderr)
        
    # 3. Run Profiles
    print("Running compute_hourly_profiles.py...")
    run_cmd("compute_hourly_profiles.py")
    
    # 4. Run Retrain
    print("Running retrain_pipeline.py...")
    res_retrain = run_cmd("retrain_pipeline.py")
    if res_retrain.returncode != 0:
        print("  Retrain Error:", res_retrain.stderr)

    # 5. Evaluate results from DB & Holdout Parquet
    conn = sqlite3.connect(DB_FILE)
    
    # Date span check
    df_act = pd.read_sql_query("SELECT MIN(date) as min_d, MAX(date) as max_d, COUNT(DISTINCT date) as dist_days FROM actual_traffic", conn)
    min_d = df_act["min_d"].values[0]
    max_d = df_act["max_d"].values[0]
    dist_days = df_act["dist_days"].values[0]
    
    min_dt = pd.to_datetime(min_d)
    max_dt = pd.to_datetime(max_d)
    span_days = (max_dt - min_dt).days
    use_yearly_seasonality = span_days >= 360
    
    # Latest training run ID
    df_run = pd.read_sql_query("SELECT id, status, years_used FROM training_runs ORDER BY id DESC LIMIT 1", conn)
    run_id = df_run["id"].values[0] if not df_run.empty else None
    
    # Check predictions quality
    df_preds = pd.read_sql_query("SELECT date, predicted FROM daily_forecasts WHERE training_run_id = ? AND category_id = 0", conn, params=(run_id,))
    zero_count = len(df_preds[df_preds["predicted"] <= 0])
    
    # Evaluate Holdout Month accuracy using Staging Parquet
    h_start, h_end, h_label = c["holdout_month"]
    
    # Load holdout actuals from staging parquet files for the holdout month
    h_mon_str = pd.to_datetime(h_start).strftime("%b-%Y")
    h_parquet = os.path.join(STAGING_DIR, "2026", f"DailyTicket_Log_{h_mon_str}.parquet")
    
    wmape, acc, r2 = None, None, None
    if os.path.exists(h_parquet):
        df_h_raw = pd.read_parquet(h_parquet)
        df_h_raw["date"] = pd.to_datetime(df_h_raw["Ticket Issue Date"]).dt.strftime("%Y-%m-%d")
        df_h_act = df_h_raw.groupby(["Branch Name", "date"]).size().reset_index(name="actual")
        df_h_act.rename(columns={"Branch Name": "branch_name"}, inplace=True)
        
        # Query forecasts from DB for the holdout period (joining latest training run per branch)
        df_h_fcst = pd.read_sql_query("""
            SELECT b.name as branch_name, f.date, f.predicted 
            FROM daily_forecasts f
            JOIN branches b ON f.branch_id = b.id
            WHERE f.category_id = 0 
              AND b.name != 'ALL' 
              AND f.date >= ? AND f.date <= ?
              AND f.training_run_id IN (SELECT MAX(id) FROM training_runs GROUP BY branch_id)
        """, conn, params=(h_start, h_end))
        
        merged = pd.merge(df_h_act, df_h_fcst, on=["branch_name", "date"], how="inner")
        if not merged.empty:
            acts = merged["actual"].values
            preds = merged["predicted"].values
            wmape = (np.sum(np.abs(acts - preds)) / np.sum(acts)) * 100
            acc = max(0, 100 - wmape)
            
            ss_res = np.sum((acts - preds) ** 2)
            ss_tot = np.sum((acts - np.mean(acts)) ** 2)
            r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0
            
    conn.close()
    
    res_info = {
        "Cycle": f"Cycle {c['cycle']}",
        "Training Span": f"{span_days} days ({dist_days} active)",
        "Yearly Seasonality": "Enabled (True)" if use_yearly_seasonality else "Auto-Disabled (False)",
        "Zero Predictions": zero_count,
        "Holdout Evaluated": h_label,
        "Holdout Accuracy": f"{acc:.2f}%" if acc is not None else "N/A",
        "Holdout WMAPE": f"{wmape:.2f}%" if wmape is not None else "N/A",
        "R2 Score": f"{r2:.4f}" if r2 is not None else "N/A"
    }
    results_summary.append(res_info)
    
    print(f"  --> Span: {span_days} days | Seasonality: {res_info['Yearly Seasonality']}")
    print(f"  --> Zero Preds: {zero_count} | Holdout [{h_label}] Accuracy: {res_info['Holdout Accuracy']} (R2: {res_info['R2 Score']})")

print("\n=======================================================")
print("  FINAL MULTI-CYCLE EVALUATION SUMMARY")
print("=======================================================")
df_summary = pd.DataFrame(results_summary)
print(df_summary.to_string(index=False))

# Save summary to csv for report generation
df_summary.to_csv(os.path.join(BASE_DIR, "multi_cycle_results.csv"), index=False)
