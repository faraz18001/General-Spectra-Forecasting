import os
import glob
import pandas as pd
from datetime import datetime
from database import (
    LocalSession,
    IngestedFile,
    get_or_create_branch,
    get_or_create_category,
    save_actual_traffic,
    init_db,
)

# Use absolute path or standard repo path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "..", "Data")


def is_already_ingested(filename):
    """Check if a parquet file has already been ingested into the database."""
    db = LocalSession()
    try:
        return db.query(IngestedFile).filter(IngestedFile.filename == filename).first() is not None
    finally:
        db.close()


def mark_as_ingested(filename, row_count):
    """Record a parquet file as ingested to prevent duplicate processing."""
    db = LocalSession()
    try:
        record = IngestedFile(filename=filename, row_count=row_count)
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

    Also extracts and stores the Region for each branch.

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
    row_count = len(df)
    print(f"  Rows loaded: {row_count}")

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
    print(f"  Calculating daily actuals...")
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

        print(f"  Successfully inserted actuals into database.")

    except Exception as e:
        print(f"  Error inserting actual data: {e}")
        return 0
    finally:
        db.close()

    return row_count


def convert_csv_to_parquet(csv_path):
    """
    Auto-converts an incoming CSV file (.csv) to .parquet in the same directory.
    Skips conversion if the target .parquet file already exists and is newer.

    Args:
        csv_path (str): Absolute path to the CSV file.

    Returns:
        str: Absolute path to the generated/existing parquet file, or None on error.
    """
    base, _ = os.path.splitext(csv_path)
    parquet_path = base + ".parquet"

    # If parquet already exists and is mtime-up-to-date, reuse it
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
    Skips conversion if the target .parquet file already exists and is newer.

    Args:
        excel_path (str): Absolute path to the Excel file.

    Returns:
        str: Absolute path to the generated/existing parquet file, or None on error.
    """
    base, _ = os.path.splitext(excel_path)
    parquet_path = base + ".parquet"

    # If parquet already exists and is mtime-up-to-date, reuse it
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
    2. Scans all .parquet files, checks ingested_files table, and processes new ones.
    
    Supports CSV drops, Excel drops, and direct Parquet drops seamlessly.
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
        if not is_already_ingested(basename):
            files_to_process.append(fp)

    print(f"Found {len(all_parquets)} total parquets. {len(files_to_process)} new to ingest.")

    for fp in files_to_process:
        row_count = process_parquet(fp)
        if row_count > 0:
            mark_as_ingested(os.path.basename(fp), row_count)

    print(f"\nFinished data ingestion pipeline at {datetime.now()}")


if __name__ == "__main__":
    main()
