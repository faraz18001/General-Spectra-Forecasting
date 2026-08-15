import os
import shutil
import random
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

# Path setup
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "Data", "icp_data")
YEAR_2025_DIR = os.path.join(DATA_DIR, "2025")
YEAR_2026_DIR = os.path.join(DATA_DIR, "2026")
DB_FILE = os.path.join(BASE_DIR, "forecast_app.db")
EVENTS_FILE = os.path.join(BASE_DIR, "events_data.json")

# Clean existing icp_data and db
if os.path.exists(DATA_DIR):
    shutil.rmtree(DATA_DIR)
os.makedirs(YEAR_2025_DIR, exist_ok=True)
os.makedirs(YEAR_2026_DIR, exist_ok=True)

if os.path.exists(DB_FILE):
    os.remove(DB_FILE)
    print("Removed forecast_app.db")

with open(EVENTS_FILE, "w") as f:
    f.write("[]")
print("Set events_data.json to []")

# North America Branch & Region Hierarchy
BRANCH_CONFIG = [
    {"branch": "Toronto Main", "region": "Ontario", "base_volume": 350},
    {"branch": "Mississauga Center", "region": "Ontario", "base_volume": 250},
    {"branch": "Ottawa Hub", "region": "Ontario", "base_volume": 200},
    {"branch": "Vancouver West", "region": "British Columbia", "base_volume": 300},
    {"branch": "Victoria Regional", "region": "British Columbia", "base_volume": 180},
    {"branch": "Calgary Central", "region": "Alberta", "base_volume": 220},
    {"branch": "Edmonton North", "region": "Alberta", "base_volume": 190},
]

CATEGORIES = [
    "Driver Licensing",
    "Vehicle Registration",
    "Health Card Services",
    "Identity Services",
    "General Inquiry",
]

OPERATORS = ["Op_JohnD", "Op_SarahM", "Op_AlexP", "Op_EmilyR", "Op_MichaelT", "Op_LisaK"]

# Datasets to generate: (Year, Directory, Month Name, Month Number, Days)
SCHEDULE = [
    # Full 12 Months of 2025 (365 days) for Training
    (2025, YEAR_2025_DIR, "Jan", 1, 31),
    (2025, YEAR_2025_DIR, "Feb", 2, 28),
    (2025, YEAR_2025_DIR, "Mar", 3, 31),
    (2025, YEAR_2025_DIR, "Apr", 4, 30),
    (2025, YEAR_2025_DIR, "May", 5, 31),
    (2025, YEAR_2025_DIR, "Jun", 6, 30),
    (2025, YEAR_2025_DIR, "Jul", 7, 31),
    (2025, YEAR_2025_DIR, "Aug", 8, 31),
    (2025, YEAR_2025_DIR, "Sep", 9, 30),
    (2025, YEAR_2025_DIR, "Oct", 10, 31),
    (2025, YEAR_2025_DIR, "Nov", 11, 30),
    (2025, YEAR_2025_DIR, "Dec", 12, 31),
    # Full 12 Months of 2026 Actuals / Holdout
    (2026, YEAR_2026_DIR, "Jan", 1, 31),
    (2026, YEAR_2026_DIR, "Feb", 2, 28),
    (2026, YEAR_2026_DIR, "Mar", 3, 31),
    (2026, YEAR_2026_DIR, "Apr", 4, 30),
    (2026, YEAR_2026_DIR, "May", 5, 31),
    (2026, YEAR_2026_DIR, "Jun", 6, 30),
    (2026, YEAR_2026_DIR, "Jul", 7, 31),
    (2026, YEAR_2026_DIR, "Aug", 8, 31),
    (2026, YEAR_2026_DIR, "Sep", 9, 30),
    (2026, YEAR_2026_DIR, "Oct", 10, 31),
    (2026, YEAR_2026_DIR, "Nov", 11, 30),
    (2026, YEAR_2026_DIR, "Dec", 12, 31),
]

ticket_id_counter = 100000

for yr, out_dir, mon_name, mon_num, days_in_mon in SCHEDULE:
    print(f"Generating synthetic NA data for {mon_name}-{yr} ({days_in_mon} days)...")
    rows = []
    
    for day in range(1, days_in_mon + 1):
        dt = datetime(yr, mon_num, day)
        is_weekend = dt.weekday() >= 5  # 5=Sat, 6=Sun
        
        # Day multiplier: weekends have lower volume
        day_mult = 0.2 if is_weekend else random.uniform(0.85, 1.15)
        
        for b_info in BRANCH_CONFIG:
            b_name = b_info["branch"]
            r_name = b_info["region"]
            daily_count = int(b_info["base_volume"] * day_mult)
            
            for _ in range(daily_count):
                ticket_id_counter += 1
                cat = random.choice(CATEGORIES)
                op = random.choice(OPERATORS)
                
                # Operating hours 08:00 to 17:00
                hour = random.randint(8, 16)
                minute = random.randint(0, 59)
                second = random.randint(0, 59)
                issue_time_dt = datetime(yr, mon_num, day, hour, minute, second)
                issue_time_str = issue_time_dt.strftime("%I:%M:%S %p")
                
                wait_mins = random.randint(2, 35)
                serv_mins = random.randint(3, 20)
                call_time_dt = issue_time_dt + timedelta(minutes=wait_mins)
                close_time_dt = call_time_dt + timedelta(minutes=serv_mins)
                
                rows.append({
                    "Region": r_name,
                    "Branch Name": b_name,
                    "Ticket Issue Date": dt.strftime("%Y-%m-%d 00:00:00"),
                    "Category Name": cat,
                    "Ticket Id": ticket_id_counter,
                    "Ticket Number": f"T-{random.randint(100, 999)}",
                    "Counter ID": float(random.randint(1, 12)),
                    "Operator Name": op,
                    "Issue Time": issue_time_str,
                    "Call Time": call_time_dt.strftime("%I:%M:%S %p"),
                    "WaitTime": f"00:{wait_mins:02d}:00",
                    "First Call Wait Time": float(wait_mins * 60),
                    "Local Wait Time": float(wait_mins * 60),
                    "No Show Wait Time": 0.0,
                    "No Show Service Time": 0.0,
                    "Service Time": f"00:{serv_mins:02d}:00",
                    "CloseTime": close_time_dt.strftime("%I:%M:%S %p"),
                    "Total Customer Time": f"00:{wait_mins + serv_mins:02d}:00",
                    "Status": "Closed",
                    "Customer Information 1": np.nan,
                    "Customer Information 2": np.nan,
                    "Customer Information 3": np.nan,
                    "Customer Information 4": np.nan,
                    "Customer Information 5": np.nan,
                    "Customer Information 6": np.nan,
                    "Customer Information 7": np.nan,
                    "Customer Information 8": np.nan,
                    "Customer Information 9": np.nan,
                    "Customer Information 10": np.nan,
                    "Type of Call": np.nan,
                    "Ticket Origin": "Kiosk",
                    "CancelType": np.nan,
                })
                
    df = pd.DataFrame(rows)
    out_file = os.path.join(out_dir, f"DailyTicket_Log_{mon_name}-{yr}.parquet")
    df.to_parquet(out_file, index=False)
    print(f"  Saved {len(df)} records -> {out_file}")

print("\nSuccessfully generated 2025 training data AND 2026 actuals data!")
