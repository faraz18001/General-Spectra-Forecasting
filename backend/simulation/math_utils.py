import math
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

import math
import random
from dataclasses import dataclass, field
from typing import List, Optional

import simpy
try:
    from logger_setup import log_print as print
except ImportError:
    pass

# Default hourly traffic profile for government branches (9 hours: 8AM-5PM).
# Represents the fraction of daily traffic expected in each hour.
# Pattern: slow start, mid-morning peak, lunch dip, afternoon taper.
DEFAULT_HOURLY_PROFILE = [0.05, 0.08, 0.12, 0.15, 0.18, 0.15, 0.12, 0.10, 0.05]


# Math Bridge: Real-world mean/std_dev → Lognormal μ, σ


def get_lognormal_params(mean: float, std_dev: float) -> tuple:
    """
    Converts real-world mean and standard deviation into the μ and σ
    parameters of the underlying normal distribution for lognormal sampling.

    The lognormal distribution X = e^Y where Y ~ Normal(μ, σ²) has:
        Variance(X) = (e^(σ²) - 1) * Mean(X)²
    Solving for σ and μ:
        σ² = ln(1 + variance / mean²)
        μ  = ln(mean) - σ² / 2

    Args:
        mean: Desired real-world mean (e.g., 10 minutes).
        std_dev: Desired real-world standard deviation (e.g., 3 minutes).

    Returns:
        tuple: (mu, sigma) parameters for random.lognormvariate().
    """
    variance = std_dev**2
    sigma2 = math.log(variance / (mean**2) + 1)
    sigma = math.sqrt(sigma2)
    mu = math.log(mean) - sigma2 / 2
    return mu, sigma
