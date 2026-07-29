from typing import List, Optional
import os
import json

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from model import Model
from pydantic_mode import (
    Stats,
    EventItem,
    EventCreate,
    TrainRequest,
    TrainResponse,
    SimulationRequest,
    AgentChatRequest,
    AgentChatResponse,
)
from database import (
    init_db,
    is_model_trained,
    get_trained_branches,
    get_branch_id_by_name,
    get_category_id_by_name,
    get_categories_for_branch,
    get_latest_forecasts,
    get_historical_data,
    get_stats_from_db,
    get_weekly_pattern_from_db,
    save_simulation_setting,
    get_simulation_settings,
    get_simulation_setting_by_id,
)
from auth import (
    SignupRequest,
    LoginRequest,
    TokenResponse,
    UserResponse,
    handle_signup,
    handle_login,
    get_current_user,
    require_admin,
)

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize model
forecast_model = Model()

# Cache for processed 2024 valicledation data (actual counts per month)
validation_cache = {}

# Events storage (in-memory, persisted to JSON file)
EVENTS_FILE = "events_data.json"
events_storage = []


# Load events from file on module load
def load_events_from_file():
    global events_storage
    if os.path.exists(EVENTS_FILE):
        try:
            with open(EVENTS_FILE, "r") as f:
                events_storage = json.load(f)
            print(f"Loaded {len(events_storage)} events from file")
        except Exception as e:
            print(f"Error loading events: {e}")
            events_storage = []


def save_events_to_file():
    try:
        with open(EVENTS_FILE, "w") as f:
            json.dump(events_storage, f, indent=2)
        print(f"Saved {len(events_storage)} events to file")
    except Exception as e:
        print(f"Error saving events: {e}")


load_events_from_file()


# Branch access control helper
def check_branch_access(branch_name: str, allowed_branches: Optional[str]):
    """Raise 403 if branch_name is not in the allowed_branches list."""
    if allowed_branches and branch_name:
        allowed = [b.strip() for b in allowed_branches.split(",")]
        if branch_name not in allowed:
            raise HTTPException(
                status_code=403, detail="Access to this branch is not allowed"
            )


# Correction factor (set to 1.0 by default)
PREDICTION_CORRECTION_FACTOR = 1.0

# Base data path configuration
DATA_PATH = os.getenv("DATA_PATH", os.path.join("..", "Data", "icp_data"))

# Ramadan correction factor
# Model over-predicts during Ramadan due to 2023 anomaly (0.97x vs 0.85x normal)
# Based on March 2025: Predicted 206,686 vs Actual 160,771 = 0.78 correction needed
# We migt not  be ussing this now because we have remove 2023 ramzan data
# RAMADAN_CORRECTION_FACTOR = 1.0 (Resetting as model improvements should handle this)
RAMADAN_CORRECTION_FACTOR = 1.0


def get_correction_factor_for_date(date):
    """
    Get the appropriate correction factor for a given date.
    Returns RAMADAN_CORRECTION_FACTOR if date is during Ramadan, else 1.0
    """
    import pandas as pd

    if isinstance(date, str):
        date = pd.Timestamp(date)

    # Check if date falls within any Ramadan event
    for event in events_storage:
        event_name = event.get("name", "").lower()
        if "ramadan" in event_name:
            start = pd.Timestamp(event.get("start"))
            end = pd.Timestamp(event.get("end"))
            if start <= date <= end:
                return RAMADAN_CORRECTION_FACTOR * PREDICTION_CORRECTION_FACTOR

    return PREDICTION_CORRECTION_FACTOR


@app.on_event("startup")
async def startup_event():
    """Initialize DB and check if model has been trained"""
    # Initialize database tables
    init_db()

    # Check if we have a trained model in the database
    if is_model_trained():
        branches = get_trained_branches()
        print(
            f"Trained model found in database! {len(branches)} branches ready to serve."
        )
    else:
        print("NO TRAINED MODEL FOUND")
        print("The model has not been trained yet.")
        print("An admin must login and train the model from the Configuration tab.")


# Authentication Endpoints


@app.post("/api/auth/signup", response_model=TokenResponse)
async def signup_endpoint(request: SignupRequest):
    """Register a new user"""
    return handle_signup(request)


@app.post("/api/auth/login", response_model=TokenResponse)
async def login_endpoint(request: LoginRequest):
    """Login and get JWT token"""
    return handle_login(request)


@app.get("/api/auth/me", response_model=UserResponse)
async def get_current_user_endpoint(current_user: dict = Depends(get_current_user)):
    """Get current authenticated user"""
    return UserResponse(
        id=current_user["id"],
        email=current_user["email"],
        name=current_user["name"],
        role=current_user["role"],
    )


# Events Endpoints


@app.get("/api/events", response_model=List[EventItem])
async def get_events():
    """Get all stored events"""
    return events_storage


@app.post("/api/events", response_model=EventItem)
async def create_event(event: EventCreate, admin: dict = Depends(require_admin)):
    """Create a new event (Admin only)"""
    # Validate that start date is before or equal to end date
    if event.start > event.end:
        raise HTTPException(
            status_code=400, detail="Start date must be before or equal to end date"
        )

    event_data = {
        "name": event.name,
        "start": event.start,
        "end": event.end,
        "impact": event.impact,
        "notes": event.notes or "",
    }
    events_storage.append(event_data)
    save_events_to_file()
    return event_data


@app.delete("/api/events/{event_index}")
async def delete_event(event_index: int, admin: dict = Depends(require_admin)):
    """Delete an event by index (Admin only)"""
    if event_index < 0 or event_index >= len(events_storage):
        raise HTTPException(status_code=404, detail="Event not found")

    deleted_event = events_storage.pop(event_index)
    save_events_to_file()
    return {"message": "Event deleted", "event": deleted_event}


@app.put("/api/events/{event_index}", response_model=EventItem)
async def update_event(
    event_index: int, event: EventCreate, admin: dict = Depends(require_admin)
):
    """Update an event by index (Admin only)"""
    if event_index < 0 or event_index >= len(events_storage):
        raise HTTPException(status_code=404, detail="Event not found")

    # Validate that start date is before or equal to end date
    if event.start > event.end:
        raise HTTPException(
            status_code=400, detail="Start date must be before or equal to end date"
        )

    event_data = {
        "name": event.name,
        "start": event.start,
        "end": event.end,
        "impact": event.impact,
        "notes": event.notes or "",
    }
    events_storage[event_index] = event_data
    save_events_to_file()
    return event_data


@app.post("/api/events/bulk", response_model=List[EventItem])
async def bulk_save_events(
    events: List[EventCreate], admin: dict = Depends(require_admin)
):
    """Replace all events with new list (Admin only)"""
    global events_storage
    events_storage = []
    for event in events:
        event_data = {
            "name": event.name,
            "start": event.start,
            "end": event.end,
            "impact": event.impact,
            "notes": event.notes or "",
        }
        events_storage.append(event_data)
    save_events_to_file()
    return events_storage


# Branches Endpoints


@app.get("/api/branches")
async def get_branches(allowed_branches: Optional[str] = None):
    """Get list of available branches from database or recent data"""
    branches = []
    try:
        from database import LocalSession, Branch
        db = LocalSession()
        db_branches = db.query(Branch.name).distinct().all()
        branches = sorted([b[0] for b in db_branches if b[0]])
        db.close()
    except Exception as db_err:
        print(f"Database error getting branches: {db_err}")
        branches = []

    # Fallback to parquet files if database returned nothing
    if not branches:
        try:
            if os.path.exists(DATA_PATH):
                # Find the most recent year folder
                years = sorted(
                    [
                        d
                        for d in os.listdir(DATA_PATH)
                        if os.path.isdir(os.path.join(DATA_PATH, d)) and d.isdigit()
                    ],
                    reverse=True,
                )
                if years:
                    latest_year_dir = os.path.join(DATA_PATH, years[0])
                    files = [f for f in os.listdir(latest_year_dir) if f.endswith(".parquet")]
                    if files:
                        import pandas as pd
                        file_path = os.path.join(latest_year_dir, files[0])
                        df = pd.read_parquet(file_path, columns=["Branch Name"])
                        branches = sorted(df["Branch Name"].unique().tolist())
        except Exception as e:
            print(f"Error getting branches from parquet fallback: {e}")

    try:
        # Filter branches if allowed_branches is provided
        if allowed_branches:
            allowed = [b.strip() for b in allowed_branches.split(",")]
            branches = [b for b in branches if b in allowed]

        from database import get_region_for_branch

        emirates_map = {}
        for b in branches:
            em = get_region_for_branch(b)
            if em not in emirates_map:
                emirates_map[em] = []
            emirates_map[em].append(b)

        return {"branches": branches, "emirates": emirates_map}

    except Exception as e:
        print(f"Error getting branches: {e}")
        return {"branches": []}



@app.get("/api/categories")
async def get_categories(
    branch_name: Optional[str] = None,
    emirate: Optional[str] = None,
    allowed_branches: Optional[str] = None,
):
    """Get list of categories for a branch or emirate from the database"""
    check_branch_access(branch_name, allowed_branches)
    try:
        from database import (
            get_categories_for_branch,
            get_region_for_branch,
            LocalSession,
            Branch,
            Category,
        )

        if branch_name:
            categories = get_categories_for_branch(branch_name)
        elif emirate:
            db = LocalSession()
            all_branches = db.query(Branch).all()
            emirate_branches = [
                b for b in all_branches if get_region_for_branch(b.name) == emirate
            ]
            emirate_branch_ids = [b.id for b in emirate_branches]

            categories_query = (
                db.query(Category.name)
                .filter(Category.branch_id.in_(emirate_branch_ids))
                .distinct()
                .all()
            )
            categories = sorted([c[0] for c in categories_query])
            db.close()
        else:
            db = LocalSession()
            categories_query = db.query(Category.name).distinct().all()
            categories = sorted([c[0] for c in categories_query])
            db.close()
        return {"categories": categories}
    except Exception as e:
        print(f"Error getting categories: {e}")
        return {"categories": []}



@app.get("/api/hourly-profile")
async def get_hourly_profile(branch_name: Optional[str] = None):
    """Get the 24-hour weight profile for a specific branch, or fallback to default."""
    try:
        from database import get_branch_id_by_name, get_branch_hourly_profile
        from simulation import DEFAULT_HOURLY_PROFILE
        
        profile = None
        if branch_name:
            branch_id = get_branch_id_by_name(branch_name)
            if branch_id:
                profile = get_branch_hourly_profile(branch_id)
                
        if not profile:
            profile = DEFAULT_HOURLY_PROFILE[:24]
            # Ensure it is exactly 24 hours long
            while len(profile) < 24:
                profile.append(0.0)
            profile = profile[:24]
            # Normalize to 1.0
            total = sum(profile)
            if total > 0:
                profile = [p / total for p in profile]
            else:
                profile = [1.0/24.0] * 24
                
        return {"profile": profile}
    except Exception as e:
        print(f"Error getting hourly profile: {e}")
        return {"profile": [1.0/24.0] * 24}


# Training Endpoints


@app.get("/api/model/status")
async def get_model_status():
    """Check if model is trained and ready"""
    trained = is_model_trained()
    branches = get_trained_branches() if trained else []

    return {
        "is_trained": trained,
        "model_count": len(branches),
        "branches": branches,
        "message": (
            "Model is trained and ready" if trained else "No trained model found"
        ),
    }


@app.post("/api/train", response_model=TrainResponse)
async def train_model(request: TrainRequest, admin: dict = Depends(require_admin)):
    """Train the forecast model with specified parameters (Admin only)"""
    try:
        print(
            f"Training requested: Years={request.years}, "
            f"Branch={request.branch_name or 'ALL'}, "
            f"Events={len(request.events)}"
        )

        # Convert events to the format expected by model
        events_list = []
        for event in request.events:
            events_list.append(
                {"name": event.name, "start": event.start, "end": event.end}
            )

        # Call the model's train method
        success = forecast_model.train_model(
            years=request.years,
            events_list=events_list,
            branch_name=request.branch_name,
            prediction_days=request.prediction_days,
            confidence=request.confidence,
            category_name=request.category_name,
        )

        if success:
            check_branch = request.branch_name if request.branch_name else "ALL"
            daily_counts = forecast_model.get_daily_counts(
                check_branch, request.category_name
            )
            forecast = forecast_model.get_forecast(check_branch, request.category_name)

            return TrainResponse(
                success=True,
                message=f"Model trained successfully! (Branch: {check_branch})",
                training_days=(len(daily_counts) if daily_counts is not None else 0),
                events_count=len(events_list),
                forecast_days=(len(forecast) if forecast is not None else 0),
                prediction_year=(max(request.years) + 1 if request.years else None),
            )
        else:
            return TrainResponse(
                success=False, message="Model training failed. Check server logs."
            )

    except Exception as e:
        print(f"Training error: {e}")
        import traceback

        traceback.print_exc()
        return TrainResponse(success=False, message=f"Training error: {str(e)}")


@app.get("/api/forecast-by-month")
async def get_forecast_by_month(
    year: int = None,
    month: int = None,
    branch_name: Optional[str] = None,
    emirate: Optional[str] = None,
    category_name: Optional[str] = None,
    allowed_branches: Optional[str] = None,
):
    from datetime import date
    from datetime import datetime as dt

    year = year or date.today().year
    """Get forecast predictions for a specific month and branch from database"""
    check_branch_access(branch_name, allowed_branches)

    # 1. EMIRATE-LEVEL AGGREGATE LOGIC
    if emirate and (not branch_name or branch_name == "ALL"):
        from database import (
            LocalSession,
            Branch,
            Category,
            DailyForecast,
            TrainingRun,
            get_region_for_branch,
        )
        from sqlalchemy import extract

        db = LocalSession()

        # Get branches in this emirate
        all_branches = db.query(Branch).all()
        emirate_branches = [
            b for b in all_branches if get_region_for_branch(b.name) == emirate
        ]
        emirate_branch_ids = [b.id for b in emirate_branches]

        if not emirate_branch_ids:
            db.close()
            return []

        # Find latest successful training run
        latest_run = (
            db.query(TrainingRun)
            .filter(TrainingRun.status == "success")
            .order_by(TrainingRun.id.desc())
            .first()
        )
        if not latest_run:
            db.close()
            return []

        # Get category IDs for these branches
        category_ids = []
        if category_name:
            for b in emirate_branches:
                cid = get_category_id_by_name(category_name, b.id)
                if cid is not None:
                    category_ids.append(cid)
        else:
            category_ids = [0]  # branch level

        # Query all forecasts
        query = db.query(DailyForecast).filter(
            DailyForecast.training_run_id == latest_run.id,
            DailyForecast.branch_id.in_(emirate_branch_ids),
            DailyForecast.category_id.in_(category_ids),
        )
        if month and year:
            query = query.filter(
                extract("month", DailyForecast.date) == month,
                extract("year", DailyForecast.date) == year,
            )
        forecasts = query.all()
        db.close()

        if not forecasts:
            return []

        # Group by date and sum
        from collections import defaultdict

        grouped = defaultdict(
            lambda: {"predicted": 0, "lower": 0, "upper": 0, "day": ""}
        )
        for r in forecasts:
            d_str = r.date.strftime("%Y-%m-%d")
            grouped[d_str]["predicted"] += r.predicted
            grouped[d_str]["lower"] += r.lower_bound
            grouped[d_str]["upper"] += r.upper_bound
            grouped[d_str]["day"] = r.day_of_week

        result = []
        for d_str in sorted(grouped.keys()):
            vals = grouped[d_str]
            date_obj = dt.strptime(d_str, "%Y-%m-%d")
            is_weekend = date_obj.weekday() >= 5

            pred_val = vals["predicted"]
            low_val = vals["lower"]
            upp_val = vals["upper"]

            # Deviation %: average spread (±%) around the predicted value
            dev_pct = round(((upp_val - low_val) / (2 * pred_val)) * 100, 1) if pred_val > 0 else 0

            result.append(
                {
                    "date": d_str,
                    "day": vals["day"],
                    "predicted": pred_val,
                    "lower": low_val,
                    "upper": upp_val,
                    "deviation_pct": dev_pct,
                    "isWeekend": is_weekend,
                }
            )
        return result

    # Resolve branch/category IDs
    b_name = branch_name if branch_name else "ALL"
    branch_id = get_branch_id_by_name(b_name)
    if branch_id is None:
        return []

    category_id = 0
    if category_name:
        cid = get_category_id_by_name(category_name, branch_id)
        if cid is not None:
            category_id = cid

    # Query forecasts from database
    forecasts = get_latest_forecasts(branch_id, category_id, month, year)

    if not forecasts:
        return []

    from datetime import datetime as dt

    result = []
    for row in forecasts:
        date_obj = dt.strptime(row["date"], "%Y-%m-%d")
        is_weekend = date_obj.weekday() >= 5

        predicted_value = row["predicted"]
        lower_value = row["lower_bound"]
        upper_value = row["upper_bound"]

        # Deviation %: average spread (±%) around the predicted value
        dev_pct = round(((upper_value - lower_value) / (2 * predicted_value)) * 100, 1) if predicted_value > 0 else 0

        result.append(
            {
                "date": row["date"],
                "day": row["day_of_week"],
                "predicted": predicted_value,
                "lower": lower_value,
                "upper": upper_value,
                "deviation_pct": dev_pct,
                "isWeekend": is_weekend,
            }
        )

    return result


@app.get("/api/forecast/download-excel")
async def download_excel(
    year: int = None,
    month: int = 1,
    branch_name: Optional[str] = None,
    emirate: Optional[str] = None,
    category_name: Optional[str] = None,
    allowed_branches: Optional[str] = None,
):
    """Download volume forecast and actual traffic data as an Excel file"""
    check_branch_access(branch_name, allowed_branches)

    # 1. Fetch the unified forecast data
    forecast_data = await get_forecast_by_month(
        year=year,
        month=month,
        branch_name=branch_name,
        emirate=emirate,
        category_name=category_name,
        allowed_branches=allowed_branches,
    )

    # 2. Fetch the corresponding actual traffic numbers
    import io
    import pandas as pd
    from fastapi.responses import StreamingResponse
    from database import (
        LocalSession,
        Branch,
        Category,
        ActualTraffic,
        get_branch_id_by_name,
        get_category_id_by_name,
        get_region_for_branch,
    )
    from sqlalchemy import extract

    branch_ids = []
    if branch_name and branch_name != "ALL":
        bid = get_branch_id_by_name(branch_name)
        if bid:
            branch_ids = [bid]
    elif emirate:
        db = LocalSession()
        all_branches = db.query(Branch).all()
        branch_ids = [
            b.id for b in all_branches if get_region_for_branch(b.name) == emirate
        ]
        db.close()
    else:
        bid = get_branch_id_by_name("ALL")
        if bid:
            branch_ids = [bid]

    db = LocalSession()
    actuals_dict = {}
    if branch_ids:
        category_ids = [0]
        if category_name:
            for b_id in branch_ids:
                cid = get_category_id_by_name(category_name, b_id)
                if cid is not None:
                    category_ids.append(cid)

        actual_query = db.query(ActualTraffic).filter(
            ActualTraffic.branch_id.in_(branch_ids),
            ActualTraffic.category_id.in_(category_ids),
        )
        if month and year:
            actual_query = actual_query.filter(
                extract("month", ActualTraffic.date) == month,
                extract("year", ActualTraffic.date) == year,
            )
        actuals = actual_query.all()

        from collections import defaultdict

        actuals_grouped = defaultdict(int)
        for a in actuals:
            d_str = a.date.strftime("%Y-%m-%d")
            actuals_grouped[d_str] += a.actual_count
        actuals_dict = dict(actuals_grouped)
    db.close()

    # 3. Build spreadsheet rows
    rows = []
    for f in forecast_data:
        date_str = f["date"]
        actual_val = actuals_dict.get(date_str, None)

        rows.append(
            {
                "Date": date_str,
                "Day of Week": f["day"],
                "Predicted Tickets": f["predicted"],
                "Lower Bound (Min Expected)": f["lower"],
                "Upper Bound (Max Expected)": f["upper"],
                "Actual Tickets": actual_val if actual_val is not None else "N/A",
            }
        )

    df = pd.DataFrame(rows)

    # 4. Stream file
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Volume Forecast")
    buffer.seek(0)

    name_label = branch_name or emirate or "All_Branches"
    clean_label = name_label.replace(" ", "_").replace(":", "")
    filename = f"Volume_Forecast_{clean_label}_{year}_{month:02d}.xlsx"

    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(
        buffer,
        headers=headers,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/api/weekly-pattern-by-month")
@app.get("/api/weekly-pattern")
async def get_weekly_pattern_by_month(
    year: int = None,
    month: int = 1,
    branch_name: Optional[str] = None,
    category_name: Optional[str] = None,
    allowed_branches: Optional[str] = None,
):
    from datetime import date

    year = year or date.today().year
    """Get weekly pattern for a specific month from database"""
    check_branch_access(branch_name, allowed_branches)
    b_name = branch_name if branch_name else "ALL"
    branch_id = get_branch_id_by_name(b_name)
    if branch_id is None:
        return []

    category_id = 0
    if category_name:
        cid = get_category_id_by_name(category_name, branch_id)
        if cid is not None:
            category_id = cid

    return get_weekly_pattern_from_db(branch_id, category_id, month, year)


@app.get("/api/historical")
async def get_historical(
    branch_name: Optional[str] = None,
    category_name: Optional[str] = None,
    allowed_branches: Optional[str] = None,
):
    """Get historical ticket data from database"""
    check_branch_access(branch_name, allowed_branches)
    b_name = branch_name if branch_name else "ALL"
    branch_id = get_branch_id_by_name(b_name)
    if branch_id is None:
        return []

    category_id = 0
    if category_name:
        cid = get_category_id_by_name(category_name, branch_id)
        if cid is not None:
            category_id = cid

    return get_historical_data(branch_id, category_id)


@app.get("/api/training-info")
async def get_training_info():
    """Return metadata about the latest successful training run (years used, prediction year)."""
    from database import LocalSession, TrainingRun

    db = LocalSession()
    try:
        latest_run = (
            db.query(TrainingRun)
            .filter(TrainingRun.status == "success")
            .order_by(TrainingRun.id.desc())
            .first()
        )
        if not latest_run or not latest_run.years_used:
            return {"years_used": [], "prediction_year": None}

        years = [
            int(y.strip())
            for y in latest_run.years_used.split(",")
            if y.strip().isdigit()
        ]
        from datetime import datetime

        if years:
            max_year = max(years)
            current_yr = datetime.now().year
            # If the model is continually learning from the ongoing year, focus UI on the ongoing year.
            prediction_year = max_year if max_year == current_yr else max_year + 1
        else:
            prediction_year = None
        return {
            "years_used": years,
            "prediction_year": prediction_year,
            "prediction_days": latest_run.prediction_days,
        }
    finally:
        db.close()


@app.get("/api/stats", response_model=Stats)
async def get_stats(
    branch_name: Optional[str] = None,
    category_name: Optional[str] = None,
    allowed_branches: Optional[str] = None,
):
    """Get summary statistics from database"""
    check_branch_access(branch_name, allowed_branches)
    b_name = branch_name if branch_name else "ALL"
    branch_id = get_branch_id_by_name(b_name)
    if branch_id is None:
        return {
            "totalDays": 0,
            "totalTickets": 0,
            "avgPerDay": 0,
            "minPerDay": 0,
            "maxPerDay": 0,
            "nextWeekTotal": 0,
            "nextWeekAvg": 0,
        }

    category_id = 0
    if category_name:
        cid = get_category_id_by_name(category_name, branch_id)
        if cid is not None:
            category_id = cid

    return get_stats_from_db(branch_id, category_id)



@app.get("/api/validation")
async def get_validation(
    year: int = None,
    month: int = None,
    branch_name: Optional[str] = None,
    category_name: Optional[str] = None,
    allowed_branches: Optional[str] = None,
):
    """Compare predictions with actual data from database for a specific month and branch"""
    check_branch_access(branch_name, allowed_branches)
    from datetime import date

    year = year or date.today().year
    from database import get_validation_data_from_db, get_branch_id_by_name, get_category_id_by_name

    b_name = branch_name if branch_name else "ALL"
    branch_id = get_branch_id_by_name(b_name)
    if branch_id is None:
        raise HTTPException(status_code=500, detail="Branch not found")

    category_id = 0
    if category_name:
        resolved_cat_id = get_category_id_by_name(category_name, branch_id)
        if resolved_cat_id is not None:
            category_id = resolved_cat_id
        else:
            category_id = -1

    # Validation is done on the requested prediction year
    val_year = year
    validation_data = get_validation_data_from_db(
        branch_id, category_id, month, val_year, events_list=events_storage
    )

    if not validation_data:
        return {
            "error": f"No actual/prediction data found for validation in {val_year}"
        }

    # Override the returned month/year so the UI dropdown doesn't get confused
    validation_data["month"] = f"{year}-{month}"
    validation_data["year"] = year

    return validation_data


# Simulation endpoints


@app.post("/api/simulate")
async def run_simulation_endpoint(request: SimulationRequest):
    """
    Run a Discrete Event Simulation stress test for a branch.

    Accepts the full Configuration Center parameters (services, workgroups,
    hourly inflows, lobby capacity) and runs a Monte Carlo DES simulation
    using SimPy. Returns averaged performance metrics.

    If save_config is True, persists the configuration to the database
    so it can be reloaded via the "Load History" button.
    """
    from simulation import run_simulation

    # Convert Pydantic model to dict for the simulation engine
    config_dict = request.model_dump()

    # Resolve branch_id from branch_name if needed (frontend passes name)
    if request.branch_name and request.branch_id == 0:
        resolved_id = get_branch_id_by_name(request.branch_name)
        if resolved_id:
            config_dict["branch_id"] = resolved_id

    try:
        # Run the simulation
        results = run_simulation(config_dict, num_trials=request.num_trials)

        # Optionally persist the config to DB
        config_id = None
        if request.save_config:
            try:
                config_id = save_simulation_setting(config_dict)
            except Exception as e:
                print(f"Warning: Failed to save simulation config: {e}")

        results["config_id"] = config_id
        return results

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Simulation failed: {str(e)}")


@app.get("/api/simulation/history")
async def get_simulation_history(branch_id: int = 0, branch_name: str = None):
    """
    Returns all saved simulation configurations for a branch.
    Used by the "Load History" button in the Configuration Center UI.
    Accepts either branch_id or branch_name for lookup.
    """
    if branch_name and branch_id == 0:
        resolved = get_branch_id_by_name(branch_name)
        if resolved:
            branch_id = resolved
    return {"settings": get_simulation_settings(branch_id)}


@app.get("/api/simulation/history/{setting_id}")
async def get_simulation_config(setting_id: int):
    """
    Returns a single saved simulation config with all nested relationships
    (services, hourly inflows, workgroups, skills).
    """
    config = get_simulation_setting_by_id(setting_id)
    if not config:
        raise HTTPException(status_code=404, detail="Simulation config not found")
    return config


# AI agent chat endpoint

try:
    from agent_service import initialize_agent
    operations_agent = initialize_agent()
    print("AI Operations Agent compiled successfully with LangGraph!")
except Exception as agent_err:
    print(f"Warning: Failed to compile AI Operations Agent: {agent_err}")
    operations_agent = None


@app.post("/api/agent/chat", response_model=AgentChatResponse)
async def chat_with_agent_endpoint(request: AgentChatRequest):
    """
    Stateful conversational chat with the AI Operations Agent.
    Runs simulated trials using tools behind the scenes if required.
    """
    if operations_agent is None:
        raise HTTPException(
            status_code=503,
            detail="AI Operations Agent is currently unavailable. Check server logs."
        )
        
    try:
        # Construct message payload for LangGraph React Agent
        config = {
            "configurable": {"thread_id": request.session_id},
            "recursion_limit": 100
        }
        
        # Enrich the query with branch context if provided
        user_message = request.message
        if request.branch_name:
            user_message = f"[Target Branch: {request.branch_name}, Active Month: {request.month or 1}, Active Year: {request.year or 2026}]\n{user_message}"
            
        result = operations_agent.invoke(
            {"messages": [("user", user_message)]},
            config=config
        )
        
        messages = result.get("messages", [])
        if messages:
            ai_response = messages[-1].content
        else:
            ai_response = "The agent was unable to formulate a response."
            
        return AgentChatResponse(
            response=ai_response,
            session_id=request.session_id
        )
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Agent reasoning failed: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8079))
    uvicorn.run(app, host="0.0.0.0", port=port)
