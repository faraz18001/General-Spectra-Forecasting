import random
from typing import List, Optional

import simpy
try:
    from logger_setup import log_print as print
except ImportError:
    pass

from simulation.dataclasses import (
    CallingProfile,
    ServiceConfig,
    SimulationConfig,
    TellerCounterConfig,
    Ticket,
    TicketMetrics,
    WorkgroupConfig,
)


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
        self.arrivals_by_service = {}

        self.workgroup_busy_time = {}
        if config.counters is not None:
            for counter in config.counters:
                self.workgroup_busy_time[counter.name] = 0.0
        else:
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
        self.profiles_by_id = {}
        if config.counters is not None:
            self.total_tellers = len(config.counters)
            if config.calling_profiles:
                for p in config.calling_profiles:
                    self.profiles_by_id[p.id] = p
        else:
            for wg in config.workgroups:
                self.total_tellers += wg.counter_count

    # === Helper Methods ===

    def _get_hour_offset(self, sim_time: float) -> int:
        """Converts simulation time (minutes) to hour offset index."""
        return min(int(sim_time / 60.0), self.config.duration_hours - 1)

    def _update_max_queue_length(self):
        """Tracks peak queue length per hour for reporting."""
        hour = self._get_hour_offset(self.env.now)
        current_length = len(self.lobby_queue)
        if current_length > self.hourly_stats[hour]["max_queue_length"]:
            self.hourly_stats[hour]["max_queue_length"] = current_length

    # === SimPy Process: Customer Arrival Generator (Poisson Process) ===

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
                self.arrivals_by_service[service_name] = self.arrivals_by_service.get(service_name, 0) + 1

                # --- Lobby Capacity Check (Balking) ---
                if len(self.lobby_queue) >= self.config.waiting_capacity:
                    self.balked_count += 1
                    self.hourly_stats[current_hour]["balked"] += 1
                    continue

                # Determine ticket priority from workgroup skill configs
                best_priority = 999
                if self.config.workgroups:
                    for wg in self.config.workgroups:
                        for skill in wg.skills:
                            if skill.service_name == service_name and skill.is_active:
                                if skill.priority < best_priority:
                                    best_priority = skill.priority
                if best_priority == 999:
                    best_priority = 3

                # Simulate a kiosk priority selection (e.g. VIP vs Regular)
                kiosk_priority = random.choices([1, 3], weights=[0.2, 0.8], k=1)[0]

                ticket = Ticket(
                    id=self.ticket_counter,
                    service_name=service_name,
                    arrival_time=self.env.now,
                    priority=best_priority,
                    kiosk_priority=kiosk_priority,
                )

                self.lobby_queue.append(ticket)
                self._update_max_queue_length()

                # Wake up any idle tellers waiting for customers
                if not self.new_ticket_event.triggered:
                    self.new_ticket_event.succeed()

    # === SimPy Process: Teller (Server-Pull with Skill-Based Routing) ===

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

    def get_best_ticket_for_profiles(
        self, resolved_profiles: List[CallingProfile]
    ) -> Optional[Ticket]:
        """
        Scans the central lobby queue and returns the best eligible ticket
        for the given active calling profiles.

        Precedence Rules:
        1. Evaluate priority levels in parallel across all resolved profiles from level 1 down.
        2. Within each level:
           - For FIFO profiles: any ticket matching the category is eligible.
           - For Overflow profiles: tickets matching the category are eligible only if wait_time >= max_wait_time.
        3. If multiple candidates are eligible:
           - Sort by kiosk-level priority if ticket_priority condition is True.
           - Otherwise, sort by FIFO arrival time.
        4. Fallback: If no tickets match the priority levels, fall back to the default_category of the profiles.
        """
        if not resolved_profiles:
            return None

        # Determine the maximum number of priority levels across active profiles
        max_levels = 0
        for p in resolved_profiles:
            if len(p.order) > max_levels:
                max_levels = len(p.order)

        for level_idx in range(max_levels):
            level_candidates = []
            for p in resolved_profiles:
                if level_idx >= len(p.order):
                    continue
                level_items = p.order[level_idx]
                for item in level_items:
                    # Look for matching tickets in lobby queue
                    for ticket in self.lobby_queue:
                        if ticket.service_name == item.category:
                            is_eligible = False
                            if p.type.upper() == "FIFO":
                                is_eligible = True
                            elif p.type.upper() == "OVERFLOW":
                                wait_time = self.env.now - ticket.arrival_time
                                threshold = (
                                    item.condition.max_wait_time
                                    if item.condition
                                    else 0.0
                                )
                                if wait_time >= threshold:
                                    is_eligible = True

                            if is_eligible:
                                # Check if ticket_priority is enabled for this item
                                use_kiosk_priority = False
                                if item.condition and item.condition.ticket_priority:
                                    use_kiosk_priority = True

                                # Sort key:
                                # 1. Priority value (kiosk_priority if enabled, else 999 to treat as standard/equal)
                                # 2. Arrival time (FIFO)
                                sort_priority = (
                                    ticket.kiosk_priority if use_kiosk_priority else 999
                                )
                                level_candidates.append(
                                    (sort_priority, ticket.arrival_time, ticket)
                                )

            if level_candidates:
                # Sort: priority ASC (highest priority is lower number), then arrival_time ASC
                level_candidates.sort(key=lambda x: (x[0], x[1]))
                best_ticket = level_candidates[0][2]
                self.lobby_queue.remove(best_ticket)
                return best_ticket

        # Fallback to default categories of profiles if no candidates matched the priority levels
        for p in resolved_profiles:
            default_cat = p.default_category
            if not default_cat:
                continue
            default_candidates = []
            for ticket in self.lobby_queue:
                if ticket.service_name == default_cat:
                    default_candidates.append(ticket)

            if default_candidates:
                # Fallback defaults are called purely FIFO
                default_candidates.sort(key=lambda x: x.arrival_time)
                best_ticket = default_candidates[0]
                self.lobby_queue.remove(best_ticket)
                return best_ticket

        return None

    def teller_process(self, workgroup: WorkgroupConfig, teller_id: int):
        """
        SimPy process for a single teller counter within a workgroup (legacy fallback).

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

    def teller_profile_process(self, counter: TellerCounterConfig):
        """
        SimPy process for a single teller counter using QMS Calling Profiles.
        """
        wg_name = counter.name

        # Resolve effective profiles based on configuration resolution mode
        # Hardcoded to 'counter' per user request
        mode = "counter"
        profile_ids = []
        if mode == "counter":
            profile_ids = counter.counter_profiles
        elif mode == "operator":
            profile_ids = counter.operator_profiles
        else:  # hybrid
            combined = []
            for p_id in counter.counter_profiles:
                if p_id not in combined:
                    combined.append(p_id)
            for p_id in counter.operator_profiles:
                if p_id not in combined:
                    combined.append(p_id)
            profile_ids = combined

        resolved_profiles = []
        for p_id in profile_ids:
            if p_id in self.profiles_by_id:
                resolved_profiles.append(self.profiles_by_id[p_id])

        while True:
            # Server-pull from queue
            ticket = self.get_best_ticket_for_profiles(resolved_profiles)

            if ticket is None:
                # No eligible tickets — wait for a new arrival signal
                if self.new_ticket_event.triggered:
                    self.new_ticket_event = self.env.event()
                yield self.new_ticket_event
                continue

            # --- Serve the customer ---
            service_start = self.env.now
            wait_time = service_start - ticket.arrival_time

            svc = self.services[ticket.service_name]
            service_duration = max(
                0.5, random.lognormvariate(svc.lognormal_mu, svc.lognormal_sigma)
            )

            self.active_tellers += 1
            yield self.env.timeout(service_duration)
            self.active_tellers -= 1

            self.workgroup_busy_time[wg_name] += service_duration

            # Resolve SLA target (default from category master if defined, else service SLA target)
            sla_target = svc.sla_target_mins
            if (
                self.config.category_max_wait_times
                and ticket.service_name in self.config.category_max_wait_times
            ):
                sla_target = float(
                    self.config.category_max_wait_times[ticket.service_name]
                )

            hour_of_arrival = self._get_hour_offset(ticket.arrival_time)
            self.hourly_stats[hour_of_arrival]["wait_times"].append(wait_time)

            self.served_tickets.append(
                TicketMetrics(
                    ticket_id=ticket.id,
                    service_name=ticket.service_name,
                    arrival_time=ticket.arrival_time,
                    service_start_time=service_start,
                    service_end_time=service_start + service_duration,
                    wait_time=wait_time,
                    service_duration=service_duration,
                    workgroup_name=wg_name,
                    sla_target=sla_target,
                    sla_breached=wait_time > sla_target,
                    hour_of_arrival=hour_of_arrival,
                )
            )

    # SimPy Process: Timeline Monitor (Passive Observer) ===

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
                "arrivals_by_service": dict(self.arrivals_by_service),
                "active_tellers": self.active_tellers,
                "total_tellers": self.total_tellers,
                "utilization": round(
                    (self.active_tellers / max(self.total_tellers, 1)) * 100.0, 1
                ),
                "cumulative_served": served_so_far,
                "cumulative_sla": cumulative_sla,
                "cumulative_balked": self.balked_count,
                "cumulative_arrived": self.ticket_counter,
                "avg_wait": avg_wait,
            }

            self.timeline_snapshots.append(snapshot)

            # Sleep for 1 simulated minute
            yield self.env.timeout(1.0)

    # === Run Simulation & Compile Metrics ===

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

        # Start teller processes (calling-profile based or legacy workgroups)
        if self.config.counters is not None:
            for counter in self.config.counters:
                self.env.process(self.teller_profile_process(counter))
        else:
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
        total_arrived = self.ticket_counter
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
        if self.config.counters is not None:
            total_tellers = len(self.config.counters)
        else:
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
        """Aggregates metrics grouped by workgroup or counter."""
        results = []
        if self.config.counters is not None:
            for counter in self.config.counters:
                busy = self.workgroup_busy_time.get(counter.name, 0.0)
                available = 1.0 * total_minutes

                if available > 0:
                    util = (busy / available) * 100.0
                else:
                    util = 0.0

                served_count = 0
                for t in self.served_tickets:
                    if t.workgroup_name == counter.name:
                        served_count += 1

                results.append(
                    {
                        "workgroup_name": counter.name,
                        "utilization_pct": round(util, 1),
                        "tickets_served": served_count,
                    }
                )
        else:
            for wg in self.config.workgroups:
                busy = self.workgroup_busy_time.get(wg.name, 0.0)
                available = wg.counter_count * total_minutes

                if available > 0:
                    util = (busy / available) * 100.0
                else:
                    util = 0.0

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
