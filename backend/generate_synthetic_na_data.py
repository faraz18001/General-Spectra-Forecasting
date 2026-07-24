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
DB_FILE = os.path.join(BASE_DIR, "forecast_app.db")
EVENTS_FILE = os.path.join(BASE_DIR, "events_data.json")

# 1. Clean existing icp_data and db
if os.path.exists(DATA_DIR):
    shutil.rmtree(DATA_DIR)
os.makedirs(YEAR_2025_DIR, exist_ok=True)

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

# Months to generate for 2025: (Month Name, Month Number, Days)
MONTHS_2025 = [
    ("Jan", 1, 31),
    ("Feb", 2, 28),
    ("Mar", 3, 31),
    ("Apr", 4, 30),
]

ticket_id_counter = 100000

for mon_name, mon_num, days_in_mon in MONTHS_2025:
    print(f"Generating synthetic NA data for {mon_name}-2025 ({days_in_mon} days)...")
    rows = []
    
    for day in range(1, days_in_mon + 1):
        dt = datetime(2025, mon_num, day)
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
                issue_time_dt = datetime(2025, mon_num, day, hour, minute, second)
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
    out_file = os.path.join(YEAR_2025_DIR, f"DailyTicket_Log_{mon_name}-2025.parquet")
    df.to_parquet(out_file, index=False)
    print(f"  Saved {len(df)} records -> {out_file}")

print("\nSuccessfully generated all 4 months of 2025 North America synthetic data!")
