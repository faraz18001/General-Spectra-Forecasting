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
    services: List[SimulationServiceRequest]
    workgroups: List[WorkgroupRequest]
    save_config: bool = False           # Whether to persist config to DB
    forecast_total: Optional[int] = None  # For AI Forecast mode


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
