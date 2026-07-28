import os
import glob
import pandas as pd
from datetime import datetime
from database import (
    LocalSession,
    IngestedFile,
    ActualTraffic,
    get_or_create_branch,
    get_or_create_category,
    save_actual_traffic,
    init_db,
)
from sqlalchemy import func

# Use absolute path or standard repo path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "..", "Data")


def is_already_ingested(filename, file_mtime=None):
    """
    Check if a file has already been ingested into the database.
    If file_mtime is provided, checks whether the file on disk has been updated
    since its last ingestion. Returns False if the file is new OR has been modified/appended.
    """
    db = LocalSession()
    try:
        record = db.query(IngestedFile).filter(IngestedFile.filename == filename).first()
        if not record:
            return False
        if file_mtime is not None and record.file_mtime is not None:
            # Return True (already ingested) ONLY if mtime matches
            return record.file_mtime == file_mtime
        return True
    finally:
        db.close()


def mark_as_ingested(filename, row_count, file_mtime=None):
    """
    Record or update a file's ingestion status, row count, and modification timestamp.
    """
    db = LocalSession()
    try:
        record = db.query(IngestedFile).filter(IngestedFile.filename == filename).first()
        if record:
            record.row_count = row_count
            if file_mtime is not None:
                record.file_mtime = file_mtime
            record.ingested_at = datetime.utcnow()
        else:
            record = IngestedFile(filename=filename, row_count=row_count, file_mtime=file_mtime)
            db.add(record)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"  Warning: Could not mark file as ingested: {e}")
    finally:
        db.close()


def process_parquet(file_path):
    """
    Reads a single DailyTicket_Log parquet file and inserts daily actual
    traffic counts into the database per branch, category, and globally.

    Filters out already-ingested dates so that appending new days into an existing
    file (e.g. Daily Ticket Log July 2026.csv) safely ingests only the new days.

    Args:
        file_path (str): Absolute path to the parquet file.

    Returns:
        int: Number of rows processed, or 0 on failure.
    """
    print(f"\nProcessing: {file_path}")

    try:
        df = pd.read_parquet(file_path)
    except Exception as e:
        print(f"  Error reading parquet: {e}")
        return 0

    # Resolve date column
    date_col = "Ticket Issue Date" if "Ticket Issue Date" in df.columns else "Issue Date"
    required_cols = {date_col, "Branch Name", "Category Name"}
    if not required_cols.issubset(set(df.columns)):
        print(f"  Missing required columns ({required_cols - set(df.columns)}). Skipping.")
        return 0

    # Clean data
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce").dt.normalize()
    df = df.dropna(subset=[date_col, "Branch Name", "Category Name"])
    raw_row_count = len(df)
    print(f"  Total file rows loaded: {raw_row_count}")

    # Incremental Filtering: Filter out dates already ingested into actual_traffic
    db_check = LocalSession()
    try:
        max_db_date = db_check.query(func.max(ActualTraffic.date)).scalar()
        if max_db_date is not None:
            df = df[df[date_col].dt.date > max_db_date]
            print(f"  Incremental Filter: Kept {len(df)} new rows (newer than last ingested date {max_db_date})")
    except Exception as e:
        print(f"  Warning during incremental date filter check: {e}")
    finally:
        db_check.close()

    if df.empty:
        print("  No new daily records to ingest (all dates in file are already in database). Skipping.")
        return raw_row_count

    # Build branch → region map from Region column
    branch_region_map = {}
    region_col = "Region Name" if "Region Name" in df.columns else ("Region" if "Region" in df.columns else None)
    if region_col:
        region_df = df[["Branch Name", region_col]].dropna().drop_duplicates()
        for _, rrow in region_df.iterrows():
            b_key = str(rrow["Branch Name"]).strip()
            reg_val = str(rrow[region_col]).strip()
            if b_key and reg_val:
                branch_region_map[b_key] = reg_val

    # Compute daily actuals per branch + category
    print(f"  Calculating daily actuals for new dates...")
    daily_counts = df.groupby(["Branch Name", "Category Name", date_col]).size().reset_index(name="actual_count")

    db = LocalSession()
    try:
        branch_ids = {}
        category_ids = {}

        # Per branch + category actuals
        for (branch_name, category_name), group in daily_counts.groupby(["Branch Name", "Category Name"]):
            b_name = str(branch_name).strip()
            c_name = str(category_name).strip()

            if b_name not in branch_ids:
                branch_ids[b_name] = get_or_create_branch(b_name, region=branch_region_map.get(b_name))
            b_id = branch_ids[b_name]

            cat_key = (b_id, c_name)
            if cat_key not in category_ids:
                category_ids[cat_key] = get_or_create_category(c_name, b_id)
            c_id = category_ids[cat_key]

            traffic_rows = [
                {"date": row[date_col].date(), "actual_count": int(row["actual_count"])}
                for _, row in group.iterrows()
            ]
            save_actual_traffic(b_id, c_id, traffic_rows)

        # Aggregate actuals per branch (all categories summed)
        branch_grouped = daily_counts.groupby(["Branch Name", date_col])["actual_count"].sum().reset_index()
        for branch_name, group in branch_grouped.groupby("Branch Name"):
            b_name = str(branch_name).strip()
            if b_name not in branch_ids:
                branch_ids[b_name] = get_or_create_branch(b_name, region=branch_region_map.get(b_name))
            b_id = branch_ids[b_name]

            branch_rows = [
                {"date": row[date_col].date(), "actual_count": int(row["actual_count"])}
                for _, row in group.iterrows()
            ]
            save_actual_traffic(b_id, 0, branch_rows)

        # Global aggregate (ALL branches)
        all_branch_id = get_or_create_branch("ALL")
        all_grouped = daily_counts.groupby(date_col)["actual_count"].sum().reset_index()
        all_rows = [
            {"date": row[date_col].date(), "actual_count": int(row["actual_count"])}
            for _, row in all_grouped.iterrows()
        ]
        save_actual_traffic(all_branch_id, 0, all_rows)

        print(f"  Successfully inserted new actuals into database.")

    except Exception as e:
        print(f"  Error inserting actual data: {e}")
        return 0
    finally:
        db.close()

    return raw_row_count


def convert_csv_to_parquet(csv_path):
    """
    Auto-converts an incoming CSV file (.csv) to .parquet in the same directory.
    Updates target .parquet file if the CSV has a newer modification time.

    Args:
        csv_path (str): Absolute path to the CSV file.

    Returns:
        str: Absolute path to the generated/existing parquet file, or None on error.
    """
    base, _ = os.path.splitext(csv_path)
    parquet_path = base + ".parquet"

    # If parquet exists and is up-to-date with CSV mtime, reuse it
    if os.path.exists(parquet_path):
        if os.path.getmtime(parquet_path) >= os.path.getmtime(csv_path):
            return parquet_path

    print(f"\nAuto-converting CSV to Parquet: {csv_path} -> {parquet_path}")
    try:
        df = pd.read_csv(csv_path)
        df.to_parquet(parquet_path, index=False)
        print(f"  Successfully converted {len(df)} rows to Parquet.")
        return parquet_path
    except Exception as e:
        print(f"  Error converting CSV to Parquet ({csv_path}): {e}")
        return None


def convert_excel_to_parquet(excel_path):
    """
    Auto-converts an incoming Excel file (.xlsx, .xls) to .parquet in the same directory.
    Updates target .parquet file if the Excel file has a newer modification time.

    Args:
        excel_path (str): Absolute path to the Excel file.

    Returns:
        str: Absolute path to the generated/existing parquet file, or None on error.
    """
    base, _ = os.path.splitext(excel_path)
    parquet_path = base + ".parquet"

    if os.path.exists(parquet_path):
        if os.path.getmtime(parquet_path) >= os.path.getmtime(excel_path):
            return parquet_path

    print(f"\nAuto-converting Excel to Parquet: {excel_path} -> {parquet_path}")
    try:
        df = pd.read_excel(excel_path)
        df.to_parquet(parquet_path, index=False)
        print(f"  Successfully converted {len(df)} rows to Parquet.")
        return parquet_path
    except Exception as e:
        print(f"  Error converting Excel to Parquet ({excel_path}): {e}")
        return None


def main():
    """
    Main entry point. 
    1. Scans all CSV files (.csv) and Excel files (.xlsx, .xls) in DATA_PATH and auto-converts them to .parquet.
    2. Scans all .parquet files, checks ingested_files table & modification timestamps (mtime), and processes new/updated files.
    
    Supports appended CSV drops, Excel drops, and direct Parquet drops seamlessly.
    """
    print(f"Starting data ingestion pipeline at {datetime.now()}")

    # Ensure all DB tables exist (including ingested_files)
    init_db()

    if not os.path.exists(DATA_PATH):
        print(f"Data path {DATA_PATH} does not exist.")
        return

    # Step 1a: Auto-convert any CSV files (.csv) to Parquet
    csv_files = sorted(glob.glob(
        os.path.join(DATA_PATH, "**", "*.csv"), recursive=True
    ))

    if csv_files:
        print(f"Found {len(csv_files)} CSV file(s). Checking for conversion...")
        for cf in csv_files:
            convert_csv_to_parquet(cf)

    # Step 1b: Auto-convert any Excel files (.xlsx, .xls) to Parquet
    excel_files = sorted(glob.glob(
        os.path.join(DATA_PATH, "**", "*.xlsx"), recursive=True
    ) + glob.glob(
        os.path.join(DATA_PATH, "**", "*.xls"), recursive=True
    ))

    if excel_files:
        print(f"Found {len(excel_files)} Excel file(s). Checking for conversion...")
        for ef in excel_files:
            convert_excel_to_parquet(ef)

    # Step 2: Scan all parquet files
    all_parquets = sorted(glob.glob(
        os.path.join(DATA_PATH, "**", "*.parquet"), recursive=True
    ))

    files_to_process = []
    for fp in all_parquets:
        basename = os.path.basename(fp)
        file_mtime = os.path.getmtime(fp)
        if not is_already_ingested(basename, file_mtime):
            files_to_process.append((fp, file_mtime))

    print(f"Found {len(all_parquets)} total parquets. {len(files_to_process)} new or updated to ingest.")

    for fp, mtime in files_to_process:
        row_count = process_parquet(fp)
        if row_count > 0:
            mark_as_ingested(os.path.basename(fp), row_count, mtime)

    print(f"\nFinished data ingestion pipeline at {datetime.now()}")


if __name__ == "__main__":
    main()
