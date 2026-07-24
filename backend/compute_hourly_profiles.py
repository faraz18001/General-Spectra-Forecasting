import os
import glob
import pandas as pd
from datetime import datetime

from database import LocalSession, Branch, BranchHourlyProfile, get_or_create_branch

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.getenv("DATA_PATH", os.path.join(BASE_DIR, "..", "Data", "icp_data"))

def compute_profiles():
    """
    Computes per-branch hourly traffic profiles from raw parquet data.
    """
    print(f"Starting hourly profile computation at {datetime.now()}")
    
    if not os.path.exists(DATA_PATH):
        print(f"Data path {DATA_PATH} does not exist.")
        return

    # Find all parquet files
    search_pattern = os.path.join(DATA_PATH, "**", "*.parquet")
    files = glob.glob(search_pattern, recursive=True)
    
    if not files:
        print("No parquet files found.")
        return

    print(f"Found {len(files)} parquet files. Reading and combining...")
    
    dfs = []
    for f in files:
        try:
            # We only need Branch Name and Issue Time
            df = pd.read_parquet(f, columns=["Branch Name", "Issue Time"])
            dfs.append(df)
        except Exception as e:
            print(f"Error reading {f}: {e}")
            
    if not dfs:
        print("No valid data loaded.")
        return
        
    combined_df = pd.concat(dfs, ignore_index=True)
    print(f"Loaded {len(combined_df)} total records.")
    
    # Drop NA
    combined_df = combined_df.dropna(subset=["Branch Name", "Issue Time"])
    
    print("Parsing timestamps...")
    # Parse standard 12-hour AM/PM time
    combined_df['parsed_time'] = pd.to_datetime(combined_df['Issue Time'], format='%I:%M:%S %p', errors='coerce')
    combined_df = combined_df.dropna(subset=['parsed_time'])
    
    # Extract hour component
    combined_df['hour'] = combined_df['parsed_time'].dt.hour
    
    print("Calculating distributions per branch...")
    # Group by Branch and Hour
    hour_counts = combined_df.groupby(['Branch Name', 'hour']).size().reset_index(name='count')
    
    # Calculate normalized weights per branch
    branch_totals = hour_counts.groupby('Branch Name')['count'].sum().reset_index(name='total_count')
    hour_counts = hour_counts.merge(branch_totals, on='Branch Name')
    hour_counts['weight'] = hour_counts['count'] / hour_counts['total_count']
    
    db = LocalSession()
    try:
        print("Saving profiles to database...")
        # Clear existing profiles to avoid duplicates
        db.query(BranchHourlyProfile).delete()
        
        branch_ids = {}
        records = []
        
        for _, row in hour_counts.iterrows():
            b_name = str(row["Branch Name"]).strip()
            
            # Get DB IDs
            if b_name not in branch_ids:
                branch_ids[b_name] = get_or_create_branch(b_name)
            b_id = branch_ids[b_name]
            
            records.append(BranchHourlyProfile(
                branch_id=b_id,
                hour_of_day=int(row['hour']),
                weight=float(row['weight'])
            ))
            
        # Bulk insert
        db.bulk_save_objects(records)
        db.commit()
        print(f"Successfully saved {len(records)} hourly profile records for {len(branch_ids)} branches.")
        
    except Exception as e:
        db.rollback()
        print(f"Database error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    compute_profiles()
