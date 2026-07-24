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
from typing import Dict, List, Optional

import simpy

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


# Data Classes


@dataclass
class Ticket:
    """Represents a customer ticket in the central lobby queue."""

    id: int
    service_name: str
    arrival_time: float  # Simulation time in minutes
    priority: int  # Lower number = higher priority


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
class SimulationConfig:
    """Complete configuration for a single simulation run."""

    start_hour: int  # Operating day start (24h format, e.g., 9)
    duration_hours: int  # Length of simulated day (e.g., 8)
    waiting_capacity: int  # Max lobby queue size before balking
    hourly_inflows: List[int]  # Expected ticket counts per hour
    services: List[ServiceConfig]  # Service category definitions
    workgroups: List[WorkgroupConfig]  # Workgroup (teller cluster) definitions


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


class BranchSimulator:
    """
    Discrete Event Simulation engine for a single branch operating day.

    Models:
    - Central lobby queue with capacity limits (balking).
    - Multiple workgroups (teller clusters) with skill-based routing.
    - Server-pull model: idle tellers pull from queue by priority then FIFO.
    - Poisson arrivals with per-hour rates.
    - Lognormal service durations per service category.
    """

    def __init__(self, env: simpy.Environment, config: SimulationConfig):
        self.env = env
        self.config = config

        # Central lobby queue — shared across all workgroups
        self.lobby_queue: List[Ticket] = []

        # SimPy event to signal idle tellers when a new ticket arrives.
        # Multiple tellers can wait on the same event; when triggered,
        # all wake up and compete for tickets (cooperative, no race condition).
        self.new_ticket_event = env.event()

        # Service lookup tables (simplified from comprehensions)
        self.services = {}
        for s in config.services:
            self.services[s.name] = s

        self.service_names = []
        for s in config.services:
            self.service_names.append(s.name)

        self.service_weights = []
        for s in config.services:
            self.service_weights.append(s.ratio)

        # Metrics collectors
        self.served_tickets: List[TicketMetrics] = []
        self.balked_count: int = 0
        self.ticket_counter: int = 0

        self.workgroup_busy_time = {}
        for wg in config.workgroups:
            self.workgroup_busy_time[wg.name] = 0.0

        # Per-hour statistics
        self.hourly_stats = {}
        for h in range(config.duration_hours):
            self.hourly_stats[h] = {
                "arrivals": 0,
                "balked": 0,
                "wait_times": [],
                "max_queue_length": 0,
            }

        # Timeline snapshots for replay animation (captured every simulated minute)
        self.timeline_snapshots: List[dict] = []

        # Track active tellers (how many are currently serving a customer)
        self.active_tellers: int = 0
        self.total_tellers: int = 0
        for wg in config.workgroups:
            self.total_tellers += wg.counter_count

    # -------------------------------------------------------------------
    # Helper Methods
    # -------------------------------------------------------------------

    def _get_hour_offset(self, sim_time: float) -> int:
        """Converts simulation time (minutes) to hour offset index."""
        return min(int(sim_time / 60.0), self.config.duration_hours - 1)

    def _update_max_queue_length(self):
        """Tracks peak queue length per hour for reporting."""
        hour = self._get_hour_offset(self.env.now)
        current_length = len(self.lobby_queue)
        if current_length > self.hourly_stats[hour]["max_queue_length"]:
            self.hourly_stats[hour]["max_queue_length"] = current_length

    # -------------------------------------------------------------------
    # SimPy Process: Customer Arrival Generator (Poisson Process)
    # -------------------------------------------------------------------

    def customer_generator(self):
        """
        Generates customers arriving according to a Poisson process.

        For each hour of operation:
        1. Compute arrival rate λ = hourly_ticket_count / 60 (arrivals/min).
        2. Sample inter-arrival times from Exponential(λ) distribution.
        3. Assign each customer a random service type based on weighted ratios.
        4. Check lobby capacity — balk (turn away) if full.
        5. Signal idle tellers that a new ticket is available.
        """
        for hour_offset in range(self.config.duration_hours):
            hourly_count = self.config.hourly_inflows[hour_offset]

            # Skip hours with zero expected traffic
            if hourly_count <= 0:
                yield self.env.timeout(60.0)
                continue

            arrival_rate_per_min = hourly_count / 60.0
            hour_end = (hour_offset + 1) * 60.0

            while self.env.now < hour_end:
                # Exponential inter-arrival time (Poisson process)
                inter_arrival = random.expovariate(arrival_rate_per_min)
                yield self.env.timeout(inter_arrival)

                # Guard: don't generate customers past the hour boundary
                if self.env.now >= hour_end:
                    break

                self.ticket_counter += 1
                current_hour = self._get_hour_offset(self.env.now)
                self.hourly_stats[current_hour]["arrivals"] += 1

                # Assign service type by weighted random selection
                service_name = random.choices(
                    self.service_names, weights=self.service_weights, k=1
                )[0]

                # --- Lobby Capacity Check (Balking) ---
                if len(self.lobby_queue) >= self.config.waiting_capacity:
                    self.balked_count += 1
                    self.hourly_stats[current_hour]["balked"] += 1
                    continue

                # Determine ticket priority from workgroup skill configs
                best_priority = 999
                for wg in self.config.workgroups:
                    for skill in wg.skills:
                        if skill.service_name == service_name and skill.is_active:
                            if skill.priority < best_priority:
                                best_priority = skill.priority

                ticket = Ticket(
                    id=self.ticket_counter,
                    service_name=service_name,
                    arrival_time=self.env.now,
                    priority=best_priority,
                )

                self.lobby_queue.append(ticket)
                self._update_max_queue_length()

                # Wake up any idle tellers waiting for customers
                if not self.new_ticket_event.triggered:
                    self.new_ticket_event.succeed()

    # -------------------------------------------------------------------
    # SimPy Process: Teller (Server-Pull with Skill-Based Routing)
    # -------------------------------------------------------------------

    def get_best_ticket_for_workgroup(
        self, workgroup: WorkgroupConfig
    ) -> Optional[Ticket]:
        """
        Scans the central lobby queue and returns the best eligible ticket
        for this workgroup, using the server-pull routing algorithm:

        1. FILTER: Only consider tickets whose service type is active
           in this workgroup's skill checklist.
        2. SORT by priority (ascending — lower number = higher priority).
        3. SORT by arrival time (ascending — longest waiting first / FIFO).

        Removes and returns the selected ticket from the lobby queue.
        Returns None if no eligible tickets are available.
        """
        # Simplified from comprehension
        active_skills = {}
        for skill in workgroup.skills:
            if skill.is_active:
                active_skills[skill.service_name] = skill

        candidates = []
        for ticket in self.lobby_queue:
            if ticket.service_name in active_skills:
                skill = active_skills[ticket.service_name]
                candidates.append((skill.priority, ticket.arrival_time, ticket))

        if not candidates:
            return None

        # Sort: priority ASC (highest first), then arrival_time ASC (FIFO)
        candidates.sort(key=lambda x: (x[0], x[1]))
        best_ticket = candidates[0][2]

        self.lobby_queue.remove(best_ticket)
        return best_ticket

    def teller_process(self, workgroup: WorkgroupConfig, teller_id: int):
        """
        SimPy process for a single teller counter within a workgroup.

        Loops indefinitely:
        1. Attempt to pull the best eligible ticket from the lobby queue.
        2. If no eligible ticket, wait for the new_ticket_event signal.
        3. If ticket found, serve it with a lognormal service duration.
        4. Record all metrics (wait time, SLA breach, utilization).
        """
        wg_name = workgroup.name

        # Simplified from comprehension
        active_skills = {}
        for skill in workgroup.skills:
            if skill.is_active:
                active_skills[skill.service_name] = skill

        while True:
            ticket = self.get_best_ticket_for_workgroup(workgroup)

            if ticket is None:
                # No eligible tickets — wait for a new arrival signal.
                # If the event was already triggered (stale), create a fresh one.
                if self.new_ticket_event.triggered:
                    self.new_ticket_event = self.env.event()
                yield self.new_ticket_event
                continue

            # --- Serve the customer ---
            service_start = self.env.now
            wait_time = service_start - ticket.arrival_time

            # Sample service duration from lognormal distribution.
            # Floor at 0.5 min to prevent near-zero service times.
            svc = self.services[ticket.service_name]
            service_duration = max(
                0.5, random.lognormvariate(svc.lognormal_mu, svc.lognormal_sigma)
            )

            self.active_tellers += 1
            yield self.env.timeout(service_duration)
            self.active_tellers -= 1

            service_end = self.env.now
            self.workgroup_busy_time[wg_name] += service_duration

            # Determine SLA target (workgroup-level override or service-level default)
            # Replaced conditional expression
            skill = active_skills.get(ticket.service_name)
            if skill is not None and skill.sla_target_mins is not None:
                sla_target = skill.sla_target_mins
            else:
                sla_target = svc.sla_target_mins

            hour_of_arrival = self._get_hour_offset(ticket.arrival_time)
            self.hourly_stats[hour_of_arrival]["wait_times"].append(wait_time)

            self.served_tickets.append(
                TicketMetrics(
                    ticket_id=ticket.id,
                    service_name=ticket.service_name,
                    arrival_time=ticket.arrival_time,
                    service_start_time=service_start,
                    service_end_time=service_end,
                    wait_time=wait_time,
                    service_duration=service_duration,
                    workgroup_name=wg_name,
                    sla_target=sla_target,
                    sla_breached=wait_time > sla_target,
                    hour_of_arrival=hour_of_arrival,
                )
            )

    # -------------------------------------------------------------------
    # SimPy Process: Timeline Monitor (Passive Observer)
    # -------------------------------------------------------------------

    def timeline_monitor(self):
        """
        Passive observer process that wakes up every simulated minute
        and records a snapshot of the current simulation state.

        Captures:
        - Queue depth (total and per-service)
        - Active teller count and utilization
        - Cumulative served tickets and SLA compliance
        - Cumulative balked count
        - Arrival count up to this point

        This data powers the frontend replay animation (Approach 2).
        """
        total_minutes = self.config.duration_hours * 60.0

        while self.env.now < total_minutes:
            # Count queue depth per service type
            queue_by_service = {}
            for svc_name in self.service_names:
                queue_by_service[svc_name] = 0
            for ticket in self.lobby_queue:
                if ticket.service_name in queue_by_service:
                    queue_by_service[ticket.service_name] += 1

            total_queue = len(self.lobby_queue)

            # Compute cumulative SLA from served tickets so far
            served_so_far = len(self.served_tickets)
            sla_compliant = 0
            total_wait = 0.0
            for t in self.served_tickets:
                total_wait += t.wait_time
                if not t.sla_breached:
                    sla_compliant += 1

            if served_so_far > 0:
                cumulative_sla = round((sla_compliant / served_so_far) * 100.0, 1)
                avg_wait = round(total_wait / served_so_far, 2)
            else:
                cumulative_sla = 100.0
                avg_wait = 0.0

            # Compute simulated clock time
            clock_minutes = self.config.start_hour * 60 + self.env.now
            clock_hour = int(clock_minutes // 60)
            clock_min = int(clock_minutes % 60)

            snapshot = {
                "minute": round(self.env.now, 1),
                "clock": "{:02d}:{:02d}".format(clock_hour, clock_min),
                "queue_total": total_queue,
                "queue_by_service": queue_by_service,
                "active_tellers": self.active_tellers,
                "total_tellers": self.total_tellers,
                "utilization": round((self.active_tellers / max(self.total_tellers, 1)) * 100.0, 1),
                "cumulative_served": served_so_far,
                "cumulative_sla": cumulative_sla,
                "cumulative_balked": self.balked_count,
                "cumulative_arrived": self.ticket_counter,
                "avg_wait": avg_wait,
            }

            self.timeline_snapshots.append(snapshot)

            # Sleep for 1 simulated minute
            yield self.env.timeout(1.0)

    # -------------------------------------------------------------------
    # Run Simulation & Compile Metrics
    # -------------------------------------------------------------------

    def run(self) -> dict:
        """
        Executes the full simulation for one operational day.

        Starts the customer generator, all teller processes, and the
        timeline monitor, then runs the SimPy environment for the
        configured duration.

        Returns:
            dict: Raw metrics for this single trial run.
        """
        # Start the customer arrival generator
        self.env.process(self.customer_generator())

        # Start teller processes for each counter in each workgroup
        for wg in self.config.workgroups:
            for i in range(wg.counter_count):
                self.env.process(self.teller_process(wg, i + 1))

        # Start the timeline monitor (passive observer for replay)
        self.env.process(self.timeline_monitor())

        # Run simulation for the full operational day
        total_minutes = self.config.duration_hours * 60.0
        self.env.run(until=total_minutes)

        return self._compile_metrics()

    def _compile_metrics(self) -> dict:
        """Compiles all collected metrics into a structured summary dict."""
        total_served = len(self.served_tickets)
        total_arrived = total_served + self.balked_count
        total_minutes = self.config.duration_hours * 60.0

        if total_served == 0:
            return {
                "total_arrived": total_arrived,
                "total_served": 0,
                "total_balked": self.balked_count,
                "avg_wait": 0.0,
                "max_wait": 0.0,
                "overall_sla_compliance": 100.0,
                "avg_utilization": 0.0,
                "per_service": [],
                "per_workgroup": [],
                "hourly": self._compile_hourly_breakdown(),
                "timeline_snapshots": self.timeline_snapshots,
            }

        # Replaced list comprehension
        wait_times = []
        for t in self.served_tickets:
            wait_times.append(t.wait_time)

        avg_wait = sum(wait_times) / total_served
        max_wait = max(wait_times)

        # Replaced generator expression
        sla_compliant = 0
        for t in self.served_tickets:
            if not t.sla_breached:
                sla_compliant += 1

        overall_sla = (sla_compliant / total_served) * 100.0

        # Teller utilization: (total busy time) / (total available teller-minutes)
        # Replaced generator expression
        total_tellers = 0
        for wg in self.config.workgroups:
            total_tellers += wg.counter_count

        total_available = total_tellers * total_minutes
        total_busy = sum(self.workgroup_busy_time.values())

        # Replaced conditional expression
        if total_available > 0:
            avg_utilization = (total_busy / total_available) * 100.0
        else:
            avg_utilization = 0.0

        return {
            "total_arrived": total_arrived,
            "total_served": total_served,
            "total_balked": self.balked_count,
            "avg_wait": round(avg_wait, 2),
            "max_wait": round(max_wait, 2),
            "overall_sla_compliance": round(overall_sla, 1),
            "avg_utilization": round(avg_utilization, 1),
            "per_service": self._compile_per_service(),
            "per_workgroup": self._compile_per_workgroup(total_minutes),
            "hourly": self._compile_hourly_breakdown(),
            "timeline_snapshots": self.timeline_snapshots,
        }

    def _compile_per_service(self) -> list:
        """Aggregates metrics grouped by service category."""
        per_service = {}
        for t in self.served_tickets:
            if t.service_name not in per_service:
                per_service[t.service_name] = {"waits": [], "breaches": 0, "count": 0}
            per_service[t.service_name]["waits"].append(t.wait_time)
            per_service[t.service_name]["count"] += 1
            if t.sla_breached:
                per_service[t.service_name]["breaches"] += 1

        results = []
        for svc_name, data in per_service.items():
            count = data["count"]

            # Replaced conditional expression
            if count > 0:
                compliance = ((count - data["breaches"]) / count) * 100.0
            else:
                compliance = 100.0

            results.append(
                {
                    "service_name": svc_name,
                    "avg_wait": round(sum(data["waits"]) / count, 2),
                    "sla_compliance_pct": round(compliance, 1),
                    "tickets_served": count,
                }
            )
        return results

    def _compile_per_workgroup(self, total_minutes: float) -> list:
        """Aggregates metrics grouped by workgroup."""
        results = []
        for wg in self.config.workgroups:
            busy = self.workgroup_busy_time.get(wg.name, 0.0)
            available = wg.counter_count * total_minutes

            # Replaced conditional expression
            if available > 0:
                util = (busy / available) * 100.0
            else:
                util = 0.0

            # Replaced generator expression
            served_count = 0
            for t in self.served_tickets:
                if t.workgroup_name == wg.name:
                    served_count += 1

            results.append(
                {
                    "workgroup_name": wg.name,
                    "utilization_pct": round(util, 1),
                    "tickets_served": served_count,
                }
            )
        return results

    def _compile_hourly_breakdown(self) -> list:
        """Compiles per-hour statistics for congestion analysis."""
        breakdown = []
        for h in range(self.config.duration_hours):
            stats = self.hourly_stats[h]
            waits = stats["wait_times"]

            # Replaced conditional expression
            if waits:
                avg_w = round(sum(waits) / len(waits), 2)
            else:
                avg_w = 0.0

            breakdown.append(
                {
                    "hour": f"{self.config.start_hour + h:02d}:00",
                    "avg_wait_mins": avg_w,
                    "max_queue_length": stats["max_queue_length"],
                    "arrivals": stats["arrivals"],
                    "balked": stats["balked"],
                }
            )
        return breakdown


# ---------------------------------------------------------------------------
# Config Builder: API Request Dict → SimulationConfig
# ---------------------------------------------------------------------------
"""
Request Body that frontend Sends
{
  "start_hour": 9,
  "duration_hours": 8,
  "waiting_capacity": 50,
  "inflow_type": "hourly_flow",
  "hourly_inflows": [15, 30, 45, 40, 20, 25, 35, 10],

  "services": [
    {
      "name": "Cash Transaction",
      "ratio": 0.70,
      "sla_target_mins": 10.0,
      "mean_service_time_mins": 5.0,
      "std_dev_service_time_mins": 2.0
    },
    {
      "name": "Account Opening",
      "ratio": 0.30,
      "sla_target_mins": 20.0,
      "mean_service_time_mins": 25.0,
      "std_dev_service_time_mins": 10.0
    }
  ],

  "workgroups": [
    {
      "name": "Main Counters",
      "counter_count": 4,
      "skills": [
        {
          "service_name": "Cash Transaction",
          "is_active": true,
          "priority": 1
        },
        {
          "service_name": "Account Opening",
          "is_active": true,
          "priority": 3
        }
      ]
    },
    {
      "name": "Specialist Desk",
      "counter_count": 1,
      "skills": [
        {
          "service_name": "Account Opening",
          "is_active": true,
          "priority": 1,
          "sla_target_mins": 15.0
        }
      ]
    }
  ]
}

"""


def build_config_from_request(request_data: dict) -> SimulationConfig:
    """
    Transforms a flat API request dictionary into a structured SimulationConfig.

    Handles three inflow modes:
    - 'hourly_flow': Uses the user-provided hourly ticket counts directly.
    - 'ai_forecast': Distributes a total daily volume across hours using
       the DEFAULT_HOURLY_PROFILE (government office traffic curve).
    - 'imported': Same as hourly_flow (expects pre-processed hourly data).

    Args:
        request_data: Dictionary from the API request body.

    Returns:
        SimulationConfig: Fully populated configuration object.
    """
    # Build service configs
    services = []
    for s in request_data["services"]:
        services.append(
            ServiceConfig(
                name=s["name"],
                ratio=s["ratio"],
                sla_target_mins=s["sla_target_mins"],
                mean_service_time_mins=s["mean_service_time_mins"],
                std_dev_service_time_mins=s["std_dev_service_time_mins"],
            )
        )

    # Build workgroup configs
    workgroups = []
    for wg in request_data["workgroups"]:
        skills = []
        for sk in wg["skills"]:
            skills.append(
                WorkgroupSkillConfig(
                    service_name=sk["service_name"],
                    is_active=sk["is_active"],
                    priority=sk["priority"],
                    sla_target_mins=sk.get("sla_target_mins", 15.0),
                )
            )
        workgroups.append(
            WorkgroupConfig(
                name=wg["name"],
                counter_count=wg["counter_count"],
                skills=skills,
            )
        )

    # Handle inflow mode
    duration_hours = request_data.get("duration_hours", 8)
    hourly_inflows = request_data.get("hourly_inflows", [])
    inflow_type = request_data.get("inflow_type", "hourly_flow")

    if inflow_type == "ai_forecast":
        # Distribute total predicted volume using the default hourly profile.
        # The total can come from hourly_inflows (summed) or a separate field.
        # Replaced conditional expressions
        if request_data.get("forecast_total") is not None:
            total_daily = request_data["forecast_total"]
        else:
            if hourly_inflows:
                total_daily = sum(hourly_inflows)
            else:
                total_daily = 300

        start_hour = request_data.get("start_hour", 9)
        profile = None
        
        # Try to get branch-specific profile
        branch_id = request_data.get("branch_id", 0)
        branch_name = request_data.get("branch_name")
        
        from database import get_branch_hourly_profile, get_branch_id_by_name
        
        if branch_id == 0 and branch_name:
            branch_id = get_branch_id_by_name(branch_name) or 0
            
        if branch_id != 0:
            full_profile = get_branch_hourly_profile(branch_id)
            if full_profile:
                profile = full_profile[start_hour:start_hour+duration_hours]
                if sum(profile) == 0:
                    profile = None

        if not profile:
            profile = DEFAULT_HOURLY_PROFILE[:duration_hours]
            
        profile_sum = sum(profile)
        if profile_sum == 0:
            profile = [1.0/len(profile)] * len(profile)
            profile_sum = 1.0

        # Replaced list comprehension
        hourly_inflows = []
        for p in profile:
            hourly_inflows.append(int(total_daily * (p / profile_sum)))

    # Pad or trim hourly_inflows to match duration_hours
    while len(hourly_inflows) < duration_hours:
        hourly_inflows.append(0)
    hourly_inflows = hourly_inflows[:duration_hours]

    return SimulationConfig(
        start_hour=request_data.get("start_hour", 9),
        duration_hours=duration_hours,
        waiting_capacity=request_data.get("waiting_capacity", 50),
        hourly_inflows=hourly_inflows,
        services=services,
        workgroups=workgroups,
    )


# ---------------------------------------------------------------------------
# Monte Carlo Runner & Results Averaging
# ---------------------------------------------------------------------------


def _average_results(results: list, config: SimulationConfig) -> dict:
    """
    Averages metrics across multiple independent simulation trial runs
    to produce stable, reliable output (Monte Carlo averaging).

    Scalar metrics (avg_wait, utilization) are averaged.
    Peak metrics (max_wait) take the worst case across all trials.

    Args:
        results: List of raw metric dicts from individual trials.
        config: The simulation configuration used for all trials.

    Returns:
        dict: Final averaged simulation results.
    """
    n = len(results)
    if n == 0:
        return {}

    # --- Summary metrics ---
    # Replaced generator expressions with standard loops
    sum_arrived = 0
    sum_served = 0
    sum_balked = 0
    sum_wait = 0
    max_wait_list = []
    sum_sla = 0
    sum_util = 0

    for r in results:
        sum_arrived += r["total_arrived"]
        sum_served += r["total_served"]
        sum_balked += r["total_balked"]
        sum_wait += r["avg_wait"]
        max_wait_list.append(r["max_wait"])
        sum_sla += r["overall_sla_compliance"]
        sum_util += r["avg_utilization"]

    avg_total_arrived = sum_arrived / n
    avg_total_served = sum_served / n
    avg_total_balked = sum_balked / n
    avg_wait = sum_wait / n
    max_wait = max(max_wait_list)  # Worst case across all trials
    avg_sla = sum_sla / n
    avg_util = sum_util / n

    # --- Per-service averages ---
    # Replaced set and dict comprehension
    service_names_set = set()
    for s in config.services:
        service_names_set.add(s.name)
    service_names = list(service_names_set)

    per_service_agg = {}
    for svc in service_names:
        per_service_agg[svc] = {"waits": [], "sla": [], "counts": []}

    for r in results:
        for sm in r["per_service"]:
            name = sm["service_name"]
            if name in per_service_agg:
                per_service_agg[name]["waits"].append(sm["avg_wait"])
                per_service_agg[name]["sla"].append(sm["sla_compliance_pct"])
                per_service_agg[name]["counts"].append(sm["tickets_served"])

    per_service_metrics = []
    for svc in service_names:
        data = per_service_agg[svc]
        if data["counts"]:
            per_service_metrics.append(
                {
                    "service_name": svc,
                    "avg_wait": round(sum(data["waits"]) / len(data["waits"]), 2),
                    "sla_compliance_pct": round(sum(data["sla"]) / len(data["sla"]), 1),
                    "tickets_served": int(
                        round(sum(data["counts"]) / len(data["counts"]))
                    ),
                }
            )

    # --- Per-workgroup averages ---
    # Replaced dict comprehension
    per_wg_agg = {}
    for wg in config.workgroups:
        per_wg_agg[wg.name] = {"utils": [], "counts": []}

    for r in results:
        for wm in r["per_workgroup"]:
            name = wm["workgroup_name"]
            if name in per_wg_agg:
                per_wg_agg[name]["utils"].append(wm["utilization_pct"])
                per_wg_agg[name]["counts"].append(wm["tickets_served"])

    per_workgroup_metrics = []
    for wg in config.workgroups:
        wg_name = wg.name
        data = per_wg_agg[wg_name]
        if data["utils"]:
            per_workgroup_metrics.append(
                {
                    "workgroup_name": wg_name,
                    "utilization_pct": round(
                        sum(data["utils"]) / len(data["utils"]), 1
                    ),
                    "tickets_served": int(
                        round(sum(data["counts"]) / len(data["counts"]))
                    ),
                }
            )

    # --- Hourly averages ---
    # Replaced nested list comprehensions and ternary operators
    hourly_breakdown = []
    for h in range(config.duration_hours):
        waits = []
        queues = []
        arrivals = []
        balked = []

        for r in results:
            if h < len(r["hourly"]):
                waits.append(r["hourly"][h]["avg_wait_mins"])
                queues.append(r["hourly"][h]["max_queue_length"])
                arrivals.append(r["hourly"][h]["arrivals"])
                balked.append(r["hourly"][h]["balked"])

        if waits:
            avg_wait_mins_val = round(sum(waits) / len(waits), 2)
        else:
            avg_wait_mins_val = 0.0

        if queues:
            max_queue_len_val = int(round(max(queues)))
        else:
            max_queue_len_val = 0

        if arrivals:
            arrivals_val = int(round(sum(arrivals) / len(arrivals)))
        else:
            arrivals_val = 0

        if balked:
            balked_val = int(round(sum(balked) / len(balked)))
        else:
            balked_val = 0

        hourly_breakdown.append(
            {
                "hour": f"{config.start_hour + h:02d}:00",
                "avg_wait_mins": avg_wait_mins_val,
                "max_queue_length": max_queue_len_val,
                "arrivals": arrivals_val,
                "balked": balked_val,
            }
        )

    # --- Average timeline snapshots across all trials ---
    averaged_snapshots = []
    if results and "timeline_snapshots" in results[0]:
        num_snapshots = len(results[0]["timeline_snapshots"])
        for idx in range(num_snapshots):
            sum_queue = 0
            sum_active = 0
            sum_served = 0
            sum_balked = 0
            sum_arrived = 0
            sum_wait = 0.0
            sum_sla = 0.0
            
            queue_by_svc_sums = {}
            for svc in config.services:
                queue_by_svc_sums[svc.name] = 0

            # Sum values for this minute index across all trials
            for r in results:
                snap = r["timeline_snapshots"][idx]
                sum_queue += snap["queue_total"]
                sum_active += snap["active_tellers"]
                sum_served += snap["cumulative_served"]
                sum_balked += snap["cumulative_balked"]
                sum_arrived += snap["cumulative_arrived"]
                sum_wait += snap["avg_wait"]
                sum_sla += snap["cumulative_sla"]
                
                snap_svc = snap.get("queue_by_service", {})
                for svc_name in queue_by_svc_sums:
                    queue_by_svc_sums[svc_name] += snap_svc.get(svc_name, 0)

            # Compute averages
            n_trials = len(results)
            avg_queue = int(round(sum_queue / n_trials))
            avg_active = int(round(sum_active / n_trials))
            avg_served = int(round(sum_served / n_trials))
            avg_balked = int(round(sum_balked / n_trials))
            avg_arrived = int(round(sum_arrived / n_trials))
            avg_wait_val = round(sum_wait / n_trials, 2)
            avg_sla_val = round(sum_sla / n_trials, 1)
            
            # Total tellers, clock and minute are constant/same across trials
            total_tellers = results[0]["timeline_snapshots"][idx]["total_tellers"]
            clock = results[0]["timeline_snapshots"][idx]["clock"]
            minute = results[0]["timeline_snapshots"][idx]["minute"]

            avg_queue_by_svc = {}
            for svc_name, svc_sum in queue_by_svc_sums.items():
                avg_queue_by_svc[svc_name] = int(round(svc_sum / n_trials))

            averaged_snapshots.append({
                "minute": minute,
                "clock": clock,
                "queue_total": avg_queue,
                "queue_by_service": avg_queue_by_svc,
                "active_tellers": avg_active,
                "total_tellers": total_tellers,
                "utilization": round((avg_active / max(total_tellers, 1)) * 100.0, 1),
                "cumulative_served": avg_served,
                "cumulative_sla": avg_sla_val,
                "cumulative_balked": avg_balked,
                "cumulative_arrived": avg_arrived,
                "avg_wait": avg_wait_val,
            })

    return {
        "summary": {
            "total_customers_arrived": int(round(avg_total_arrived)),
            "total_customers_served": int(round(avg_total_served)),
            "total_customers_balked": int(round(avg_total_balked)),
            "avg_wait_time_mins": round(avg_wait, 2),
            "max_wait_time_mins": round(max_wait, 2),
            "overall_sla_compliance_pct": round(avg_sla, 1),
            "avg_teller_utilization_pct": round(avg_util, 1),
        },
        "per_service_metrics": per_service_metrics,
        "per_workgroup_metrics": per_workgroup_metrics,
        "hourly_breakdown": hourly_breakdown,
        "timeline_snapshots": averaged_snapshots,
    }


def run_simulation(request_data: dict, num_trials: int = 50) -> dict:
    """
    Runs a Monte Carlo DES simulation: executes num_trials independent
    simulation runs with the same configuration and averages the results
    to produce stable, reliable performance metrics.

    Args:
        request_data: Configuration dictionary from the API request body.
        num_trials: Number of independent trials to average (default 50).

    Returns:
        dict: Averaged simulation results containing:
            - summary: Overall performance metrics.
            - per_service_metrics: Breakdown by service category.
            - per_workgroup_metrics: Breakdown by teller workgroup.
            - hourly_breakdown: Hour-by-hour congestion analysis.
    """
    config = build_config_from_request(request_data)

    all_results = []
    for trial in range(num_trials):
        env = simpy.Environment()
        sim = BranchSimulator(env, config)
        result = sim.run()
        all_results.append(result)

    return _average_results(all_results, config)
