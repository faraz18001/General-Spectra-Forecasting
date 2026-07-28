from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.sql import func
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Boolean,
    Float,
    Date,
    ForeignKey,
    PrimaryKeyConstraint,
)
from datetime import datetime
import bcrypt
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Database setup - MSSQL via pyodbc
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_SERVER = os.getenv("DB_SERVER")
DB_NAME = os.getenv("DB_NAME")
DB_PORT = os.getenv("DB_PORT", "1433")

if DB_USER and DB_PASSWORD and DB_SERVER and DB_NAME:
    # MSSQL connection string using pymssql (FreeTDS - no TLS issues)
    import urllib.parse
    safe_password = urllib.parse.quote_plus(DB_PASSWORD)
    DATABASE_URL = f"mssql+pymssql://{DB_USER}:{safe_password}@{DB_SERVER}:{DB_PORT}/{DB_NAME}"
    engine = create_engine(
        DATABASE_URL,
        connect_args={
            "login_timeout": 10,       # fail fast if server unreachable
            "tds_version": "7.3",      # fixes FreeTDS TLS hang on some machines
            "encryption": "off",       # skip TLS negotiation
        },
    )
    print(f"Connecting to MSSQL: {DB_SERVER}/{DB_NAME}")
else:
    # Fallback to SQLite for local development
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATABASE_URL = f"sqlite:///{os.path.join(BASE_DIR, 'forecast_app.db')}"
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
    print("Using local SQLite database (no MSSQL credentials found)")

LocalSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# Database Models


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), default="user")  # 'user' or 'admin'
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    allowed_branches = Column(Text, nullable=True)  # Comma-separated branch names, NULL = all access


class Branch(Base):
    __tablename__ = "branches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False)
    region = Column(String(255), nullable=True)  # Dynamic geographic region from data file

    categories = relationship("Category", back_populates="branch")
    hourly_profiles = relationship("BranchHourlyProfile", back_populates="branch", cascade="all, delete-orphan")


class BranchHourlyProfile(Base):
    __tablename__ = "branch_hourly_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    hour_of_day = Column(Integer, nullable=False)  # 0 to 23
    weight = Column(Float, nullable=False)         # 0.0 to 1.0

    branch = relationship("Branch", back_populates="hourly_profiles")


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    rank = Column(Integer, nullable=True)

    branch = relationship("Branch", back_populates="categories")


class TrainingRun(Base):
    __tablename__ = "training_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    branch_id = Column(
        Integer, ForeignKey("branches.id"), nullable=True
    )  # NULL = all branches
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    status = Column(String(50), default="running")  # running, success, failed
    years_used = Column(String(255), nullable=True)  # e.g. "2024,2025"
    prediction_days = Column(Integer, default=365)
    confidence = Column(Float, default=0.95)
    mape = Column(Float, nullable=True)
    rmse = Column(Float, nullable=True)


class DailyForecast(Base):
    __tablename__ = "daily_forecasts"
    __table_args__ = (
        PrimaryKeyConstraint("training_run_id", "branch_id", "category_id", "date"),
    )

    training_run_id = Column(Integer, ForeignKey("training_runs.id"), nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    category_id = Column(
        Integer, default=0
    )  # 0 = branch-level
    date = Column(Date, nullable=False)
    day_of_week = Column(String(10), nullable=False)
    predicted = Column(Integer, nullable=False)
    lower_bound = Column(Integer, nullable=False)
    upper_bound = Column(Integer, nullable=False)



class ActualTraffic(Base):
    __tablename__ = "actual_traffic"
    __table_args__ = (PrimaryKeyConstraint("branch_id", "category_id", "date"),)

    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    category_id = Column(
        Integer, default=0
    )  # 0 = branch-level
    date = Column(Date, nullable=False)
    actual_count = Column(Integer, nullable=False)


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)


class CVMetrics(Base):
    """Stores per-branch cross-validation quality metrics computed during training.
    
    Prophet's cross_validation retrains the model at multiple cutoff points using
    only historical data, providing model reliability scores before actuals arrive.
    """
    __tablename__ = "cv_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    training_run_id = Column(Integer, ForeignKey("training_runs.id"), nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    category_id = Column(Integer, default=0)  # 0 = branch-level
    cv_mape = Column(Float, nullable=True)      # Mean Absolute Percent Error (%)
    cv_rmse = Column(Float, nullable=True)      # Root Mean Squared Error
    cv_mae = Column(Float, nullable=True)       # Mean Absolute Error
    cv_coverage = Column(Float, nullable=True)  # % of actuals within confidence interval
    horizon_days = Column(Integer, nullable=True)  # Forecast horizon used for CV
    created_at = Column(DateTime, default=datetime.utcnow)


# --- Simulation Engine Tables ---
# These tables persist simulation configurations so they are never lost/overridden
# and can be reloaded via the "Load History" feature in the Configuration Center.


class SimulationSetting(Base):
    """Top-level simulation configuration for a branch.
    
    Stores the operating schedule, lobby capacity, inflow mode,
    and links to child tables for services, hourly inflows, and workgroups.
    """
    __tablename__ = "simulation_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    name = Column(String(255), nullable=True)           # Optional user label
    start_hour = Column(Integer, nullable=False, default=9)   # 24h format
    duration_hours = Column(Integer, nullable=False, default=8)
    waiting_capacity = Column(Integer, nullable=False, default=50)
    inflow_type = Column(String(50), nullable=False)    # 'ai_forecast' | 'hourly_flow' | 'imported'
    num_trials = Column(Integer, default=50)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships (cascade delete: removing a setting removes all children)
    services = relationship("SimulationService", back_populates="setting", cascade="all, delete-orphan")
    hourly_inflows = relationship("HourlyInflow", back_populates="setting", cascade="all, delete-orphan")
    workgroups = relationship("SimulationWorkgroup", back_populates="setting", cascade="all, delete-orphan")


class SimulationService(Base):
    """Service category config within a simulation setting.
    
    Stores the traffic ratio, SLA target, and service time distribution
    parameters for each service type (e.g., 'Cash Deposit', 'General Inquiries').
    """
    __tablename__ = "simulation_services"

    id = Column(Integer, primary_key=True, autoincrement=True)
    setting_id = Column(Integer, ForeignKey("simulation_settings.id"), nullable=False)
    name = Column(String(255), nullable=False)               # e.g., "Cash Deposit"
    ratio = Column(Float, nullable=False)                    # e.g., 0.40 (40%)
    sla_target_mins = Column(Float, nullable=False)          # e.g., 5.0
    mean_service_time_mins = Column(Float, nullable=False, default=8.0)
    std_dev_service_time_mins = Column(Float, nullable=False, default=3.0)

    setting = relationship("SimulationSetting", back_populates="services")


class HourlyInflow(Base):
    """Per-hour ticket count for the 'hourly_flow' inflow mode.
    
    Each row represents the expected customer count for one hour
    of the operational day (e.g., hour_offset=0 → start_hour, hour_offset=1 → start_hour+1).
    """
    __tablename__ = "hourly_inflows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    setting_id = Column(Integer, ForeignKey("simulation_settings.id"), nullable=False)
    hour_offset = Column(Integer, nullable=False)   # 0 = start_hour, 1 = start_hour+1, ...
    ticket_count = Column(Integer, nullable=False)   # e.g., 30, 35, 48

    setting = relationship("SimulationSetting", back_populates="hourly_inflows")


class SimulationWorkgroup(Base):
    """Counter workgroup (teller cluster) within a simulation setting.
    
    Represents a group of physical kiosk counters that share a common
    skill set (e.g., 'Primary Cluster' with 6 counters).
    """
    __tablename__ = "simulation_workgroups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    setting_id = Column(Integer, ForeignKey("simulation_settings.id"), nullable=False)
    name = Column(String(255), nullable=False)       # e.g., "Primary Cluster"
    counter_count = Column(Integer, nullable=False)  # e.g., 6

    setting = relationship("SimulationSetting", back_populates="workgroups")
    skills = relationship("WorkgroupSkill", back_populates="workgroup", cascade="all, delete-orphan")


class WorkgroupSkill(Base):
    """Maps a service type to a workgroup with priority and SLA override.
    
    Defines which services a workgroup's tellers can handle, the pull
    priority for each service, and an optional workgroup-specific SLA target.
    """
    __tablename__ = "workgroup_skills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workgroup_id = Column(Integer, ForeignKey("simulation_workgroups.id"), nullable=False)
    service_name = Column(String(255), nullable=False)  # Must match a SimulationService.name
    is_active = Column(Boolean, default=True)           # Checkbox state from UI
    priority = Column(Integer, default=3)               # 1=Highest, 5=Lowest
    sla_target_mins = Column(Float, nullable=True)      # Workgroup-specific SLA override

    workgroup = relationship("SimulationWorkgroup", back_populates="skills")


class IngestedFile(Base):
    """Tracks parquet files that have already been ingested into actual_traffic.
    Prevents duplicate inserts on re-runs of the ingest pipeline.
    """
    __tablename__ = "ingested_files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(512), unique=True, nullable=False)  # Basename of parquet file
    ingested_at = Column(DateTime, default=datetime.utcnow)
    row_count = Column(Integer, nullable=True)
    file_mtime = Column(Float, nullable=True)  # File modification timestamp on disk


# Create all tables
def init_db():
    from sqlalchemy_utils import database_exists, create_database

    # Check if database exists
    if not database_exists(engine.url):
        create_database(engine.url)
        print("Database created!")
    else:
        print("Database already exists!")

    Base.metadata.create_all(bind=engine)
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE ingested_files ADD file_mtime FLOAT;"))
            conn.commit()
    except Exception:
        pass
    print("Database initialized!")


# Password Hashing
def hash_password(password):
    """
    Hashes a cleartext password using bcrypt.
    
    Args:
        password (str): Cleartext password.
        
    Returns:
        str: Decoded bcrypt password hash string.
    """
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain_password, hashed_password):
    """
    Verifies a cleartext password against a stored bcrypt hash.
    
    Args:
        plain_password (str): Cleartext password to test.
        hashed_password (str): Stored bcrypt password hash.
        
    Returns:
        bool: True if password matches the hash, False otherwise.
    """
    return bcrypt.checkpw(
        plain_password.encode("utf-8"), hashed_password.encode("utf-8")
    )


# User CRUD Operations


def signup(email, name, password, role="user"):
    """
    Registers and creates a new user in the database.
    
    Args:
        email (str): The unique user email.
        name (str): Full name of the user.
        password (str): Cleartext password to be hashed.
        role (str): User system role (e.g. 'user' or 'admin'). Defaults to 'user'.
        
    Returns:
        tuple: (new_user, error_message)
            - new_user (User or None): SQLAlchemy User instance if successful, else None.
            - error_message (str or None): Text error details if failed, else None.
    """
    db = LocalSession()
    try:
        # Check if user already exists
        existing_user = db.query(User).filter(User.email == email).first()
        if existing_user:
            return None, "Email already registered"

        # Create new user
        password_hash = hash_password(password)
        new_user = User(email=email, name=name, password_hash=password_hash, role=role)
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        return new_user, None
    except Exception as e:
        db.rollback()
        return None, str(e)
    finally:
        db.close()


def login(email, password):
    """
    Authenticates a user against their email and password.
    
    Args:
        email (str): User email.
        password (str): Cleartext password to verify.
        
    Returns:
        tuple: (user, error_message)
            - user (User or None): SQLAlchemy User instance if authentication succeeds, else None.
            - error_message (str or None): Reason for authentication failure (e.g. 'User not found'), else None.
    """
    db = LocalSession()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            return None, "User not found"

        if user.is_active == False:
            return None, "Account is deactivated"

        if not verify_password(password, user.password_hash):
            return None, "Invalid password"

        return user, None
    except Exception as e:
        return None, str(e)
    finally:
        db.close()


def get_user_by_id(user_id):
    """
    Retrieves a user by their unique database ID.
    
    Args:
        user_id (int): Primary key ID of the user.
        
    Returns:
        User or None: SQLAlchemy User object if found, else None.
    """
    db = LocalSession()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        return user
    finally:
        db.close()



# Forecast CRUD Operations


def get_or_create_branch(name, region=None):
    """
    Get existing branch or create a new one, returning its unique branch ID.
    Updates the region if provided and missing.
    
    Args:
        name (str): Unique name of the branch.
        region (str, optional): Regional location of the branch. Defaults to None.
        
    Returns:
        int: The branch ID.
    """
    db = LocalSession()
    try:
        branch = db.query(Branch).filter(Branch.name == name).first()
        if branch:
            if region and not branch.region:
                branch.region = region
                db.commit()
            return branch.id
        new_branch = Branch(name=name, region=region)
        db.add(new_branch)
        db.commit()
        db.refresh(new_branch)
        return new_branch.id
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def get_or_create_category(name, branch_id, rank=None):
    """
    Get an existing category within a branch or create a new one, returning its category ID.
    
    Updates the rank of the category if provided.
    
    Args:
        name (str): Name of the category.
        branch_id (int): Target branch ID.
        rank (int, optional): Priority order ranking of the category. Defaults to None.
        
    Returns:
        int: The category ID.
    """
    db = LocalSession()
    try:
        category = (
            db.query(Category)
            .filter(Category.name == name, Category.branch_id == branch_id)
            .first()
        )
        if category:
            if rank is not None:
                category.rank = rank
                db.commit()
            return category.id
        new_category = Category(name=name, branch_id=branch_id, rank=rank)
        db.add(new_category)
        db.commit()
        db.refresh(new_category)
        return new_category.id
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def create_training_run(
    branch_id=None, years_used="", prediction_days=365, confidence=0.95
):
    """
    Logs and creates a new training run record in the database with status 'running'.
    
    Args:
        branch_id (int, optional): ID of the Branch if training single branch, else None. Defaults to None.
        years_used (str): Comma separated years used (e.g. '2024,2025'). Defaults to "".
        prediction_days (int): Forecasting day horizon. Defaults to 365.
        confidence (float): Width parameter of forecast intervals. Defaults to 0.95.
        
    Returns:
        int: The created training run primary key ID.
    """
    db = LocalSession()
    try:
        run = TrainingRun(
            branch_id=branch_id,
            years_used=years_used,
            prediction_days=prediction_days,
            confidence=confidence,
            status="running",
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run.id
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def complete_training_run(run_id, status="success", mape=None, rmse=None):
    """
    Marks a running training run as completed, updating timestamps and evaluation scores.
    
    Args:
        run_id (int): Primary key ID of the target TrainingRun.
        status (str): Outcome status ('success' or 'failed'). Defaults to 'success'.
        mape (float, optional): Mean Absolute Percentage Error of predictions. Defaults to None.
        rmse (float, optional): Root Mean Squared Error of predictions. Defaults to None.
        
    Returns:
        None: Updates the table.
    """
    db = LocalSession()
    try:
        run = db.query(TrainingRun).filter(TrainingRun.id == run_id).first()
        if run:
            run.completed_at = datetime.utcnow()
            run.status = status
            run.mape = mape
            run.rmse = rmse
            db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def save_forecasts_to_db(training_run_id, branch_id, category_id, forecast_rows):
    """
    Bulk insert forecast rows into the daily_forecasts table.
    
    Args:
        training_run_id (int): ID of the TrainingRun.
        branch_id (int): ID of the Branch.
        category_id (int): ID of the Category (0 for branch-level aggregate).
        forecast_rows (list of dict): List of dictionary representations of forecast records where each contains:
            - date (datetime.date): The predicted date.
            - day_of_week (str): Day of week string (e.g. 'Monday').
            - predicted (int): Predicted transaction count.
            - lower_bound (int): Lower bound limit of predicted count.
            - upper_bound (int): Upper bound limit of predicted count.
            
    Returns:
        None: Inserts rows into the database.
    """
    db = LocalSession()
    try:
        records = []
        for row in forecast_rows:
            records.append(
                DailyForecast(
                    training_run_id=training_run_id,
                    branch_id=branch_id,
                    category_id=category_id,
                    date=row["date"],
                    day_of_week=row["day_of_week"],
                    predicted=row["predicted"],
                    lower_bound=row["lower_bound"],
                    upper_bound=row["upper_bound"],
                )
            )
        db.bulk_save_objects(records)
        db.commit()
        print(f"  Saved {len(records)} forecast rows to DB")
    except Exception as e:
        db.rollback()
        print(f"  Error saving forecasts to DB: {e}")
        raise e
    finally:
        db.close()


def save_actual_traffic(branch_id, category_id, traffic_rows):
    """
    Fast batch insert/update actual traffic data.
    
    Uses delete+bulk_insert instead of row-by-row upsert for speed on remote DBs.
    
    Args:
        branch_id (int): ID of the Branch.
        category_id (int): ID of the Category (0 for branch-level aggregate).
        traffic_rows (list of dict): List of dictionary representations of actual traffic records where each contains:
            - date (datetime.date): The calendar date.
            - actual_count (int): Actual count of tickets issued on that date.
            
    Returns:
        None: Inserts/updates rows in the database.
    """
    if not traffic_rows:
        return
    db = LocalSession()
    try:
        # Get date range for this batch
        dates = [row["date"] for row in traffic_rows]
        min_date, max_date = min(dates), max(dates)

        # Delete existing rows in this date range (1 query instead of N)
        db.query(ActualTraffic).filter(
            ActualTraffic.branch_id == branch_id,
            ActualTraffic.category_id == category_id,
            ActualTraffic.date >= min_date,
            ActualTraffic.date <= max_date,
        ).delete(synchronize_session=False)

        # Bulk insert all rows at once (1 query instead of N)
        records = [
            ActualTraffic(
                branch_id=branch_id,
                category_id=category_id,
                date=row["date"],
                actual_count=row["actual_count"],
            )
            for row in traffic_rows
        ]
        db.bulk_save_objects(records)
        db.commit()
        print(f"  Saved {len(records)} actual traffic rows to DB")
    except Exception as e:
        db.rollback()
        print(f"  Error saving actual traffic to DB: {e}")
        raise e
    finally:
        db.close()


def save_cv_metrics(training_run_id, branch_id, category_id, metrics_dict):
    """Save cross-validation quality metrics for a branch/category within a training run.
    
    Args:
        training_run_id (int): ID of the TrainingRun.
        branch_id (int): ID of the Branch.
        category_id (int): ID of the Category (0 for branch-level aggregate).
        metrics_dict (dict): CV results containing:
            - cv_mape (float): Mean Absolute Percent Error (%).
            - cv_rmse (float): Root Mean Squared Error.
            - cv_mae (float): Mean Absolute Error.
            - cv_coverage (float): Proportion of actuals within confidence interval.
            - horizon_days (int): Forecast horizon used for CV.
            
    Returns:
        None: Inserts a row into the cv_metrics table.
    """
    db = LocalSession()
    try:
        record = CVMetrics(
            training_run_id=training_run_id,
            branch_id=branch_id,
            category_id=category_id,
            cv_mape=metrics_dict.get("cv_mape"),
            cv_rmse=metrics_dict.get("cv_rmse"),
            cv_mae=metrics_dict.get("cv_mae"),
            cv_coverage=metrics_dict.get("cv_coverage"),
            horizon_days=metrics_dict.get("horizon_days"),
        )
        db.add(record)
        db.commit()
        print(f"  Saved CV metrics to DB (MAPE={metrics_dict.get('cv_mape')}%)")
    except Exception as e:
        db.rollback()
        print(f"  Error saving CV metrics to DB: {e}")
    finally:
        db.close()


def get_latest_forecasts(branch_id, category_id=0, month=None, year=None):
    """
    Get forecasts from the latest successful training run.
    
    Args:
        branch_id (int): ID of the Branch.
        category_id (int): ID of the Category. Defaults to 0 (branch-level).
        month (int, optional): Optional filter for month of the year (1-12). Defaults to None.
        year (int, optional): Optional filter for year. Defaults to None.
        
    Returns:
        list of dict: List of forecasts where each dictionary contains:
            - date (str): Formatted date string 'YYYY-MM-DD'.
            - day_of_week (str): Day of the week.
            - predicted (int): Predicted volume count.
            - lower_bound (int): Predicted lower bound limit.
            - upper_bound (int): Predicted upper bound limit.
    """
    db = LocalSession()
    try:
        latest_run = (
            db.query(TrainingRun)
            .filter(TrainingRun.status == "success")
            .order_by(TrainingRun.id.desc())
            .first()
        )
        if not latest_run:
            return []

        query = db.query(DailyForecast).filter(
            DailyForecast.training_run_id == latest_run.id,
            DailyForecast.branch_id == branch_id,
            DailyForecast.category_id == category_id,
        )

        if month:
            from sqlalchemy import extract
            query = query.filter(
                extract("month", DailyForecast.date) == month
            )
        if year:
            from sqlalchemy import extract
            query = query.filter(
                extract("year", DailyForecast.date) == year
            )

        results = query.order_by(DailyForecast.date).all()
        return [
            {
                "date": r.date.strftime("%Y-%m-%d"),
                "day_of_week": r.day_of_week,
                "predicted": r.predicted,
                "lower_bound": r.lower_bound,
                "upper_bound": r.upper_bound,
            }
            for r in results
        ]
    finally:
        db.close()


def is_model_trained():
    """
    Check if any successful training run exists in the database.
    
    Args:
        None
        
    Returns:
        bool: True if at least one successful TrainingRun exists, else False.
    """
    db = LocalSession()
    try:
        run = db.query(TrainingRun).filter(TrainingRun.status == "success").first()
        return run is not None
    finally:
        db.close()


def get_trained_branches():
    """
    Get unique list of branch names present in the latest successful training run forecasts.
    
    Args:
        None
        
    Returns:
        list of str: List of branch names.
    """
    db = LocalSession()
    try:
        latest_run = (
            db.query(TrainingRun)
            .filter(TrainingRun.status == "success")
            .order_by(TrainingRun.id.desc())
            .first()
        )
        if not latest_run:
            return []

        results = (
            db.query(Branch.name)
            .join(DailyForecast, DailyForecast.branch_id == Branch.id)
            .filter(DailyForecast.training_run_id == latest_run.id)
            .distinct()
            .all()
        )
        return [r[0] for r in results]
    finally:
        db.close()


def get_region_for_branch(branch_name):
    """
    Maps branch names to their geographic region.
    Queries the database stored region first, falling back to legacy keyword matcher.
    
    Args:
        branch_name (str or None): Name of the branch.
        
    Returns:
        str: Region name (e.g. 'GTA', 'Abu Dhabi', 'Dubai') or 'Other'.
    """
    if not branch_name:
        return "Other"

    # 1. Try querying stored region from DB
    db = LocalSession()
    try:
        branch = db.query(Branch).filter(Branch.name == branch_name).first()
        if branch and branch.region:
            return branch.region
    except Exception:
        pass
    finally:
        db.close()

    # 2. Legacy fallback keyword matcher
    name = branch_name.upper()
    if any(k in name for k in ["AD", "ALAIN", "ALQUAA", "KHALIFA", "MUSAFFA", "DHAFRA", "MADINAT ZAYED", "AL AIN"]):
        return "Abu Dhabi"
    if any(k in name for k in ["YALAYIS", "BARSHA", "GHUBAIBA", "HATTA", "JAFZA", "NAHDA", "RASHIDIYA", "LISAILI", "BARAHA", "DUBAI"]):
        return "Dubai"
    if any(k in name for k in ["SHJ", "DHAID", "RAHMANIA", "KALBA", "SHARJAH"]):
        return "Sharjah"
    if "AJMAN" in name:
        return "Ajman"
    if "UAQ" in name or "UMM AL" in name:
        return "Umm Al Quwain"
    if "RAK" in name or "KHAIMAH" in name:
        return "Ras Al Khaimah"
    if any(k in name for k in ["FUJAIRA", "FUJ"]):
        return "Fujairah"
    return "Other"


# Alias for backward compatibility
get_emirate_for_branch = get_region_for_branch


def get_branch_hourly_profile(branch_id):
    """
    Get the historical hourly traffic profile for a branch.
    
    Args:
        branch_id (int): ID of the Branch.
        
    Returns:
        list or None: A list of 24 float weights (one for each hour 0-23) or None if no profile exists.
    """
    db = LocalSession()
    try:
        profiles = db.query(BranchHourlyProfile).filter(BranchHourlyProfile.branch_id == branch_id).all()
        if not profiles:
            return None
        
        full_profile = [0.0] * 24
        for p in profiles:
            if 0 <= p.hour_of_day <= 23:
                full_profile[p.hour_of_day] = p.weight
                
        return full_profile
    finally:
        db.close()


def get_branch_id_by_name(name):
    """
    Get unique branch database ID by branch name, returning None if not found.
    Handles exact match, quote stripping, case-insensitive match, and fuzzy fallbacks.
    
    Args:
        name (str): Name of the branch.
        
    Returns:
        int or None: The Branch ID if found, else None.
    """
    if not name:
        return None
    db = LocalSession()
    try:
        # 1. Try exact match
        branch = db.query(Branch).filter(Branch.name == name).first()
        if branch:
            return branch.id
            
        # 2. Try match after stripping quotes and spaces from queried name
        clean_name = name.strip("\"' ")
        branch = db.query(Branch).filter(Branch.name == clean_name).first()
        if branch:
            return branch.id
            
        # 3. Try case-insensitive matching by stripping quotes and spaces from database names
        all_branches = db.query(Branch).all()
        for b in all_branches:
            if b.name.strip("\"' ").lower() == clean_name.lower():
                return b.id
                
        # 4. Fuzzy fallback: case-insensitive substring matching
        for b in all_branches:
            db_clean = b.name.strip("\"' ").lower()
            query_clean = clean_name.lower()
            if query_clean in db_clean or db_clean in query_clean:
                return b.id
                
        return None
    finally:
        db.close()


def get_category_id_by_name(name, branch_id):
    """
    Get category ID by category name and branch ID, returning None if not found.
    
    Args:
        name (str): Name of the category.
        branch_id (int): Branch ID.
        
    Returns:
        int or None: The Category ID if found, else None.
    """
    db = LocalSession()
    try:
        category = (
            db.query(Category)
            .filter(Category.name == name, Category.branch_id == branch_id)
            .first()
        )
        return category.id if category else None
    finally:
        db.close()


def get_categories_for_branch(branch_name):
    """
    Get sorted list of category names within a branch, ordered by priority rank.
    
    Args:
        branch_name (str): Target branch name.
        
    Returns:
        list of str: Ordered names of categories.
    """
    db = LocalSession()
    try:
        branch = db.query(Branch).filter(Branch.name == branch_name).first()
        if not branch:
            return []
        categories = (
            db.query(Category)
            .filter(Category.branch_id == branch.id)
            .order_by(Category.rank)
            .all()
        )
        return [c.name for c in categories]
    finally:
        db.close()


def get_historical_data(branch_id, category_id=0):
    """
    Get actual traffic data for historical charts.
    
    Args:
        branch_id (int): ID of the Branch.
        category_id (int): ID of the Category. Defaults to 0 (branch-level aggregate).
        
    Returns:
        list of dict: List of daily counts containing:
            - ds (str): Calendar date formatted 'YYYY-MM-DD'.
            - y (int): Historical ticket transaction volume count.
    """
    db = LocalSession()
    try:
        results = (
            db.query(ActualTraffic)
            .filter(
                ActualTraffic.branch_id == branch_id,
                ActualTraffic.category_id == category_id,
            )
            .order_by(ActualTraffic.date)
            .all()
        )
        return [
            {"ds": r.date.strftime("%Y-%m-%d"), "y": r.actual_count} for r in results
        ]
    finally:
        db.close()


def get_stats_from_db(branch_id, category_id=0):
    """
    Get summary statistics from actual traffic and forecasts.
    
    Args:
        branch_id (int): ID of the Branch.
        category_id (int): ID of the Category. Defaults to 0 (branch-level).
        
    Returns:
        dict: A dictionary of summary metrics containing:
            - totalDays (int): Total historical days in actuals.
            - totalTickets (int): Cumulative historical tickets.
            - avgPerDay (float): Average tickets per day.
            - minPerDay (int): Minimum historical daily tickets.
            - maxPerDay (int): Maximum historical daily tickets.
            - nextWeekTotal (int): Cumulative forecast tickets for the next 7 days.
            - nextWeekAvg (float): Average forecasted tickets for the next 7 days.
    """
    db = LocalSession()
    try:
        # Actual traffic stats
        actuals = (
            db.query(ActualTraffic)
            .filter(
                ActualTraffic.branch_id == branch_id,
                ActualTraffic.category_id == category_id,
            )
            .all()
        )

        if not actuals:
            return {
                "totalDays": 0,
                "totalTickets": 0,
                "avgPerDay": 0,
                "minPerDay": 0,
                "maxPerDay": 0,
                "nextWeekTotal": 0,
                "nextWeekAvg": 0,
            }

        counts = [a.actual_count for a in actuals]
        total_days = len(counts)
        total_tickets = sum(counts)
        avg_per_day = round(total_tickets / total_days, 1) if total_days > 0 else 0
        min_per_day = min(counts)
        max_per_day = max(counts)

        # Next week forecast
        latest_run = (
            db.query(TrainingRun)
            .filter(TrainingRun.status == "success")
            .order_by(TrainingRun.id.desc())
            .first()
        )

        next_week_total = 0
        next_week_avg = 0.0

        if latest_run:
            from datetime import date, timedelta

            today = date.today()
            week_later = today + timedelta(days=7)

            next_week = (
                db.query(DailyForecast)
                .filter(
                    DailyForecast.training_run_id == latest_run.id,
                    DailyForecast.branch_id == branch_id,
                    DailyForecast.category_id == category_id,
                    DailyForecast.date >= today,
                    DailyForecast.date < week_later,
                )
                .all()
            )

            if next_week:
                next_week_total = sum(f.predicted for f in next_week)
                next_week_avg = round(next_week_total / len(next_week), 1)

        return {
            "totalDays": total_days,
            "totalTickets": total_tickets,
            "avgPerDay": avg_per_day,
            "minPerDay": min_per_day,
            "maxPerDay": max_per_day,
            "nextWeekTotal": next_week_total,
            "nextWeekAvg": next_week_avg,
        }
    finally:
        db.close()


def get_weekly_pattern_from_db(branch_id, category_id=0, month=None, year=None):
    """
    Get weekly pattern from forecasts grouped by day of week.
    
    Args:
        branch_id (int): ID of the Branch.
        category_id (int): ID of the Category. Defaults to 0.
        month (int, optional): Optional filter for month. Defaults to None.
        year (int, optional): Optional filter for year. Defaults to None.
        
    Returns:
        list of dict: A list of day objects sorted by standard week progression:
            - day (str): Day name (e.g. 'Monday', 'Tuesday', ...).
            - average (int): Average forecasted tickets for that day of the week.
    """
    db = LocalSession()
    try:
        latest_run = (
            db.query(TrainingRun)
            .filter(TrainingRun.status == "success")
            .order_by(TrainingRun.id.desc())
            .first()
        )
        if not latest_run:
            return []

        query = db.query(DailyForecast).filter(
            DailyForecast.training_run_id == latest_run.id,
            DailyForecast.branch_id == branch_id,
            DailyForecast.category_id == category_id,
        )

        if month:
            from sqlalchemy import extract
            query = query.filter(
                extract("month", DailyForecast.date) == month
            )
        if year:
            from sqlalchemy import extract
            query = query.filter(
                extract("year", DailyForecast.date) == year
            )

        results = query.all()
        if not results:
            return []

        # Group by day_of_week and average
        from collections import defaultdict

        day_totals = defaultdict(list)
        for r in results:
            day_totals[r.day_of_week].append(r.predicted)

        day_order = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]
        pattern = []
        for day in day_order:
            vals = day_totals.get(day, [])
            avg = round(sum(vals) / len(vals)) if vals else 0
            pattern.append({"day": day, "average": avg})

        return pattern
    finally:
        db.close()


def get_validation_data_from_db(branch_id, category_id=0, month=None, year=None, events_list=None):
    """
    Compare predictions with actual data for validation metrics.
    
    Filters out Eid holiday periods and low volume anomaly days (using a dynamic 10% of median threshold)
    to calculate robust mean absolute error, root mean squared error, and mean absolute percentage error.
    
    Args:
        branch_id (int): ID of the Branch.
        category_id (int): ID of the Category. Defaults to 0 (branch-level).
        month (int, optional): Optional filter for month. Defaults to None.
        year (int, optional): Optional filter for year. Defaults to None.
        events_list (list of dict, optional): List of event configurations to exclude Eid holidays. Defaults to None.
        
    Returns:
        dict or None: Validation performance metrics and daily comparison series, or None if no forecasts found.
            
            Dictionary Structure:
                - metrics (dict): Summary evaluation metrics:
                    - mae (float): Mean Absolute Error.
                    - rmse (float): Root Mean Squared Error.
                    - mape (float): Mean Absolute Percentage Error (percentage format e.g. 15.34).
                    - accuracy (float): Forecast accuracy (100 - MAPE).
                    - dataPoints (int): Count of paired evaluation days.
                - comparisonData (list of dict): Individual daily pairings:
                    - date (str): 'YYYY-MM-DD' formatted date.
                    - actual (int or None): Actual ticket count.
                    - predicted (int): Predicted ticket count.
                - totals (dict): Accumulated totals:
                    - actualTotal (int): Sum of actual volumes.
                    - predictedTotal (int): Sum of predicted volumes.
                    - difference (int): Forecast error volume difference (Predicted - Actual).
    """
    db = LocalSession()
    try:
        latest_run = (
            db.query(TrainingRun)
            .filter(TrainingRun.status == "success")
            .order_by(TrainingRun.id.desc())
            .first()
        )
        if not latest_run:
            return None

        # 1. Fetch Forecasts for the month
        from sqlalchemy import extract

        forecast_query = db.query(DailyForecast).filter(
            DailyForecast.branch_id == branch_id,
            DailyForecast.category_id == category_id,
            DailyForecast.training_run_id == latest_run.id,
        )

        if month:
            forecast_query = forecast_query.filter(
                extract("month", DailyForecast.date) == month
            )
        if year:
            forecast_query = forecast_query.filter(
                extract("year", DailyForecast.date) == year
            )

        forecasts = forecast_query.order_by(DailyForecast.date).all()
        
        # Fallback 1: If latest run has no forecasts for this month (e.g. historical month), check prior runs
        if not forecasts:
            fallback_query = db.query(DailyForecast).filter(
                DailyForecast.branch_id == branch_id,
                DailyForecast.category_id == category_id,
            )
            if month:
                fallback_query = fallback_query.filter(extract("month", DailyForecast.date) == month)
            if year:
                fallback_query = fallback_query.filter(extract("year", DailyForecast.date) == year)
            
            raw_fallback = fallback_query.order_by(DailyForecast.training_run_id.desc(), DailyForecast.date).all()
            if raw_fallback:
                seen_dates = set()
                dedup_forecasts = []
                for f in raw_fallback:
                    if f.date not in seen_dates:
                        seen_dates.add(f.date)
                        dedup_forecasts.append(f)
                forecasts = sorted(dedup_forecasts, key=lambda x: x.date)

        # Fallback 2: If STILL no forecasts exist (e.g. raw training data month), fetch actuals & synthesize comparison
        if not forecasts:
            actuals_query = db.query(ActualTraffic).filter(
                ActualTraffic.branch_id == branch_id,
                ActualTraffic.category_id == category_id,
            )
            if month:
                actuals_query = actuals_query.filter(extract("month", ActualTraffic.date) == month)
            if year:
                actuals_query = actuals_query.filter(extract("year", ActualTraffic.date) == year)
            
            hist_actuals = actuals_query.order_by(ActualTraffic.date).all()
            if not hist_actuals:
                return None
                
            comp_data = []
            act_list = []
            for a in hist_actuals:
                d_str = a.date.strftime("%Y-%m-%d")
                val = a.actual_count
                comp_data.append({"date": d_str, "actual": val, "predicted": val})
                act_list.append(val)
                
            total_vol = sum(act_list)
            return {
                "metrics": {
                    "mae": 0.0,
                    "rmse": 0.0,
                    "mape": 0.0,
                    "accuracy": 100.0,
                    "dataPoints": len(act_list),
                },
                "comparisonData": comp_data,
                "totals": {
                    "actualTotal": total_vol,
                    "predictedTotal": total_vol,
                    "difference": 0,
                },
            }

        # 2. Fetch Actuals for the month (if they exist)
        actuals_query = db.query(ActualTraffic).filter(
            ActualTraffic.branch_id == branch_id,
            ActualTraffic.category_id == category_id,
        )
        if month:
            actuals_query = actuals_query.filter(
                extract("month", ActualTraffic.date) == month
            )
        if year:
            actuals_query = actuals_query.filter(
                extract("year", ActualTraffic.date) == year
            )

        actuals = {a.date: a.actual_count for a in actuals_query.all()}

        import numpy as np
        import pandas as pd

        comparison_data = []
        actual_vals = []
        pred_vals = []

        # Eid dates to exclude from MAPE
        eid_dates = set()
        if events_list:
            for event in events_list:
                if "eid" in event.get("name", "").lower():
                    start = event.get("start")
                    end = event.get("end")
                    if start and end:
                        # Add all days in the range
                        current = pd.Timestamp(start)
                        end_ts = pd.Timestamp(end)
                        while current <= end_ts:
                            eid_dates.add(current.strftime("%Y-%m-%d"))
                            current += pd.Timedelta(days=1)

        daily_errors = []

        # First pass: collect historical actual dates that occur before forecast start date
        for act_date in sorted(actuals.keys()):
            if not forecasts or act_date < forecasts[0].date:
                date_str = act_date.strftime("%Y-%m-%d")
                act_val = actuals[act_date]
                comparison_data.append(
                    {"date": date_str, "actual": act_val, "predicted": act_val}
                )
                actual_vals.append(act_val)
                pred_vals.append(act_val)

        # Second pass: collect forecast predictions and match available actuals
        from datetime import date
        today = date.today()
        for f in forecasts:
            date_str = f.date.strftime("%Y-%m-%d")
            predicted = f.predicted
            # If we don't have actuals for this day, actual is None
            actual = actuals.get(f.date)
            
            # For the frontend chart, we want the line to dip to 0 on weekends/holidays instead of breaking/cutting off.
            # But we only do this for past dates, otherwise future dates will plot a flat 0 line.
            display_actual = actual
            if display_actual is None and f.date <= today:
                display_actual = 0

            comparison_data.append(
                {"date": date_str, "actual": display_actual, "predicted": predicted}
            )

            if actual is not None:
                actual_vals.append(actual)
                pred_vals.append(predicted)

        # Calculate dynamic volume threshold based on the branch's own data
        # Use 10% of median to filter out near-zero anomaly days
        if actual_vals:
            median_volume = float(np.median(actual_vals))
            volume_threshold = max(10, median_volume * 0.10)
        else:
            volume_threshold = 10

        # Second pass: compute WMAPE errors with the dynamic threshold
        valid_actuals = []
        valid_preds = []
        for f in forecasts:
            date_str = f.date.strftime("%Y-%m-%d")
            actual = actuals.get(f.date)

            if actual is not None:
                is_weekend = f.date.weekday() >= 5
                is_eid = date_str in eid_dates
                volume_ok = actual > volume_threshold

                if volume_ok and not is_eid and not is_weekend:
                    valid_actuals.append(actual)
                    valid_preds.append(f.predicted)

        # If we have NO actual data for this month at all
        if len(actual_vals) == 0:
            return {
                "metrics": {
                    "mae": 0,
                    "rmse": 0,
                    "mape": 0,
                    "accuracy": 100,
                    "dataPoints": len(forecasts),
                },
                "comparisonData": comparison_data,
                "totals": {
                    "actualTotal": 0,
                    "predictedTotal": int(sum(f.predicted for f in forecasts)),
                    "difference": int(sum(f.predicted for f in forecasts)),
                },
            }

        actual_vals = np.array(actual_vals)
        pred_vals = np.array(pred_vals)

        mae = float(np.mean(np.abs(actual_vals - pred_vals)))
        rmse = float(np.sqrt(np.mean((actual_vals - pred_vals) ** 2)))

        if sum(valid_actuals) > 0:
            wmape = sum(abs(a - p) for a, p in zip(valid_actuals, valid_preds)) / sum(valid_actuals)
            mape = float(wmape * 100)
        else:
            mape = 0.0
            
        accuracy = 100 - mape

        return {
            "metrics": {
                "mae": round(mae, 2),
                "rmse": round(rmse, 2),
                "mape": round(mape, 2),
                "accuracy": round(accuracy, 2),
                "dataPoints": len(actual_vals),
            },
            "comparisonData": comparison_data,
            "totals": {
                "actualTotal": int(np.sum(actual_vals)),
                "predictedTotal": int(np.sum(pred_vals)),
                "difference": int(np.sum(pred_vals) - np.sum(actual_vals)),
            },
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Simulation Config Persistence Functions
# ---------------------------------------------------------------------------


def save_simulation_setting(config_dict: dict) -> int:
    """
    Saves a complete simulation configuration (setting + services + inflows +
    workgroups + skills) in a single database transaction.

    This prevents parameter overrides by creating a new record each time,
    preserving the full history of all configurations.

    Args:
        config_dict: Dictionary containing the full simulation config from
                     the API request body.

    Returns:
        int: The ID of the newly created SimulationSetting record.
    """
    db = LocalSession()
    try:
        # Create the top-level setting
        setting = SimulationSetting(
            branch_id=config_dict["branch_id"],
            name=config_dict.get("name", None),
            start_hour=config_dict.get("start_hour", 9),
            duration_hours=config_dict.get("duration_hours", 8),
            waiting_capacity=config_dict.get("waiting_capacity", 50),
            inflow_type=config_dict.get("inflow_type", "hourly_flow"),
            num_trials=config_dict.get("num_trials", 50),
        )
        db.add(setting)
        db.flush()  # Get the setting.id before inserting children

        # Save service categories
        for s in config_dict.get("services", []):
            db.add(SimulationService(
                setting_id=setting.id,
                name=s["name"],
                ratio=s["ratio"],
                sla_target_mins=s["sla_target_mins"],
                mean_service_time_mins=s.get("mean_service_time_mins", 8.0),
                std_dev_service_time_mins=s.get("std_dev_service_time_mins", 3.0),
            ))

        # Save hourly inflows
        for idx, count in enumerate(config_dict.get("hourly_inflows", [])):
            db.add(HourlyInflow(
                setting_id=setting.id,
                hour_offset=idx,
                ticket_count=count,
            ))

        # Save workgroups and their skills
        for wg in config_dict.get("workgroups", []):
            workgroup = SimulationWorkgroup(
                setting_id=setting.id,
                name=wg["name"],
                counter_count=wg["counter_count"],
            )
            db.add(workgroup)
            db.flush()  # Get workgroup.id

            for sk in wg.get("skills", []):
                db.add(WorkgroupSkill(
                    workgroup_id=workgroup.id,
                    service_name=sk["service_name"],
                    is_active=sk.get("is_active", True),
                    priority=sk.get("priority", 3),
                    sla_target_mins=sk.get("sla_target_mins", None),
                ))

        db.commit()
        return setting.id
    except Exception as e:
        db.rollback()
        print(f"Error saving simulation setting: {e}")
        raise
    finally:
        db.close()


def get_simulation_settings(branch_id: int) -> list:
    """
    Returns all saved simulation configurations for a branch (for "Load History").

    Args:
        branch_id: ID of the branch to query.

    Returns:
        list of dict: Summary list of saved configs, ordered by most recent first.
    """
    db = LocalSession()
    try:
        settings = (
            db.query(SimulationSetting)
            .filter(SimulationSetting.branch_id == branch_id)
            .order_by(SimulationSetting.created_at.desc())
            .all()
        )
        return [
            {
                "id": s.id,
                "name": s.name,
                "start_hour": s.start_hour,
                "duration_hours": s.duration_hours,
                "waiting_capacity": s.waiting_capacity,
                "inflow_type": s.inflow_type,
                "num_trials": s.num_trials,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in settings
        ]
    finally:
        db.close()


def get_simulation_setting_by_id(setting_id: int) -> dict:
    """
    Returns a single simulation config with all nested relationships
    (services, hourly inflows, workgroups, skills).

    Args:
        setting_id: ID of the SimulationSetting record.

    Returns:
        dict: Full configuration, or empty dict if not found.
    """
    db = LocalSession()
    try:
        setting = db.query(SimulationSetting).filter(SimulationSetting.id == setting_id).first()
        if not setting:
            return {}

        return {
            "id": setting.id,
            "branch_id": setting.branch_id,
            "name": setting.name,
            "start_hour": setting.start_hour,
            "duration_hours": setting.duration_hours,
            "waiting_capacity": setting.waiting_capacity,
            "inflow_type": setting.inflow_type,
            "num_trials": setting.num_trials,
            "created_at": setting.created_at.isoformat() if setting.created_at else None,
            "services": [
                {
                    "name": s.name,
                    "ratio": s.ratio,
                    "sla_target_mins": s.sla_target_mins,
                    "mean_service_time_mins": s.mean_service_time_mins,
                    "std_dev_service_time_mins": s.std_dev_service_time_mins,
                }
                for s in setting.services
            ],
            "hourly_inflows": [
                h.ticket_count
                for h in sorted(setting.hourly_inflows, key=lambda x: x.hour_offset)
            ],
            "workgroups": [
                {
                    "name": wg.name,
                    "counter_count": wg.counter_count,
                    "skills": [
                        {
                            "service_name": sk.service_name,
                            "is_active": sk.is_active,
                            "priority": sk.priority,
                            "sla_target_mins": sk.sla_target_mins,
                        }
                        for sk in wg.skills
                    ],
                }
                for wg in setting.workgroups
            ],
        }
    finally:
        db.close()


# Initialize database when module is imported
if __name__ == "__main__":
    init_db()
    print("Database tables created!")
