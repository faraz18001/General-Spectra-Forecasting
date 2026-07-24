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
DATA_PATH = os.getenv("DATA_PATH", os.path.join(BASE_DIR, "..", "Data", "icp_data"))


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
        print(f"  Missing required columns. Skipping.")
        return 0

    # Clean data
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce").dt.normalize()
    df = df.dropna(subset=[date_col, "Branch Name", "Category Name"])
    row_count = len(df)
    print(f"  Rows loaded: {row_count}")

    # Build branch → region map from Region column
    branch_region_map = {}
    if "Region" in df.columns:
        region_df = df[["Branch Name", "Region"]].dropna().drop_duplicates()
        for _, rrow in region_df.iterrows():
            b_key = str(rrow["Branch Name"]).strip()
            reg_val = str(rrow["Region"]).strip()
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


def main():
    """
    Main entry point. Scans all DailyTicket_Log_*.parquet files in DATA_PATH,
    skips any already recorded in ingested_files table, and processes new ones.

    This is the unified ingestion pipeline — parquet is the single source of truth.
    New data workflow: drop xlsx → convert to parquet → run ingest.
    """
    print(f"Starting data ingestion pipeline at {datetime.now()}")

    # Ensure all DB tables exist (including ingested_files)
    init_db()

    if not os.path.exists(DATA_PATH):
        print(f"Data path {DATA_PATH} does not exist.")
        return

    # Scan all parquet files
    all_parquets = sorted(glob.glob(
        os.path.join(DATA_PATH, "**", "DailyTicket_Log_*.parquet"), recursive=True
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
