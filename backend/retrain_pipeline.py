import os
from datetime import datetime
from database import LocalSession, Event
from model import Model


def main():
    """
    Main entry point for orchestrating the robust, automatic model retraining pipeline.
    
    Connects to the database to fetch active calendar events, defines a target training year
    horizon (e.g. rolling 4-year period: 2023, 2024, 2025, 2026), instantiates a fresh Model
    forecaster, and executes the full network-wide parallel training sequence for all branches 
    and categories, updating database predictions and cache accordingly.
    
    Args:
        None
        
    Returns:
        None
    """
    print(f"Starting retrain pipeline at {datetime.now()}")
    
    db = LocalSession()
    try:
        import json
        try:
            with open("events_data.json", "r") as f:
                events_list = json.load(f)
            print(f"Loaded {len(events_list)} events from events_data.json.")
        except Exception as e:
            print(f"Error loading events: {e}")
            events_list = []
        
        # Dynamic rolling window: check for parquet files recursively in DATA_PATH
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        DATA_PATH = os.getenv("DATA_PATH", os.path.join(BASE_DIR, "..", "Data"))
        
        import glob
        all_parquets = glob.glob(os.path.join(DATA_PATH, "**", "*.parquet"), recursive=True)
        if not all_parquets:
            print(f"No parquet data files found in {DATA_PATH}. Aborting.")
            return
            
        current_year = datetime.now().year
        years = [str(current_year - 2), str(current_year - 1), str(current_year)]
        print(f"Training on dataset in {DATA_PATH} ({len(all_parquets)} parquet files found)...")
        
        # Initialize forecasting model
        predictor = Model()
        
        # Train aggregate model and branch-specific models
        print("Initializing training sequence...")
        success = predictor.train_model(
            years=years,
            events_list=events_list,
            branch_name=None,
            prediction_days=365,
            confidence=0.95,
            category_name=None
        )
        
        if success:
            print("Successfully completed the robust retraining pipeline across all branches and categories.")
        else:
            print("Retraining pipeline encountered an error during execution.")
            
    except Exception as e:
        print(f"Fatal error in retraining pipeline: {e}")
    finally:
        db.close()
        print(f"Finished retrain pipeline at {datetime.now()}")


if __name__ == "__main__":
    main()
