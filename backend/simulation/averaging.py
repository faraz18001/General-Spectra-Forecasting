from simulation.dataclasses import SimulationConfig


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

    # Per-workgroup averages

    per_wg_agg = {}
    if config.counters is not None:
        for counter in config.counters:
            per_wg_agg[counter.name] = {"utils": [], "counts": []}
    else:
        for wg in config.workgroups:
            per_wg_agg[wg.name] = {"utils": [], "counts": []}

    for r in results:
        for wm in r["per_workgroup"]:
            name = wm["workgroup_name"]
            if name in per_wg_agg:
                per_wg_agg[name]["utils"].append(wm["utilization_pct"])
                per_wg_agg[name]["counts"].append(wm["tickets_served"])

    per_workgroup_metrics = []
    if config.counters is not None:
        for counter in config.counters:
            name = counter.name
            data = per_wg_agg[name]
            if data["utils"]:
                per_workgroup_metrics.append(
                    {
                        "workgroup_name": name,
                        "utilization_pct": round(
                            sum(data["utils"]) / len(data["utils"]), 1
                        ),
                        "tickets_served": int(
                            round(sum(data["counts"]) / len(data["counts"]))
                        ),
                    }
                )
    else:
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

    # Hourly averages

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

    # Average timeline snapshots across all trial
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

            arrivals_by_svc_sums = {}
            for svc in config.services:
                arrivals_by_svc_sums[svc.name] = 0

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

                snap_arrivals = snap.get("arrivals_by_service", {})
                for svc_name in arrivals_by_svc_sums:
                    arrivals_by_svc_sums[svc_name] += snap_arrivals.get(svc_name, 0)

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

            avg_arrivals_by_svc = {}
            svc_total = sum(arrivals_by_svc_sums.values())
            if svc_total > 0 and avg_arrived > 0:
                int_sum = 0
                remainders = []
                for svc_name, svc_sum in arrivals_by_svc_sums.items():
                    float_val = (svc_sum / svc_total) * avg_arrived
                    int_val = int(float_val)
                    avg_arrivals_by_svc[svc_name] = int_val
                    int_sum += int_val
                    remainders.append((svc_name, float_val - int_val))
                seats_left = avg_arrived - int_sum
                remainders.sort(key=lambda x: x[1], reverse=True)
                for i in range(seats_left):
                    avg_arrivals_by_svc[remainders[i][0]] += 1
            else:
                for svc_name in arrivals_by_svc_sums:
                    avg_arrivals_by_svc[svc_name] = 0

            averaged_snapshots.append(
                {
                    "minute": minute,
                    "clock": clock,
                    "queue_total": avg_queue,
                    "queue_by_service": avg_queue_by_svc,
                    "arrivals_by_service": avg_arrivals_by_svc,
                    "active_tellers": avg_active,
                    "total_tellers": total_tellers,
                    "utilization": round(
                        (avg_active / max(total_tellers, 1)) * 100.0, 1
                    ),
                    "cumulative_served": avg_served,
                    "cumulative_sla": avg_sla_val,
                    "cumulative_balked": avg_balked,
                    "cumulative_arrived": avg_arrived,
                    "avg_wait": avg_wait_val,
                }
            )

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
