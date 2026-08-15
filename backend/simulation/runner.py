import simpy

from simulation.builder import build_config_from_request
from simulation.averaging import _average_results
from simulation.engine import BranchSimulator

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
    for _ in range(num_trials):
        env = simpy.Environment()
        sim = BranchSimulator(env, config)
        result = sim.run()
        all_results.append(result)

    averaged = _average_results(all_results, config)

    # Use a single trial's raw data for the replay animation and summary
    # (averaged point-in-time state transitions are incoherent across trials)
    if all_results:
        trial = all_results[0]

        averaged["timeline_snapshots"] = trial["timeline_snapshots"]

        averaged["summary"] = {
            "total_customers_arrived": trial["total_arrived"],
            "total_customers_served": trial["total_served"],
            "total_customers_balked": trial["total_balked"],
            "avg_wait_time_mins": trial["avg_wait"],
            "max_wait_time_mins": trial["max_wait"],
            "overall_sla_compliance_pct": trial["overall_sla_compliance"],
            "avg_teller_utilization_pct": trial["avg_utilization"],
        }

        averaged["per_service_metrics"] = trial["per_service"]
        averaged["per_workgroup_metrics"] = trial["per_workgroup"]
        averaged["hourly_breakdown"] = trial["hourly"]

    return averaged
