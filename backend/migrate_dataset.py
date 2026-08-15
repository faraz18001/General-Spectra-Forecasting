import os
import glob
import re
import pandas as pd
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.getenv("DATA_PATH", os.path.join(BASE_DIR, "..", "Data", "icp_data"))

# Target 32-column schema matching DailyTicket_Log_Jul-2026.csv
TARGET_COLUMNS = [
    "Region", "Branch Name", "Ticket Issue Date", "Category Name", "Ticket Id",
    "Ticket Number", "Counter ID", "Operator Name", "Issue Time", "Call Time",
    "WaitTime", "First Call Wait Time", "Local Wait Time", "No Show Wait Time",
    "No Show Service Time", "Service Time", "CloseTime", "Total Customer Time",
    "Status", "Customer Information 1", "Customer Information 2", "Customer Information 3",
    "Customer Information 4", "Customer Information 5", "Customer Information 6",
    "Customer Information 7", "Customer Information 8", "Customer Information 9",
    "Customer Information 10", "Type of Call", "Ticket Origin", "CancelType"
]

COLUMN_MAPPING = {
    "Issue Date": "Ticket Issue Date",
    "Region Name": "Region",
    "State Name": "Status",
    "Wait Time": "WaitTime",
    "Close Time": "CloseTime",
    "Counter Id": "Counter ID",
}

MONTH_MAP = {
    "JAN": "Jan", "FEB": "Feb", "MAR": "Mar", "APRIL": "Apr", "APR": "Apr",
    "MAY": "May", "JUNE": "Jun", "JUN": "Jun", "JULY": "Jul", "JUL": "Jul",
    "AUG": "Aug", "SEP": "Sep", "OCT": "Oct", "NOV": "Nov", "DEC": "Dec"
}

def convert_filename(filename):
    """
    Converts 'Ticket Detail_FEB 2026.parquet' -> 'DailyTicket_Log_Feb-2026.parquet'
    """
    ext = os.path.splitext(filename)[1]
    name = os.path.splitext(filename)[0]
    
    # Match pattern like 'Ticket Detail_MAR 2026' or 'Ticket Detail_APRIL 2024'
    match = re.search(r"Ticket Detail_([A-Za-z]+)\s*(\d{4})", name, re.IGNORECASE)
    if match:
        raw_month = match.group(1).upper()
        year = match.group(2)
        norm_month = MONTH_MAP.get(raw_month, raw_month.capitalize())
        return f"DailyTicket_Log_{norm_month}-{year}{ext}"
    else:
        return f"DailyTicket_Log_{name}{ext}"

def migrate_file(file_path):
    print(f"\nMigrating: {file_path}")
    dir_name, filename = os.path.split(file_path)
    new_filename = convert_filename(filename)
    new_file_path = os.path.join(dir_name, new_filename)

    try:
        if file_path.endswith('.parquet'):
            df = pd.read_parquet(file_path)
        elif file_path.endswith('.xlsx'):
            df = pd.read_excel(file_path)
        elif file_path.endswith('.csv'):
            df = pd.read_csv(file_path)
        else:
            return
    except Exception as e:
        print(f"  Error reading {file_path}: {e}")
        return

    # 1. Rename existing columns according to mapping
    rename_dict = {}
    for col in df.columns:
        if col in COLUMN_MAPPING:
            rename_dict[col] = COLUMN_MAPPING[col]
    df = df.rename(columns=rename_dict)

    # 2. Format 'Ticket Issue Date' if present
    if "Ticket Issue Date" in df.columns:
        try:
            dates = pd.to_datetime(df["Ticket Issue Date"], errors="coerce")
            df["Ticket Issue Date"] = dates.dt.strftime("%d-%b-%Y")
        except Exception as e:
            print(f"  Warning formatting dates: {e}")

    # 3. Ensure all 32 target columns exist
    for col in TARGET_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    # 4. Reorder to match exact 32-column target schema
    df = df[TARGET_COLUMNS]

    # 5. Save new file
    try:
        if new_file_path.endswith('.parquet'):
            df.to_parquet(new_file_path, index=False)
        elif new_file_path.endswith('.xlsx'):
            df.to_excel(new_file_path, index=False)
        elif new_file_path.endswith('.csv'):
            df.to_csv(new_file_path, index=False)
        print(f"  Saved migrated file: {new_file_path}")

        # Remove old file if name changed
        if file_path != new_file_path and os.path.exists(file_path):
            os.remove(file_path)
            print(f"  Removed old file: {file_path}")
    except Exception as e:
        print(f"  Error saving migrated file: {e}")

def main():
    print(f"Starting dataset schema migration in {DATA_PATH}...")
    if not os.path.exists(DATA_PATH):
        print(f"Data path {DATA_PATH} does not exist.")
        return

    search_pattern = os.path.join(DATA_PATH, "**", "Ticket Detail_*.*")
    files = glob.glob(search_pattern, recursive=True)
    
    print(f"Found {len(files)} files to migrate.")
    for f in files:
        migrate_file(f)

    print("\nDataset schema migration completed successfully!")

if __name__ == "__main__":
    main()
