from datetime import datetime, timedelta
import pandas as pd
import warnings
import time
from prophet import Prophet, models
import pickle
import os
import glob
from events import Event

# Suppress noisy pandas dateutil warnings
warnings.filterwarnings("ignore", message="Could not infer format")
from database import (
    get_or_create_branch,
    get_or_create_category,
    create_training_run,
    complete_training_run,
    save_forecasts_to_db,
    save_actual_traffic,
    save_cv_metrics,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "..", "Data")


class Model:
    def __init__(self):
        # In-memory cache for fast API responses during the server's lifetime.
        # The same data is also persisted to the database for durability across restarts.
        # Both are intentionally maintained: dicts for speed, DB for persistence.
        # Key: sanitized branch_name (or 'ALL' for aggregate)
        self.models = {}
        self.forecasts = {}
        self.daily_counts_map = {}
        self.spain_holidays = None

    def get_safe_branch_name(self, branch_name):
        """
        Sanitizes a branch name to be safe for filesystem and dictionary lookups.
        
        Replaces grouped branch pipe separators with '__OR__' and filters out other
        filesystem-unsafe characters. Returns 'ALL' if branch_name is empty/None.
        
        Args:
            branch_name (str or None): The raw branch name or None.
            
        Returns:
            str: The sanitized branch name or "ALL".
        """
        if not branch_name:
            return "ALL"
        # Pipe = grouped branch separator — replace with __OR__ to keep key readable
        # Then sanitize remaining filesystem-unsafe chars
        safe = branch_name.replace("|", "__OR__")
        safe = (
            safe.replace("/", "_")
            .replace("\\", "_")
            .replace(":", "")
            .replace('"', "")
            .replace("*", "")
            .replace("?", "")
            .replace("<", "")
            .replace(">", "")
        )
        return safe.strip()

    def get_safe_key(self, branch_name=None, category_name=None):
        """
        Generate a composite key for branch + optional category.
        
        Combines the sanitized branch name and category name using a special
        demarcator ('__CAT__').
        
        Args:
            branch_name (str or None): The raw branch name. Defaults to None.
            category_name (str or None): The raw category name. Defaults to None.
            
        Returns:
            str: The composite unique string key.
        """
        branch_key = self.get_safe_branch_name(branch_name)
        if category_name:
            cat_safe = self.get_safe_branch_name(category_name)  # Reuse sanitizer
            return f"{branch_key}__CAT__{cat_safe}"
        return branch_key

    def get_model(self, branch_name=None, category_name=None):
        """
        Retrieves a trained Prophet model from the models cache by branch/category key.
        
        If a category-level model doesn't exist, falls back to the branch-level model.
        
        Args:
            branch_name (str or None): The raw branch name. Defaults to None.
            category_name (str or None): The raw category name. Defaults to None.
            
        Returns:
            prophet.forecaster.Prophet or None: The Prophet model instance if found, else None.
        """
        key = self.get_safe_key(branch_name, category_name)
        result = self.models.get(key)
        if result is None and category_name:
            # Fall back to branch-level model
            result = self.models.get(self.get_safe_key(branch_name))
        return result

    def get_forecast(self, branch_name=None, category_name=None):
        """
        Retrieves a generated forecast DataFrame from the forecasts cache by branch/category key.
        
        If a category-level forecast doesn't exist, falls back to the branch-level forecast.
        
        Args:
            branch_name (str or None): The raw branch name. Defaults to None.
            category_name (str or None): The raw category name. Defaults to None.
            
        Returns:
            pandas.DataFrame or None: The forecast DataFrame containing future predictions, or None if not found.
                
                DataFrame Columns and Types:
                    - ds (datetime64[ns]): The predicted date.
                    - trend (float64): Trend component.
                    - yhat_lower (float64): Lower bound of prediction interval.
                    - yhat_upper (float64): Upper bound of prediction interval.
                    - trend_lower (float64): Lower bound of the trend component.
                    - trend_upper (float64): Upper bound of the trend component.
                    - additive_terms (float64): Sum of additive seasonal components.
                    - multiplicative_terms (float64): Sum of multiplicative seasonal components.
                    - yhat (float64): Predicted value.
                    - is_ramadan (float64): Multiplicative Ramadan regressor impact value.
                    - is_payday (float64): Multiplicative payday regressor impact value.
                    - (and additional Prophet columns if holiday features are generated)
        """
        key = self.get_safe_key(branch_name, category_name)
        result = self.forecasts.get(key)
        if result is None and category_name:
            # Fall back to branch-level forecast
            result = self.forecasts.get(self.get_safe_key(branch_name))
        return result

    def get_daily_counts(self, branch_name=None, category_name=None):
        """
        Retrieves preprocessed historical daily ticket counts from the cache by branch/category key.
        
        If category-level daily counts don't exist, falls back to the branch-level daily counts.
        
        Args:
            branch_name (str or None): The raw branch name. Defaults to None.
            category_name (str or None): The raw category name. Defaults to None.
            
        Returns:
            pandas.DataFrame or None: Preprocessed daily ticket counts, or None if not found.
                
                DataFrame Columns and Types:
                    - ds (datetime64[ns]): Date of the records.
                    - y (int64/float64): Daily ticket count.
        """
        key = self.get_safe_key(branch_name, category_name)
        result = self.daily_counts_map.get(key)
        if result is None and category_name:
            # Fall back to branch-level counts
            result = self.daily_counts_map.get(self.get_safe_key(branch_name))
        return result

    # Load and aggregate daily actuals from Parquet data
    def load_parquet_data(self, file_path, branch_name=None, category_name=None):
        """
        Load Parquet file and return daily ticket counts, optionally filtered by branch and/or category.
        
        Reads columns 'Issue Date' and 'Branch Name' (and optionally 'Category Name' if provided),
        performs filtering, formats/normalizes date strings, and aggregates transaction volume by day.
        branch_name can be a pipe-separated string like 'A|B|C' to combine multiple branches.
        
        Args:
            file_path (str): Absolute or relative path to the parquet file to load.
            branch_name (str or None): Name of branch(es) to filter by. Supports pipe separated groups. Defaults to None.
            category_name (str or None): Name of category to filter by. Defaults to None.
            
        Returns:
            pandas.DataFrame or None: DataFrame of daily ticket counts, or None if reading/filtering fails.
                
                DataFrame Columns and Types:
                    - ds (datetime64[ns]): Date of records.
                    - y (int64): Ticket count for that day.
        """
        date_col = "Ticket Issue Date"
        # We need to know which date column exists in the file, or read headers first
        try:
            # First check schema columns by reading 1 row
            sample = pd.read_parquet(file_path)
            actual_date_col = "Ticket Issue Date" if "Ticket Issue Date" in sample.columns else "Issue Date"
            
            columns = [actual_date_col, "Branch Name"]
            if category_name:
                columns.append("Category Name")

            data = pd.read_parquet(file_path, columns=columns)

            if branch_name and branch_name != "ALL":
                if "Branch Name" not in data.columns:
                    return None
                # Support grouped branches: split on pipe and use isin()
                branch_list = [b.strip() for b in branch_name.split("|")]
                if len(branch_list) > 1:
                    data = data[data["Branch Name"].isin(branch_list)]
                else:
                    data = data[data["Branch Name"] == branch_name]

            if category_name:
                if "Category Name" not in data.columns:
                    return None
                data = data[data["Category Name"] == category_name]

            if actual_date_col not in data.columns:
                return None

            data[actual_date_col] = pd.to_datetime(
                data[actual_date_col], errors="coerce"
            ).dt.normalize()
            data = data.dropna(subset=[actual_date_col])

            daily_counts = data.groupby(actual_date_col).size().reset_index()
            daily_counts.columns = ["ds", "y"]

            return daily_counts

        except Exception as e:
            # print(f"Error reading {file_path}: {e}")
            return None

    def get_all_branches(self, years):
        """
        Scan data files across DATA_PATH to find all unique branch names.
        """
        branches = set()

        print("Scanning for branches...")
        all_parquet_files = sorted(glob.glob(os.path.join(DATA_PATH, "**", "*.parquet"), recursive=True))
        for file_path in all_parquet_files:
            try:
                df = pd.read_parquet(file_path, columns=["Branch Name"])
                branches.update(df["Branch Name"].unique())
            except:
                pass

        return sorted(list(branches))

    def get_all_categories(self, years, branch_name=None, top_n=3):
        """
        Get top N categories by ticket volume across DATA_PATH, optionally filtered by branch.
        """
        from collections import Counter

        category_counts = Counter()

        # Support grouped branches: split on pipe
        branch_list = None
        if branch_name:
            branch_list = [b.strip() for b in branch_name.split("|")]

        all_parquet_files = sorted(glob.glob(os.path.join(DATA_PATH, "**", "*.parquet"), recursive=True))
        for file_path in all_parquet_files:
            try:
                cols = ["Category Name"]
                if branch_list:
                    cols.append("Branch Name")
                df = pd.read_parquet(file_path, columns=cols)
                if branch_list:
                    if len(branch_list) > 1:
                        df = df[df["Branch Name"].isin(branch_list)]
                    else:
                        df = df[df["Branch Name"] == branch_list[0]]
                for cat, count in df["Category Name"].value_counts().items():
                    if isinstance(cat, str):
                        category_counts[cat] += count
            except:
                pass

        # Return top N categories by ticket volume
        top_categories = [cat for cat, _ in category_counts.most_common(top_n)]
        print(f"   Top {top_n} categories: {top_categories}")
        return top_categories

    def generate_model_feedable_data(self, years, events_list, branch_name=None, category_name=None):
        """
        Load and aggregate daily actuals across all available parquet files for a branch and optional category.
        Scans all parquet files recursively inside DATA_PATH (supporting both flat and nested folder structures).
        """
        all_data = []

        # Find all parquet files recursively in DATA_PATH
        all_parquet_files = sorted(glob.glob(os.path.join(DATA_PATH, "**", "*.parquet"), recursive=True))

        for file_path in all_parquet_files:
            b_filter = branch_name if branch_name != "ALL" else None
            month_data = self.load_parquet_data(
                file_path, b_filter, category_name
            )
            if month_data is not None and not month_data.empty:
                all_data.append(month_data)

        if not all_data:
            return None

        combined_data = pd.concat(all_data, ignore_index=True)
        # Group sum in case overlaps
        daily_counts = combined_data.groupby("ds")["y"].sum().reset_index()
        daily_counts = daily_counts.sort_values("ds").reset_index(drop=True)

        return daily_counts

    def generate_holidays(self, events_list, training_years=None):
        """
        Convert events list into a holiday DataFrame compatible with the Prophet model.
        
        Filters out 'ramadan' events (since they are treated separately as regressors)
        and expands the start-end date ranges into daily records.
        
        Args:
            events_list (list of dicts): List of event configurations where each dict contains:
                - name (str): The name of the event/holiday.
                - start (str): Start date in YYYY-MM-DD format.
                - end (str): End date in YYYY-MM-DD format.
            training_years (list, optional): Unused parameter for API signature backward compatibility.
            
        Returns:
            pandas.DataFrame or None: DataFrame compatible with Prophet holidays, or None if events_list is empty.
                
                DataFrame Columns and Types:
                    - holiday (object/str): Name of the holiday/event.
                    - ds (datetime64[ns]): Date of the holiday day.
                    - lower_window (int64): Extends the holiday back by this number of days (default 0).
                    - upper_window (int64): Extends the holiday forward by this number of days (default 0).
        """
        if not events_list:
            return None
        all_holidays = []
        import re
        for event_data in events_list:
            name = event_data.get("name", "")
            if "ramadan" in name.lower():
                continue
            # Strip 4-digit years from the event name so Prophet learns across years
            base_name = re.sub(r'\s*\b20\d{2}\b\s*$', '', name).strip()
            event = Event(
                base_name, event_data.get("start"), event_data.get("end")
            )
            all_holidays.append(event.create_event())
        return pd.concat(all_holidays, ignore_index=True) if all_holidays else None

    def _train_single_branch(
        self,
        branch_name,
        years,
        events_list,
        prediction_days,
        confidence,
        category_name=None,
        training_run_id=None,
        db_branch_id=None,
        db_category_id=0,
        global_last_date=None,
    ):
        """
        Internal helper to preprocess data, train a Prophet model, generate forecasts, and save results.
        
        Applies robust outlier detection (rolling median and IQR method), removes weekends, filters low volume days,
        registers payday and Ramadan regressors, fits Prophet, generates predictions, and saves to database/cache.
        
        Args:
            branch_name (str): Name of the branch.
            years (list of int or str): List of training years.
            events_list (list of dict): List of special events dictionaries with keys 'name', 'start', 'end'.
            prediction_days (int): Horizon for future predictions in days.
            confidence (float): Width of the prediction interval (e.g. 0.95).
            category_name (str or None): Optional category name. Defaults to None.
            training_run_id (int or None): Optional database ID for tracking the training run. Defaults to None.
            db_branch_id (int or None): Optional database ID of the branch. Defaults to None.
            db_category_id (int): Database ID of the category (0 if branch-level). Defaults to 0.
            
        Returns:
            bool: True if training and data storage succeeded, False otherwise.
        """
        safe_name = self.get_safe_key(branch_name, category_name)
        label = f"{branch_name}"
        if category_name:
            label += f" > {category_name}"
        print(f"   Training: {label} (Key: {safe_name})...")

        # 1. Load Data
        daily_counts = self.generate_model_feedable_data(
            years, events_list, branch_name, category_name
        )
        if daily_counts is None or len(daily_counts) < 7:
            num_days = len(daily_counts) if daily_counts is not None else 0
            if num_days == 0:
                print(f"      Skipping {label}: Zero data records available.")
                return False

            print(
                f"      Cold-Start Triggered for {label}: Generating 365-day Heuristic Baseline Forecast ({num_days} day(s) of history)"
            )
            
            # 1. Calculate Base Volume (Mean of available 1-6 days)
            base_volume = float(daily_counts["y"].mean()) if not daily_counts.empty else 50.0
            last_date = global_last_date if global_last_date is not None else daily_counts["ds"].max().date()
            
            # 2. Generate 365 Future Dates with Standard Operational Weekly Ratios (Weekday = 1.0, Weekend = 0.20)
            forecast_rows = []
            for i in range(1, prediction_days + 1):
                f_date = last_date + timedelta(days=i)
                day_name = f_date.strftime("%A")
                is_weekend = f_date.weekday() >= 5
                ratio = 0.20 if is_weekend else 1.0
                
                pred_val = int(round(base_volume * ratio))
                low_val = int(round(pred_val * 0.85))
                upp_val = int(round(pred_val * 1.15))
                
                forecast_rows.append(
                    {
                        "date": f_date,
                        "day_of_week": day_name,
                        "predicted": max(1, pred_val),
                        "lower_bound": max(1, low_val),
                        "upper_bound": max(1, upp_val),
                    }
                )
                
            # 3. Save Cold-Start Forecasts to Database
            if training_run_id and db_branch_id is not None:
                try:
                    save_forecasts_to_db(
                        training_run_id, db_branch_id, db_category_id, forecast_rows
                    )
                    actual_rows = [
                        {"date": r["ds"].date(), "actual_count": int(r["y"])}
                        for _, r in daily_counts.iterrows()
                    ]
                    save_actual_traffic(db_branch_id, db_category_id, actual_rows)
                    print(
                        f"      ✓ Cold-Start forecast ({prediction_days} days) successfully saved to DB for {label}."
                    )
                except Exception as e:
                    print(f"      Error saving cold-start forecast: {e}")
                    
            return True

        # Apply robust outlier detection (IQR Method)
        daily_counts = daily_counts.sort_values("ds")

        # Calculate rolling median to establish a baseline
        daily_counts["rolling_med"] = (
            daily_counts["y"].rolling(window=21, center=True, min_periods=1).median()
        )

        # Calculate IQR (Interquartile Range) for robust outlier detection
        # We look at residuals (difference from rolling median) instead of raw values
        # to handle seasonality.
        daily_counts["residual"] = daily_counts["y"] - daily_counts["rolling_med"]
        Q1 = daily_counts["residual"].quantile(0.25)
        Q3 = daily_counts["residual"].quantile(0.75)
        IQR = Q3 - Q1

        # Define lower bound for "low traffic" anomalies
        # We are lenient on the upper bound (high traffic is usually real)
        # but strict on the lower bound (broken machines, system down, etc.)
        lower_bound = Q1 - (1.5 * IQR)

        # Filter
        daily_counts = daily_counts[daily_counts["residual"] >= lower_bound]

        # Still keep the hard min to avoid near-zero days
        daily_counts = daily_counts[daily_counts["y"] >= 1]

        if len(daily_counts) < 7:
            return False

        holidays = self.generate_holidays(events_list)

        # Auto-enable yearly seasonality ONLY if dataset has at least ~1 year (360+ days) of history
        date_span_days = (daily_counts["ds"].max() - daily_counts["ds"].min()).days
        use_yearly_seasonality = date_span_days >= 360

        # 4. Train Prophet
        try:
            m = Prophet(
                yearly_seasonality=use_yearly_seasonality,
                weekly_seasonality=True,
                daily_seasonality=False,
                seasonality_mode="multiplicative",
                holidays=holidays,
                changepoint_prior_scale=0.15,
                interval_width=confidence,
            )
            m.fit(daily_counts)

            # 4b. Cross-Validation (Model Quality Assessment)
            # Uses only training data to evaluate model reliability at training time.
            # No external actuals needed — Prophet retrains at multiple cutoff points internally.
            cv_results = None
            try:
                from prophet.diagnostics import cross_validation, performance_metrics

                df_cv = cross_validation(
                    m, initial='540 days', period='90 days', horizon='30 days'
                )
                df_perf = performance_metrics(df_cv)

                cv_results = {
                    "cv_mape": round(df_perf['mape'].mean() * 100, 2),
                    "cv_rmse": round(df_perf['rmse'].mean(), 2),
                    "cv_mae": round(df_perf['mae'].mean(), 2),
                    "cv_coverage": round(df_perf['coverage'].mean(), 4),
                    "horizon_days": 30,
                }
                print(f"      CV Results: MAPE={cv_results['cv_mape']}%, "
                      f"RMSE={cv_results['cv_rmse']}, Coverage={cv_results['cv_coverage']}")
            except Exception as e:
                print(f"      Warning: Cross-validation skipped for {label}: {e}")

            # 5. Forecast
            future = m.make_future_dataframe(periods=prediction_days)
            fcst = m.predict(future)

            # Store in Dicts
            self.models[safe_name] = m
            self.forecasts[safe_name] = fcst
            self.daily_counts_map[safe_name] = daily_counts

            # Save predictions to database
            if training_run_id and db_branch_id is not None:
                try:
                    future_data = fcst
                    cutoff_date = global_last_date if global_last_date is not None else daily_counts["ds"].max().date()
                    forecast_rows = []
                    for _, row in future_data.iterrows():
                        r_date = row["ds"].date()
                        if r_date > cutoff_date:
                            forecast_rows.append(
                                {
                                    "date": r_date,
                                    "day_of_week": row["ds"].strftime("%A"),
                                    "predicted": max(0, int(round(row["yhat"]))),
                                    "lower_bound": max(0, int(round(row["yhat_lower"]))),
                                    "upper_bound": int(round(row["yhat_upper"])),
                                }
                            )
                    save_forecasts_to_db(
                        training_run_id, db_branch_id, db_category_id, forecast_rows
                    )

                    # In-Sample Actuals: Cache actual traffic data for the training years in the database.
                    # This acts as a database cache to speed up historical stats and chart queries.
                    actual_rows = []
                    for _, row in daily_counts.iterrows():
                        actual_rows.append(
                            {
                                "date": row["ds"].date(),
                                "actual_count": int(row["y"]),
                            }
                        )
                    save_actual_traffic(db_branch_id, db_category_id, actual_rows)

                    # Out-of-Sample Validation: Save actual traffic data for the prediction holdout year.
                    # This allows the system to compare predictions against real-world data for error metric validation.
                    if years:
                        prediction_year = int(max(years)) + 1
                        out_of_sample_counts = self.generate_model_feedable_data(
                            [prediction_year], events_list, branch_name, category_name
                        )
                        if out_of_sample_counts is not None and not out_of_sample_counts.empty:
                            val_actual_rows = []
                            for _, row in out_of_sample_counts.iterrows():
                                val_actual_rows.append(
                                    {
                                        "date": row["ds"].date(),
                                        "actual_count": int(row["y"]),
                                    }
                                )
                            save_actual_traffic(db_branch_id, db_category_id, val_actual_rows)

                    # Save cross-validation metrics if available
                    if cv_results:
                        save_cv_metrics(
                            training_run_id, db_branch_id, db_category_id, cv_results
                        )
                except Exception as e:
                    print(f"      Warning: DB save failed for {label}: {e}")

            return True

        except Exception as e:
            print(f"      Error training {branch_name}: {e}")
            return False

    def get_global_max_date(self):
        """Find the maximum actual date across all parquet files in DATA_PATH."""
        all_parquet_files = sorted(glob.glob(os.path.join(DATA_PATH, "**", "*.parquet"), recursive=True))
        max_d = None
        for fp in all_parquet_files:
            try:
                df = pd.read_parquet(fp)
                date_col = "Ticket Issue Date" if "Ticket Issue Date" in df.columns else "Issue Date"
                if date_col in df.columns:
                    cur_max = pd.to_datetime(df[date_col], errors="coerce").max().date()
                    if max_d is None or cur_max > max_d:
                        max_d = cur_max
            except:
                pass
        return max_d

    def train_model(
        self,
        years,
        events_list,
        branch_name=None,
        prediction_days=365,
        confidence=0.95,
        category_name=None,
    ):
        """
        Orchestrates the training run for specific branches, categories, or the entire network.
        
        Resets model caches, logs a training run record in the database, determines target branches
        and categories, and sequential training loops with status updates.
        
        Args:
            years (list of int or str): List of years to use for training data.
            events_list (list of dict): Special calendar events (Ramadan, Eid, custom events).
            branch_name (str or None): Optional target branch name. If None, trains all branches + aggregate.
            prediction_days (int): Future prediction horizon in days. Defaults to 365.
            confidence (float): Prediction interval confidence width. Defaults to 0.95.
            category_name (str or None): Optional target category name. Defaults to None.
            
        Returns:
            bool: True if training completed successfully, False otherwise.
        """
        target_label = f"{branch_name or 'ALL BRANCHES'}{f' > {category_name}' if category_name else ''}"
        print(f"Starting training run for target: {target_label}")

        # Clear previous model state before training
        print("Clearing previous model state...")
        self.models = {}
        self.forecasts = {}
        self.daily_counts_map = {}

        global_last_date = self.get_global_max_date()
        print(f"Global dataset max date: {global_last_date}")

        self.spain_holidays = self.generate_holidays(
            events_list
        )  # Just for caching purposes

        # Create a training run record in DB
        years_str = ",".join(str(y) for y in years) if years else ""
        try:
            db_branch_id_for_run = (
                get_or_create_branch(branch_name) if branch_name else None
            )
            run_id = create_training_run(
                branch_id=db_branch_id_for_run,
                years_used=years_str,
                prediction_days=prediction_days,
                confidence=confidence,
            )
            print(f"Created training run #{run_id} in database")
        except Exception as e:
            print(f"Warning: Could not create training run in DB: {e}")
            run_id = None

        if category_name and branch_name:
            # Train specific branch + category combo ONLY
            db_bid = get_or_create_branch(branch_name) if run_id else None
            db_cid = get_or_create_category(category_name, db_bid) if db_bid else 0
            success = self._train_single_branch(
                branch_name,
                years,
                events_list,
                prediction_days,
                confidence,
                category_name,
                training_run_id=run_id,
                db_branch_id=db_bid,
                db_category_id=db_cid,
            )
            if run_id:
                complete_training_run(run_id, "success" if success else "failed")
            return success
        elif branch_name:
            # Train Single Specific Branch + ALL its categories
            db_bid = get_or_create_branch(branch_name) if run_id else None
            success = self._train_single_branch(
                branch_name,
                years,
                events_list,
                prediction_days,
                confidence,
                training_run_id=run_id,
                db_branch_id=db_bid,
                db_category_id=0,
                global_last_date=global_last_date,
            )

            # Also train all categories within this branch
            print(f">>> Discovering categories for {branch_name}...")
            branch_categories = self.get_all_categories(years, branch_name)
            print(f">>> Found {len(branch_categories)} categories to train.")

            cat_count = 0
            for rank, cat in enumerate(branch_categories, 1):
                db_cid = get_or_create_category(cat, db_bid, rank) if db_bid else 0
                if self._train_single_branch(
                    branch_name,
                    years,
                    events_list,
                    prediction_days,
                    confidence,
                    cat,
                    training_run_id=run_id,
                    db_branch_id=db_bid,
                    db_category_id=db_cid,
                    global_last_date=global_last_date,
                ):
                    cat_count += 1

            print(
                f"Successfully trained {cat_count}/{len(branch_categories)} category models for {branch_name}."
            )
            if run_id:
                complete_training_run(run_id, "success" if success else "failed")
            return success
        else:
            # 1. Train Aggregate 'ALL' Model
            print(">>> Training Aggregate 'ALL' Model...")
            db_all_bid = get_or_create_branch("ALL") if run_id else None
            self._train_single_branch(
                "ALL",
                years,
                events_list,
                prediction_days,
                confidence,
                training_run_id=run_id,
                db_branch_id=db_all_bid,
                db_category_id=0,
                global_last_date=global_last_date,
            )

            # 2. Find and Train ALL Individual Branches + their categories
            all_branches = self.get_all_branches(years)
            total_branches = len(all_branches)
            print(f"Found {total_branches} branches to train.")

            count = 0
            start_time = time.time()
            for idx, branch in enumerate(all_branches, 1):
                # Progress bar
                elapsed = time.time() - start_time
                if idx > 1:
                    avg_per_branch = elapsed / (idx - 1)
                    remaining = avg_per_branch * (total_branches - idx + 1)
                    eta_min = int(remaining // 60)
                    eta_sec = int(remaining % 60)
                    eta_str = f"ETA: {eta_min}m {eta_sec}s"
                else:
                    eta_str = "ETA: calculating..."

                pct = int((idx / total_branches) * 100)
                bar_len = 30
                filled = int(bar_len * idx / total_branches)
                bar = "█" * filled + "░" * (bar_len - filled)
                print(f"\n[{idx}/{total_branches}] {pct}% {bar} {branch} | {eta_str}")

                db_bid = get_or_create_branch(branch) if run_id else None
                if self._train_single_branch(
                    branch,
                    years,
                    events_list,
                    prediction_days,
                    confidence,
                    training_run_id=run_id,
                    db_branch_id=db_bid,
                    db_category_id=0,
                    global_last_date=global_last_date,
                ):
                    count += 1

                # Train all categories within this branch
                branch_categories = self.get_all_categories(years, branch)
                cat_count = 0
                for rank, cat in enumerate(branch_categories, 1):
                    db_cid = get_or_create_category(cat, db_bid, rank) if db_bid else 0
                    if self._train_single_branch(
                        branch,
                        years,
                        events_list,
                        prediction_days,
                        confidence,
                        cat,
                        training_run_id=run_id,
                        db_branch_id=db_bid,
                        db_category_id=db_cid,
                    ):
                        cat_count += 1
                if branch_categories:
                    print(
                        f"   Trained {cat_count}/{len(branch_categories)} categories for {branch}"
                    )

            print(
                f"Successfully trained {count}/{len(all_branches)} branch models (+ categories)."
            )
            if run_id:
                complete_training_run(run_id, "success")
            return True
