import json
import re
import datetime as dt
from sqlalchemy import text
from remote_engine import get_remote_session


def _fix_sp_json(raw):
    """Fix malformed JSON from the USP_GetBranchConfiguration SP."""
    if not raw or not isinstance(raw, str):
        return raw

    fixed = raw

    # Fix datetime values: "sla_target_mins":Aug 19 2021 12:05AM → minutes
    def _dt_replacer(m):
        try:
            parsed = dt.datetime.strptime(m.group(1), "%b %d %Y %I:%M%p")
            return f'"sla_target_mins":{parsed.hour * 60 + parsed.minute}'
        except ValueError:
            return m.group(0)

    fixed = re.sub(
        r'"sla_target_mins":([A-Z][a-z]{2} +\d{1,2} +\d{4} +\d{1,2}:\d{2}[AP]M)',
        _dt_replacer,
        fixed,
    )

    # Quote bare string values (json keys like "sqn":AC2 → "sqn":"AC2")
    # Pattern: : followed by optional space then word chars that aren't already quoted
    fixed = re.sub(r': ?([A-Za-z_][A-Za-z0-9 _-]*?)([,}\]])', r':"\1"\2', fixed)

    # Fix trailing commas in arrays: "profileIds":"427," → "profileIds":["427"]
    fixed = re.sub(r'"profileIds":(\d+),"', r'"profileIds":[\1],"', fixed)

    return fixed


def _safe_parse_json(raw):
    """Parse JSON, returning empty dict/list on failure."""
    if not raw or not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        try:
            return json.loads(_fix_sp_json(raw))
        except (json.JSONDecodeError, TypeError):
            return {} if raw.strip().startswith("{") else []


def _parse_profiles(session_dash, result, hub_id, cat_id_to_name):
    """Parse EQPortal_Profile rows and populate calling_profiles."""
    profiles = session_dash.execute(
        text(
            "SELECT ProfileID, ProfileName, ProfileValue, DefaultCategoryId "
            "FROM EQPortal_Profile WHERE HubId = :hid AND ISNULL(isDeleted,0) = 0"
        ),
        {"hid": hub_id},
    ).fetchall()

    for prof in profiles:
        prof_id, prof_name, prof_value_raw, default_cat_id = prof
        try:
            profile_value = json.loads(prof_value_raw) if prof_value_raw else {}
        except (json.JSONDecodeError, TypeError):
            profile_value = {}

        raw_order = profile_value.get("CALL", {}).get("order", [])
        mapped_order = []
        for level in raw_order:
            mapped_level = []
            for item in level:
                raw_cat = item.get("category", "")
                cat_key = int(raw_cat) if (isinstance(raw_cat, int) or (isinstance(raw_cat, str) and raw_cat.isdigit())) else raw_cat
                if cat_key in cat_id_to_name:
                    mapped_cat = cat_id_to_name[cat_key]
                    raw_cond = item.get("condition", "")
                    mapped_cond = None
                    if raw_cond and raw_cond != "" and isinstance(raw_cond, dict):
                        mapped_cond = {
                            "max_wait_time": raw_cond.get("max_wait_time", 10.0),
                            "ticket_priority": raw_cond.get("ticket_priority", False),
                        }
                    mapped_level.append({
                        "category": mapped_cat,
                        "condition": mapped_cond,
                        "count": item.get("count", 1),
                    })
            if mapped_level:
                mapped_order.append(mapped_level)

        def_key = int(default_cat_id) if (isinstance(default_cat_id, int) or (isinstance(default_cat_id, str) and str(default_cat_id).isdigit())) else default_cat_id
        if def_key in cat_id_to_name:
            default_cat_name = cat_id_to_name[def_key]
        elif mapped_order and mapped_order[0]:
            default_cat_name = mapped_order[0][0]["category"]
        elif result["services"]:
            default_cat_name = result["services"][0]["name"]
        else:
            default_cat_name = ""

        result["calling_profiles"].append({
            "id": prof_id,
            "name": prof_name,
            "type": "FIFO",
            "default_category": default_cat_name,
            "order": mapped_order,
        })


def _build_counters_from_total(result):
    """Build counter entries from total_counters when no assignments exist."""
    profile_ids = [p["id"] for p in result["calling_profiles"]]
    total = max(1, result.get("total_counters", 5) or 5)
    first_profile = profile_ids[:1] if profile_ids else []
    for i in range(total):
        result["counters"].append({
            "counter_id": i + 1,
            "name": f"Counter {i + 1}",
            "counter_profiles": first_profile,
            "operator_profiles": [],
        })


def get_eqms_config(branch_id: int):
    # The frontend passes the eQMS branch ID directly.
    # The previous implementation looked up the ID from ICA_BranchMappingNew
    # in the eqReport database, which was an ICA client-specific mapping table
    # that does not exist on other deployments. That lookup caused a hard 500
    # crash for any branch not in that table. Removing it makes the endpoint
    # work for any branch as long as the caller knows the eQMS branch ID.
    session_dash = get_remote_session("eqPortal")

    try:
        result = {
            "branch_id": branch_id,
            "services": [],
            "calling_profiles": [],
            "counters": [],
            "resolution_mode": "counter",
            "category_max_wait_times": {},
        }

        branch_id = int(branch_id)

        # Step B: call USP_GetBranchConfiguration (single call)
        sp_result = session_dash.execute(
            text("EXEC USP_GetBranchConfiguration :bid"),
            {"bid": branch_id},
        )
        raw_result = sp_result.fetchone()
        cols = list(sp_result.keys())

        if not raw_result:
            # Fallback: SP failed (e.g. no BranchShift), query tables directly
            branch = session_dash.execute(
                text("SELECT ID, Name FROM eQPortal_Branch WHERE ID = :bid"),
                {"bid": branch_id},
            ).fetchone()
            if not branch:
                return {"error": f"Branch ID {branch_id} not found in eQPortal"}

            result["branch_id"] = branch_id
            result["eq_branch_name"] = branch[1]
            result["start_hour"] = 0
            result["duration_hours"] = 8
            result["waiting_capacity"] = 0

            # Get categories directly
            categories = session_dash.execute(
                text(
                    "SELECT CategoryId, SQN, LQN, TargetServiceTime, TargetWaitTime "
                    "FROM eQPortal_Category WHERE BranchId = :bid "
                    "AND Active = 1 AND ISNULL(isdeleted,0) = 0 ORDER BY SQN"
                ),
                {"bid": branch_id},
            ).fetchall()

            if not categories:
                return {"error": f"No categories found for branch {branch_id}"}

            cat_id_to_name = {}
            for cat in categories:
                cat_id, sqn, lqn, svc_time, wait_time = cat
                name = str(lqn).strip() if lqn else str(sqn)
                sla = None
                if hasattr(wait_time, "hour"):
                    sla = wait_time.hour * 60 + wait_time.minute
                elif isinstance(wait_time, (int, float)):
                    sla = float(wait_time)
                cat_id_to_name[int(cat_id)] = name
                result["services"].append({
                    "category_id": int(cat_id),
                    "sqn": str(sqn),
                    "name": name,
                    "sla_target_mins": sla or 5.0,
                })
                if name and sla:
                    result["category_max_wait_times"][name] = sla

            # Get profiles directly via HubSetting
            hub = session_dash.execute(
                text(
                    "SELECT HubId, TotalCounters FROM EQPortal_HubSetting "
                    "WHERE BranchId = :bid"
                ),
                {"bid": branch_id},
            ).fetchone()

            profile_count = 0
            if hub:
                hub_id = hub[0]
                result["total_counters"] = hub[1] or 0
                _parse_profiles(session_dash, result, hub_id, cat_id_to_name)
                profile_count = len(result["calling_profiles"])

            if profile_count == 0 and result["services"]:
                svc_names = [s["name"] for s in result["services"]]
                result["calling_profiles"].append({
                    "id": branch_id * 1000 + 1,
                    "name": "Default FIFO",
                    "type": "FIFO",
                    "default_category": svc_names[0],
                    "order": [[{"category": name, "condition": None, "count": 1} for name in svc_names]],
                })
                if not hub:
                    result["total_counters"] = min(15, max(1, len(result["services"]) // 5))
            elif profile_count == 0:
                result["total_counters"] = 0

            _build_counters_from_total(result)

            return result

        sp_data = dict(zip(cols, raw_result))

        result["branch_id"] = branch_id
        result["eq_branch_name"] = sp_data.get("Name", "")

        # Base constraints directly from SP without forced overrides or 12h clamping
        raw_start = sp_data.get("start_hour")
        result["start_hour"] = int(raw_start) if raw_start is not None else 0

        raw_duration = sp_data.get("duration_hours")
        result["duration_hours"] = int(raw_duration) if raw_duration is not None else 8

        raw_cap = sp_data.get("waiting_capacity")
        result["waiting_capacity"] = int(raw_cap) if raw_cap is not None else 0

        # Step C: parse services
        services = _safe_parse_json(sp_data.get("services"))
        cat_id_to_name = {}
        if isinstance(services, list):
            for svc in services:
                if not isinstance(svc, dict):
                    continue
                cat_id = svc.get("categoryId", 0)
                name = str(svc.get("name", svc.get("sqn", ""))).strip()
                sla = svc.get("sla_target_mins")
                if isinstance(sla, (int, float)):
                    sla = float(sla)
                else:
                    sla = 5.0
                cat_id_to_name[int(cat_id)] = name
                result["services"].append({
                    "category_id": int(cat_id),
                    "sqn": str(svc.get("sqn", "")),
                    "name": name,
                    "sla_target_mins": sla,
                })
                if name and sla is not None:
                    result["category_max_wait_times"][name] = sla

        if not result["services"]:
            return {"error": f"No services found for branch {branch_id}"}

        # Patch SLA values from TargetWaitTime (SP uses TargetServiceTime incorrectly)
        try:
            raw_waits = session_dash.execute(
                text(
                    "SELECT CategoryId, "
                    "DATEPART(HOUR, TargetWaitTime)*60 + DATEPART(MINUTE, TargetWaitTime) "
                    "FROM eQPortal_Category WHERE BranchId = :bid AND ISNULL(isDeleted,0)=0"
                ),
                {"bid": branch_id},
            ).fetchall()
            wait_map = {r[0]: float(r[1]) for r in raw_waits if r[1] is not None}
            for svc in result["services"]:
                cid = svc.get("category_id")
                if cid in wait_map:
                    svc["sla_target_mins"] = wait_map[cid]
                    result["category_max_wait_times"][svc["name"]] = wait_map[cid]
        except Exception:
            pass

        # Step D: parse profiles
        profiles = _safe_parse_json(sp_data.get("profiles"))
        profile_ids = []
        if isinstance(profiles, list):
            for prof in profiles:
                if not isinstance(prof, dict):
                    continue
                prof_id = int(prof.get("profileId", 0))
                profile_ids.append(prof_id)
                profile_value = prof.get("profileValue", {})
                if isinstance(profile_value, str):
                    profile_value = _safe_parse_json(profile_value)
                if not isinstance(profile_value, dict):
                    profile_value = {}

                raw_order = profile_value.get("CALL", {}).get("order", [])
                default_cat_id = profile_value.get(
                    "default_category", prof.get("defaultCategoryId", "")
                )

                mapped_order = []
                for level in raw_order:
                    mapped_level = []
                    for item in level:
                        raw_cat = item.get("category", "")
                        cat_key = int(raw_cat) if (isinstance(raw_cat, int) or (isinstance(raw_cat, str) and raw_cat.isdigit())) else raw_cat
                        if cat_key in cat_id_to_name:
                            mapped_cat = cat_id_to_name[cat_key]
                            raw_cond = item.get("condition", "")
                            mapped_cond = None
                            if raw_cond and raw_cond != "" and isinstance(raw_cond, dict):
                                mapped_cond = {
                                    "max_wait_time": raw_cond.get("max_wait_time", 10.0),
                                    "ticket_priority": raw_cond.get("ticket_priority", False),
                                }
                            mapped_level.append({
                                "category": mapped_cat,
                                "condition": mapped_cond,
                                "count": item.get("count", 1),
                            })
                    if mapped_level:
                        mapped_order.append(mapped_level)

                def_key = int(default_cat_id) if (isinstance(default_cat_id, int) or (isinstance(default_cat_id, str) and str(default_cat_id).isdigit())) else default_cat_id
                if def_key in cat_id_to_name:
                    default_cat_name = cat_id_to_name[def_key]
                elif mapped_order and mapped_order[0]:
                    default_cat_name = mapped_order[0][0]["category"]
                elif result["services"]:
                    default_cat_name = result["services"][0]["name"]
                else:
                    default_cat_name = ""
                prof_type = prof.get("profileTypeName", "FIFO")
                if str(prof_type).lower() in ("overflow", "over flow"):
                    prof_type = "Overflow"
                else:
                    prof_type = "FIFO"

                result["calling_profiles"].append({
                    "id": prof_id,
                    "name": prof.get("profileName", f"Profile {prof_id}"),
                    "type": prof_type,
                    "default_category": default_cat_name,
                    "order": mapped_order,
                })

        # Deduplicate identical profiles (same order signature)
        seen = {}
        unique_profiles = []
        for p in result["calling_profiles"]:
            sig = json.dumps({"type": p["type"], "default": p["default_category"], "order": p["order"]}, sort_keys=True)
            if sig not in seen:
                seen[sig] = p["id"]
                unique_profiles.append(p)
        result["calling_profiles"] = unique_profiles

        # Step E: parse counter assignments (use SP data if available)
        counter_assignments = _safe_parse_json(sp_data.get("counterAssignments"))

        print(counter_assignments)
        if isinstance(counter_assignments, list) and counter_assignments:
            valid_profile_ids = {p["id"] for p in result["calling_profiles"]}
            for ctr in counter_assignments:
                if not isinstance(ctr, dict):
                    continue
                counter_id = int(ctr.get("counterId", ctr.get("counterNo", 0)))
                raw_pids = ctr.get("profileIds", [])
                if isinstance(raw_pids, list):
                    ctr_pids = [int(x) for x in raw_pids if str(x).isdigit()]
                elif isinstance(raw_pids, str):
                    ctr_pids = [int(x.strip()) for x in raw_pids.split(",") if x.strip().isdigit()]
                elif isinstance(raw_pids, int):
                    ctr_pids = [raw_pids]
                else:
                    ctr_pids = []

                filtered_pids = [pid for pid in ctr_pids if pid in valid_profile_ids] if valid_profile_ids else ctr_pids

                result["counters"].append({
                    "counter_id": counter_id,
                    "name": str(ctr.get("counterTitle", f"Counter {counter_id}")),
                    "counter_profiles": filtered_pids,
                    "operator_profiles": [],
                })
            result["total_counters"] = len(result["counters"])
        else:
            total = max(1, min(15, len(result["services"]) // 5))
            result["total_counters"] = total
            first_profile = profile_ids[:1] if profile_ids else []
            for i in range(total):
                result["counters"].append({
                    "counter_id": i + 1,
                    "name": f"Counter {i + 1}",
                    "counter_profiles": first_profile,
                    "operator_profiles": [],
                })

        return result

    finally:
        session_dash.close()
