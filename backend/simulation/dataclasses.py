import math
from dataclasses import dataclass, field
from typing import List, Optional

from simulation.math_utils import get_lognormal_params
# Data Classes


@dataclass
class Ticket:
    """Represents a customer ticket in the central lobby queue."""

    id: int
    service_name: str
    arrival_time: float  # Simulation time in minutes
    priority: int  # Lower number = higher priority
    kiosk_priority: int = 3  # Kiosk-level ticket priority (1 = Highest, 5 = Lowest)


@dataclass
class ServiceConfig:
    """Configuration for a single service category (e.g., 'Cash Deposit')."""

    name: str
    ratio: float  # Fraction of total traffic (e.g., 0.40)
    sla_target_mins: float  # Service-level SLA target (e.g., 5 minutes)
    mean_service_time_mins: float  # Mean service duration in minutes
    std_dev_service_time_mins: float  # Std dev of service duration

    # Computed lognormal parameters (set in __post_init__)
    lognormal_mu: float = field(init=False, default=0.0)
    lognormal_sigma: float = field(init=False, default=0.0)

    def __post_init__(self):
        self.lognormal_mu, self.lognormal_sigma = get_lognormal_params(
            self.mean_service_time_mins, self.std_dev_service_time_mins
        )


@dataclass
class WorkgroupSkillConfig:
    """Defines a single skill (service capability) within a workgroup."""

    service_name: str
    is_active: bool  # Whether this skill is enabled (checkbox in UI)
    priority: int  # 1 = Highest, 5 = Lowest
    sla_target_mins: float  # Workgroup-specific SLA override


@dataclass
class WorkgroupConfig:
    """Configuration for a counter workgroup (e.g., 'Primary Cluster')."""

    name: str
    counter_count: int  # Number of physical teller counters
    skills: List[WorkgroupSkillConfig]  # Services this workgroup can handle


@dataclass
class CallingProfileCondition:
    """Condition under which a category becomes eligible in an Overflow profile."""
    max_wait_time: float  # Wait time threshold in minutes
    ticket_priority: bool = False  # If True, use kiosk ticket priority


@dataclass
class CallingProfileOrderItem:
    """A single category configuration within a profile's calling order list."""
    category: str  # The service category name
    condition: Optional[CallingProfileCondition] = None
    count: int = 1


@dataclass
class CallingProfile:
    """A configuration for a QMS Calling Profile (FIFO or Overflow)."""
    id: int
    name: str
    type: str  # 'FIFO' or 'Overflow'
    default_category: str  # Fallback category name when no active queues match
    order: List[List[CallingProfileOrderItem]]  # Nested priority levels


@dataclass
class TellerCounterConfig:
    """Config for a single physical counter with assigned profiles."""
    name: str
    counter_id: int
    operator_name: Optional[str] = None
    counter_profiles: List[int] = field(default_factory=list)
    operator_profiles: List[int] = field(default_factory=list)


@dataclass
class SimulationConfig:
    """Complete configuration for a single simulation run."""

    start_hour: int  # Operating day start (24h format, e.g., 9)
    duration_hours: int  # Length of simulated day (e.g., 8)
    waiting_capacity: int  # Max lobby queue size before balking
    hourly_inflows: List[int]  # Expected ticket counts per hour
    services: List[ServiceConfig]  # Service category definitions
    workgroups: List[WorkgroupConfig]  # Workgroup (teller cluster) definitions

    # Optional fields for QMS Calling Profiles
    calling_profiles: Optional[List[CallingProfile]] = None
    counters: Optional[List[TellerCounterConfig]] = None
    resolution_mode: Optional[str] = "hybrid"  # 'operator', 'counter', or 'hybrid'
    category_max_wait_times: Optional[dict] = None  # Category Name -> default MaxWaitTime from category master


@dataclass
class TicketMetrics:
    """Recorded metrics for a single served ticket."""

    ticket_id: int
    service_name: str
    arrival_time: float
    service_start_time: float
    service_end_time: float
    wait_time: float
    service_duration: float
    workgroup_name: str
    sla_target: float
    sla_breached: bool
    hour_of_arrival: int


# Core Simulation Engine
