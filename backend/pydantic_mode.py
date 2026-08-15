from typing import List, Optional
from pydantic import BaseModel

"""pydantic models for forecast api"""


class ForecastRow(BaseModel):
    date: str
    day: str
    predicted: int
    lower: int
    upper: int
    isWeekend: bool


class HistoricalRow(BaseModel):
    date: str
    day: str
    tickets: int


class Stats(BaseModel):
    totalDays: int
    totalTickets: int
    avgPerDay: float
    minPerDay: int
    maxPerDay: int
    nextWeekTotal: int
    nextWeekAvg: float


class WeeklyPatternRow(BaseModel):
    day: str
    average: int


# Event Management Models
class EventItem(BaseModel):
    name: str
    start: str
    end: str
    impact: str
    notes: Optional[str] = ""


class EventCreate(BaseModel):
    name: str
    start: str
    end: str
    impact: str
    notes: Optional[str] = ""


class TrainRequest(BaseModel):
    years: List[int]
    events: List[EventItem]
    branch_name: Optional[str] = None
    category_name: Optional[str] = None
    prediction_days: int = 365
    confidence: float = 0.95  # Confidence interval (0.80 to 0.99)


class TrainResponse(BaseModel):
    success: bool
    message: str
    training_days: Optional[int] = None
    events_count: Optional[int] = None
    forecast_days: Optional[int] = None
    prediction_year: Optional[int] = None


# --- Simulation Engine Models ---


class WorkgroupSkillRequest(BaseModel):
    """Skill mapping within a counter workgroup."""
    service_name: str
    is_active: bool = True
    priority: int = 3           # 1=Highest, 5=Lowest
    sla_target_mins: float = 15.0


class WorkgroupRequest(BaseModel):
    """Counter workgroup (teller cluster) configuration."""
    name: str
    counter_count: int
    skills: List[WorkgroupSkillRequest]


class SimulationServiceRequest(BaseModel):
    """Service category with traffic ratio and SLA target."""
    name: str
    ratio: float                        # e.g., 0.40 (40%)
    sla_target_mins: float              # e.g., 5.0
    mean_service_time_mins: float = 8.0
    std_dev_service_time_mins: float = 3.0


class CallingProfileConditionRequest(BaseModel):
    """Condition configuration for Overflow profile queue eligibility."""
    max_wait_time: float = 10.0
    ticket_priority: bool = False


class CallingProfileOrderItemRequest(BaseModel):
    """Refers to a single category and its conditions inside a profile's calling order list."""
    category: str
    condition: Optional[CallingProfileConditionRequest] = None
    count: int = 1


class CallingProfileRequest(BaseModel):
    """Calling Profile configuration schema."""
    id: Optional[int] = None
    profile_id: Optional[int] = None    # Support both "id" and "profile_id"
    name: Optional[str] = None
    type: str = "FIFO"                  # "FIFO" or "Overflow"
    default_category: str
    order: List[List[CallingProfileOrderItemRequest]] = []


class CounterRequest(BaseModel):
    """Counter configuration schema for spawning tellers."""
    name: Optional[str] = None
    counter_id: int
    operator_name: Optional[str] = None
    counter_profiles: List[int] = []
    operator_profiles: List[int] = []


class SimulationRequest(BaseModel):
    """Full simulation configuration from the Configuration Center UI."""
    branch_id: int = 0
    branch_name: Optional[str] = None    # Frontend passes name, backend resolves ID
    start_hour: int = 9
    duration_hours: int = 8
    waiting_capacity: int = 50
    inflow_type: str = "hourly_flow"    # 'ai_forecast' | 'hourly_flow' | 'imported'
    hourly_inflows: List[int] = []
    num_trials: int = 50
    services: List[SimulationServiceRequest] = []
    workgroups: List[WorkgroupRequest] = []
    save_config: bool = False           # Whether to persist config to DB
    forecast_total: Optional[int] = None  # For AI Forecast mode

    # QMS Calling Profiles support
    calling_profiles: Optional[List[CallingProfileRequest]] = None
    counters: Optional[List[CounterRequest]] = None
    resolution_mode: Optional[str] = "hybrid"  # "operator" | "counter" | "hybrid"
    category_max_wait_times: Optional[dict] = None


class AgentChatRequest(BaseModel):
    """Schema for LLM agent conversation request."""
    message: str
    session_id: str
    branch_name: Optional[str] = None
    month: Optional[int] = 1
    year: Optional[int] = 2026


class AgentChatResponse(BaseModel):
    """Schema for LLM agent conversation response."""
    response: str
    session_id: str
