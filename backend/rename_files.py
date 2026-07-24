"""
Data File Renaming Utility.

This script scans raw Excel/CSV data files in the target data directory and 
standardizes their names to match the expected format of the ingestion pipeline.
Specifically, it changes loose month-year filenames (e.g. 'March 2026.xlsx') 
to standard capitalized 'Ticket Detail_MAR 2026.xlsx' filenames.

Inputs:
    - Base directory located at `../Data/icp_data/`
    - Mapping array `renames` containing (year_folder, old_filename, new_filename) tuples.

Outputs:
    - Renames target files physically on disk.
    - Prints confirmation of renamed or skipped files to stdout.
"""

import os
import shutil

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Data", "icp_data")

renames = [
    ("2025", "Nov 2025.xlsx",   "Ticket Detail_NOV 2025.xlsx"),
    ("2025", "Dec 2025.xlsx",   "Ticket Detail_DEC 2025.xlsx"),
    ("2026", "Jan 2026.xlsx",   "Ticket Detail_JAN 2026.xlsx"),
    ("2026", "Feb 2026.xlsx",   "Ticket Detail_FEB 2026.xlsx"),
    ("2026", "March 2026.xlsx", "Ticket Detail_MAR 2026.xlsx"),
]

for year_folder, old_name, new_name in renames:
    old_path = os.path.join(BASE, year_folder, old_name)
    new_path = os.path.join(BASE, year_folder, new_name)
    if os.path.exists(old_path):
        os.rename(old_path, new_path)
        print(f"  Renamed: {old_name} -> {new_name}")
    else:
        print(f"  SKIP (not found): {old_path}")

print("\nDone. Verify:")
for year in ["2025", "2026"]:
    folder = os.path.join(BASE, year)
    print(f"\n{year}/")
    for f in sorted(os.listdir(folder)):
        print(f"  {f}")

