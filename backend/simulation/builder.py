from typing import List

from simulation.dataclasses import (
    CallingProfile,
    CallingProfileCondition,
    CallingProfileOrderItem,
    ServiceConfig,
    SimulationConfig,
    TellerCounterConfig,
    WorkgroupConfig,
    WorkgroupSkillConfig,
)
from simulation.math_utils import DEFAULT_HOURLY_PROFILE


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
    for s in request_data.get("services", []):
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
    for wg in request_data.get("workgroups", []):
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
                profile = full_profile[start_hour : start_hour + duration_hours]
                if sum(profile) == 0:
                    profile = None

        if not profile:
            profile = DEFAULT_HOURLY_PROFILE[:duration_hours]

        profile_sum = sum(profile)
        if profile_sum == 0:
            profile = [1.0 / len(profile)] * len(profile)
            profile_sum = 1.0

        # Replaced list comprehension
        hourly_inflows = []
        for p in profile:
            hourly_inflows.append(int(total_daily * (p / profile_sum)))

    # Pad or trim hourly_inflows to match duration_hours
    while len(hourly_inflows) < duration_hours:
        hourly_inflows.append(0)
    hourly_inflows = hourly_inflows[:duration_hours]

    # Build Calling Profiles if present
    calling_profiles = None
    if "calling_profiles" in request_data and request_data["calling_profiles"]:
        calling_profiles = []
        for cp in request_data["calling_profiles"]:
            order = []
            call_order_source = cp.get("order", [])
            if not call_order_source and "CALL" in cp and isinstance(cp["CALL"], dict):
                call_order_source = cp["CALL"].get("order", [])
            for level in call_order_source:
                level_items = []
                for item in level:
                    cond = None
                    cond_data = item.get("condition")
                    if cond_data and isinstance(cond_data, dict):
                        cond = CallingProfileCondition(
                            max_wait_time=float(cond_data.get("max_wait_time", 10.0)),
                            ticket_priority=bool(
                                cond_data.get("ticket_priority", False)
                            ),
                        )
                    level_items.append(
                        CallingProfileOrderItem(
                            category=str(item["category"]),
                            condition=cond,
                            count=int(item.get("count", 1)),
                        )
                    )
                order.append(level_items)

            # Use "id" if present, otherwise fallback to "profile_id" or 0
            profile_id = int(cp.get("id", cp.get("profile_id", 0)))
            calling_profiles.append(
                CallingProfile(
                    id=profile_id,
                    name=cp.get("name", f"Profile {profile_id}"),
                    type=cp.get("type", "FIFO"),
                    default_category=str(cp.get("default_category", "")),
                    order=order,
                )
            )

    # Build Teller Counter configs if present
    counters = None
    if "counters" in request_data and request_data["counters"]:
        counters = []
        for c in request_data["counters"]:
            # Standardize profile lists
            cp_list = c.get("counter_profiles", [])
            if isinstance(cp_list, str):
                cp_list = [int(x.strip()) for x in cp_list.split(",") if x.strip()]
            else:
                cp_list = [int(x) for x in cp_list if x is not None]

            op_list = c.get("operator_profiles", [])
            if isinstance(op_list, str):
                op_list = [int(x.strip()) for x in op_list.split(",") if x.strip()]
            else:
                op_list = [int(x) for x in op_list if x is not None]

            counters.append(
                TellerCounterConfig(
                    name=c.get("name", f"Counter {c.get('counter_id', '')}"),
                    counter_id=int(c["counter_id"]),
                    operator_name=c.get("operator_name"),
                    counter_profiles=cp_list,
                    operator_profiles=op_list,
                )
            )

    resolution_mode = request_data.get("resolution_mode", "hybrid")
    category_max_wait_times = request_data.get("category_max_wait_times")

    return SimulationConfig(
        start_hour=request_data.get("start_hour", 9),
        duration_hours=duration_hours,
        waiting_capacity=request_data.get("waiting_capacity", 50),
        hourly_inflows=hourly_inflows,
        services=services,
        workgroups=workgroups,
        calling_profiles=calling_profiles,
        counters=counters,
        resolution_mode=resolution_mode,
        category_max_wait_times=category_max_wait_times,
    )
