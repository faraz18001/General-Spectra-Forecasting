"""
Operational Simulation Engine (Discrete Event Simulation)

Uses SimPy to model branch-level teller queue operations with:
- Poisson arrivals (exponential inter-arrival times)
- Log-normal service times
- Skill-based routing (server-pull model)
- Lobby capacity limits (customer balking)
- Monte Carlo averaging across multiple trials

This module is self-contained — no FastAPI or database imports.
"""

from simulation.math_utils import get_lognormal_params, DEFAULT_HOURLY_PROFILE
from simulation.dataclasses import (
    Ticket,
    ServiceConfig,
    WorkgroupSkillConfig,
    WorkgroupConfig,
    CallingProfileCondition,
    CallingProfileOrderItem,
    CallingProfile,
    TellerCounterConfig,
    SimulationConfig,
    TicketMetrics,
)
from simulation.engine import BranchSimulator
from simulation.builder import build_config_from_request
from simulation.averaging import _average_results
from simulation.runner import run_simulation

__all__ = [
    "get_lognormal_params",
    "DEFAULT_HOURLY_PROFILE",
    "Ticket",
    "ServiceConfig",
    "WorkgroupSkillConfig",
    "WorkgroupConfig",
    "CallingProfileCondition",
    "CallingProfileOrderItem",
    "CallingProfile",
    "TellerCounterConfig",
    "SimulationConfig",
    "TicketMetrics",
    "BranchSimulator",
    "build_config_from_request",
    "_average_results",
    "run_simulation",
]
