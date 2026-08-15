"""
Mathematical and Operational Verification Test Suite for the SimPy Simulation Engine.

Evaluates:
1. Erlang C Queueing Theory Analytical Convergence (M/M/c vs SimPy)
2. Staffing Level Sensitivity & Congestion Modeling (Under/Overstaffing)
3. Skill-Based Priority Routing Soundness (VIP vs Regular queues)

Usage:
    python test_simulation_math.py
"""

import sys
import math
import random
from simulation import run_simulation, build_config_from_request

# ANSI Colors for CLI formatting
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
BOLD = "\033[1m"
RESET = "\033[0m"

def erlang_c_probability(c, rho):
    """Computes the probability that a customer must wait in queue (Erlang C)."""
    a = c * rho
    num = (a**c / math.factorial(c)) * (1.0 / (1.0 - rho))
    den_sum = sum((a**k / math.factorial(k)) for k in range(c))
    return num / (den_sum + num)

def theoretical_mmc_wait_time(c, mean_service, arrivals_per_hour):
    """Computes the theoretical average wait time for an M/M/c queue."""
    lam = arrivals_per_hour / 60.0
    mu = 1.0 / mean_service
    rho = lam / (c * mu)
    
    if rho >= 1.0:
        return float('inf'), rho
        
    P_q = erlang_c_probability(c, rho)
    W_q = P_q * (mean_service / (c * (1.0 - rho)))
    return W_q, rho

def run_erlang_validation():
    print(f"\n{BOLD}{BLUE}===================================================={RESET}")
    print(f"{BOLD}{BLUE}AUDIT 1: ERLANG C STOCHASTIC CONVERGENCE TEST (M/M/c){RESET}")
    print(f"{BOLD}{BLUE}===================================================={RESET}")
    
    c = 3
    mean_service = 6.0
    std_dev_service = 6.0  # std_dev = mean makes it exponential
    arrivals_per_hour = 20
    duration_hours = 24  # Run a long 24-hour day to minimize cold-start boundary bias
    num_trials = 50
    
    # Seed random number generator for 100% deterministic test reproducibility
    random.seed(42)
    
    theoretical_wait, theoretical_util = theoretical_mmc_wait_time(c, mean_service, arrivals_per_hour)
    
    print(f"Theory Inputs:")
    print(f"  - Counters (c): {c}")
    print(f"  - Arrival Rate (\u03bb): {arrivals_per_hour}/hr ({arrivals_per_hour/60.0:.3f}/min)")
    print(f"  - Service Time (Exponential): {mean_service} min")
    print(f"  - Theoretical Teller Utilization: {theoretical_util * 100:.1f}%")
    print(f"  - Theoretical Avg Wait Time in Queue (Wq): {theoretical_wait:.3f} minutes")
    print("\nRunning SimPy simulation across 50 Monte Carlo trials...")
    
    request_payload = {
        "start_hour": 8,
        "duration_hours": duration_hours,
        "waiting_capacity": 1000,  # Pure M/M/c conditions
        "inflow_type": "hourly_flow",
        "hourly_inflows": [arrivals_per_hour] * duration_hours,
        "services": [
            {
                "name": "Standard Service",
                "ratio": 1.0,
                "sla_target_mins": 15.0,
                "mean_service_time_mins": mean_service,
                "std_dev_service_time_mins": std_dev_service
            }
        ],
        "workgroups": [
            {
                "name": "Main Cluster",
                "counter_count": c,
                "skills": [
                    {
                        "service_name": "Standard Service",
                        "is_active": True,
                        "priority": 3,
                        "sla_target_mins": 15.0
                    }
                ]
            }
        ]
    }
    
    results = run_simulation(request_payload, num_trials=num_trials)
    summary = results["summary"]
    sim_wait = summary["avg_wait_time_mins"]
    sim_util = summary["avg_teller_utilization_pct"]
    
    util_diff = abs(sim_util - (theoretical_util * 100.0))
    wait_diff = abs(sim_wait - theoretical_wait)
    wait_err_pct = (wait_diff / theoretical_wait) * 100.0
    
    print(f"\n{BOLD}Comparison results:{RESET}")
    print(f"  - Utilization: {BOLD}{sim_util:.1f}%{RESET} (Theory: {theoretical_util*100.0:.1f}%, Error: {util_diff:.1f} pp)")
    print(f"  - Avg Wait Time: {BOLD}{sim_wait:.2f} min{RESET} (Theory: {theoretical_wait:.2f} min, Error: {wait_diff:.2f} min / {wait_err_pct:.1f}%)")
    
    # Validation threshold checks
    util_ok = util_diff <= 5.0
    wait_ok = wait_err_pct <= 25.0 or wait_diff <= 0.6
    
    if util_ok and wait_ok:
        print(f"  - Status: {GREEN}{BOLD}[PASSED]{RESET} - SimPy closely converges with Erlang C queueing theory!")
        return True
    else:
        print(f"  - Status: {YELLOW}{BOLD}[WARNING]{RESET} - Marginal deviations observed. (Increase trials or duration)")
        return False

def run_staffing_audits():
    print(f"\n{BOLD}{BLUE}===================================================={RESET}")
    print(f"{BOLD}{BLUE}AUDIT 2: STAFFING LEVEL SENSITIVITY & STRESS TEST{RESET}")
    print(f"{BOLD}{BLUE}===================================================={RESET}")
    
    duration_hours = 8
    waiting_capacity = 30
    arrivals_per_hour = 20
    mean_service = 6.0
    std_dev_service = 6.0
    
    scenarios = {
        "Base Case (3 Tellers)": 3,
        "Understaffed / Overloaded (2 Tellers)": 2,
        "Overstaffed / Optimized (4 Tellers)": 4
    }
    
    print(f"Running staffing sensitivity matrix (100 trials each)...")
    
    all_passed = True
    for name, c in scenarios.items():
        print(f"\n* Scenario: {BOLD}{name}{RESET} (Tellers: {c})")
        
        request_payload = {
            "start_hour": 9,
            "duration_hours": duration_hours,
            "waiting_capacity": waiting_capacity,
            "inflow_type": "hourly_flow",
            "hourly_inflows": [arrivals_per_hour] * duration_hours,
            "services": [
                {
                    "name": "General Service",
                    "ratio": 1.0,
                    "sla_target_mins": 15.0,
                    "mean_service_time_mins": mean_service,
                    "std_dev_service_time_mins": std_dev_service
                }
            ],
            "workgroups": [
                {
                    "name": "Primary Cluster",
                    "counter_count": c,
                    "skills": [
                        {
                            "service_name": "General Service",
                            "is_active": True,
                            "priority": 3,
                            "sla_target_mins": 15.0
                        }
                    ]
                }
            ]
        }
        
        results = run_simulation(request_payload, num_trials=100)
        summary = results["summary"]
        
        print(f"    - Arrived / Served / Balked: {summary['total_customers_arrived']} / {summary['total_customers_served']} / {summary['total_customers_balked']}")
        print(f"    - Teller Utilization: {summary['avg_teller_utilization_pct']:.1f}%")
        print(f"    - Avg / Max Wait Time: {summary['avg_wait_time_mins']:.2f} min / {summary['max_wait_time_mins']:.2f} min")
        print(f"    - SLA Compliance: {summary['overall_sla_compliance_pct']:.1f}%")
        
        # Scenario validations
        if c == 2:
            # Understaffed should lead to high wait times and lower SLA compliance
            cond = summary['avg_wait_time_mins'] > 10.0 and summary['overall_sla_compliance_pct'] < 70.0
            status_str = f"{GREEN}[PASSED]{RESET} - Replicated severe operational bottlenecks!" if cond else f"{RED}[FAILED]{RESET}"
            print(f"    - Understaffing Validation: {status_str}")
            if not cond: all_passed = False
        elif c == 4:
            # Overstaffed should drop wait times near zero and increase SLA compliance to nearly 100%
            cond = summary['avg_wait_time_mins'] < 1.0 and summary['overall_sla_compliance_pct'] >= 99.0
            status_str = f"{GREEN}[PASSED]{RESET} - Confirmed staffing economies of scale!" if cond else f"{RED}[FAILED]{RESET}"
            print(f"    - Overstaffing Validation: {status_str}")
            if not cond: all_passed = False
        elif c == 3:
            # Base case should be perfectly stable
            cond = summary['avg_wait_time_mins'] < 3.0 and summary['overall_sla_compliance_pct'] >= 90.0
            status_str = f"{GREEN}[PASSED]{RESET} - Confirmed perfect staffing baseline!" if cond else f"{RED}[FAILED]{RESET}"
            print(f"    - Base Staffing Validation: {status_str}")
            if not cond: all_passed = False
            
    return all_passed

def run_priority_validation():
    print(f"\n{BOLD}{BLUE}===================================================={RESET}")
    print(f"{BOLD}{BLUE}AUDIT 3: MULTI-SKILL AND PRIORITY ROUTING INTEGRITY{RESET}")
    print(f"{BOLD}{BLUE}===================================================={RESET}")
    
    arrivals_per_hour = 12
    c = 3
    duration_hours = 12
    
    print(f"Running multi-skilled priority simulation (100 trials)...")
    print(f"  - VIP Service: Priority 1 (Highest)")
    print(f"  - Regular Service: Priority 5 (Lowest)")
    print(f"  - Both have identical 10-minute transaction times.")
    
    request_payload = {
        "start_hour": 8,
        "duration_hours": duration_hours,
        "waiting_capacity": 100,
        "inflow_type": "hourly_flow",
        "hourly_inflows": [arrivals_per_hour] * duration_hours,
        "services": [
            {
                "name": "VIP Service",
                "ratio": 0.20,
                "sla_target_mins": 5.0,
                "mean_service_time_mins": 10.0,
                "std_dev_service_time_mins": 3.0
            },
            {
                "name": "Regular Service",
                "ratio": 0.80,
                "sla_target_mins": 15.0,
                "mean_service_time_mins": 10.0,
                "std_dev_service_time_mins": 3.0
            }
        ],
        "workgroups": [
            {
                "name": "Universal Cluster",
                "counter_count": c,
                "skills": [
                    {
                        "service_name": "VIP Service",
                        "is_active": True,
                        "priority": 1,
                        "sla_target_mins": 5.0
                    },
                    {
                        "service_name": "Regular Service",
                        "is_active": True,
                        "priority": 5,
                        "sla_target_mins": 15.0
                    }
                ]
            }
        ]
    }
    
    results = run_simulation(request_payload, num_trials=100)
    
    vip_metrics = next(s for s in results["per_service_metrics"] if s["service_name"] == "VIP Service")
    reg_metrics = next(s for s in results["per_service_metrics"] if s["service_name"] == "Regular Service")
    
    print(f"\nResults:")
    print(f"  - VIP (Priority 1): {BOLD}{vip_metrics['avg_wait']:.2f} min{RESET} avg wait (SLA Compliance: {vip_metrics['sla_compliance_pct']:.1f}%)")
    print(f"  - Regular (Priority 5): {BOLD}{reg_metrics['avg_wait']:.2f} min{RESET} avg wait (SLA Compliance: {reg_metrics['sla_compliance_pct']:.1f}%)")
    
    # VIP wait time should be significantly less than Regular wait time
    priority_works = vip_metrics['avg_wait'] < reg_metrics['avg_wait'] * 0.70
    
    if priority_works:
        print(f"  - Status: {GREEN}{BOLD}[PASSED]{RESET} - Tellers prioritize VIP queue correctly! VIP wait time is {((reg_metrics['avg_wait'] - vip_metrics['avg_wait'])/reg_metrics['avg_wait']*100):.1f}% shorter.")
        return True
    else:
        print(f"  - Status: {RED}{BOLD}[FAILED]{RESET} - VIP customers did not receive significant priority routing benefit.")
        return False

def main():
    print(f"{BOLD}===================================================={RESET}")
    print(f"{BOLD}      SIMULATION ENGINE COMPREHENSIVE VERIFICATION  {RESET}")
    print(f"{BOLD}===================================================={RESET}")
    
    erlang_pass = run_erlang_validation()
    staffing_pass = run_staffing_audits()
    priority_pass = run_priority_validation()
    
    print(f"\n{BOLD}===================================================={RESET}")
    print(f"{BOLD}                  SUMMARY ASSESSMENT                {RESET}")
    print(f"===================================================={RESET}")
    
    total_audits = 3
    passed_audits = sum([erlang_pass, staffing_pass, priority_pass])
    
    print(f"Audits Completed: {passed_audits} / {total_audits} Passed")
    
    if passed_audits == total_audits:
        print(f"VERDICT: {GREEN}{BOLD}[100% VERIFIED]{RESET} - The SimPy Operational Simulation Engine is fully correct and ready for production use!")
        sys.exit(0)
    else:
        print(f"VERDICT: {YELLOW}{BOLD}[WARNING]{RESET} - Some minor variances or failures occurred. Please inspect the audit logs above.")
        sys.exit(1)

if __name__ == "__main__":
    main()
