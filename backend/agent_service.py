"""
Agent service for discrete event simulation optimization and queue analysis.
Utilizes Ollama Cloud and LangGraph to build a stateful ReAct agent.
"""

import json
import os
from typing import Optional, Union

from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

# Setup shared in-memory conversation checkpointer
agent_checkpointer = MemorySaver()

# Default hourly inflows profiles
DEFAULT_HOURLY_PROFILE = [0.05, 0.08, 0.12, 0.15, 0.18, 0.15, 0.12, 0.10, 0.05]


@tool
def simulate_operations_tool(config_str: Union[str, dict]) -> str:
    """
    Runs a discrete event simulation (DES) on a branch layout counter configuration.

    Args:
        config_str (Union[str, dict]): A JSON-formatted string or dictionary containing the counter and workgroup configuration.
                          It must have the following keys:
                          - 'branch_name': name of the target branch (str)
                          - 'start_hour': start hour in 24h format (int, e.g. 9)
                          - 'duration_hours': duration of operation (int, e.g. 8)
                          - 'waiting_capacity': maximum queue capacity (int, e.g. 50)
                          - 'inflow_type': set to 'hourly_flow' or 'ai_forecast' (str)
                          - 'hourly_inflows': list of integer inflow counts per hour (e.g. [30, 35, 48, 59, 66, 60, 49, 38])
                          - 'services': list of services, each with:
                             - 'name' (str)
                             - 'ratio' (float, 0 to 1)
                             - 'sla_target_mins' (float)
                             - 'mean_service_time_mins' (float)
                             - 'std_dev_service_time_mins' (float)
                          - 'workgroups': list of workgroups, each with:
                             - 'name' (str)
                             - 'counter_count' (int)
                             - 'skills': list of service skills, each with:
                                - 'service_name' (str)
                                - 'is_active' (bool)
                                - 'priority' (int, 1-5)
                                - 'sla_target_mins' (float)

    Returns:
        str: JSON string containing averaged simulation results (overall SLA compliance %, avg wait time, teller utilization %).
    """
    try:
        if isinstance(config_str, str):
            config = json.loads(config_str)
        elif isinstance(config_str, dict):
            config = config_str
        else:
            return json.dumps(
                {
                    "success": False,
                    "error": "config_str must be a valid JSON string or dictionary.",
                }
            )

        from database import get_branch_id_by_name
        from simulation import run_simulation

        b_name = config.get("branch_name")
        config["branch_id"] = 0
        if b_name:
            resolved_id = get_branch_id_by_name(b_name)
            if resolved_id:
                config["branch_id"] = resolved_id

        # We run with 20 trials inside the agent for faster response latency
        results = run_simulation(config, num_trials=20)
        summary = results.get("summary", {})

        return json.dumps(
            {
                "success": True,
                "overall_sla_compliance_pct": summary.get(
                    "overall_sla_compliance_pct", 0.0
                ),
                "avg_wait_time_mins": summary.get("avg_wait_time_mins", 0.0),
                "max_wait_time_mins": summary.get("max_wait_time_mins", 0.0),
                "avg_teller_utilization_pct": summary.get(
                    "avg_teller_utilization_pct", 0.0
                ),
                "total_customers_arrived": summary.get("total_customers_arrived", 0),
                "total_customers_served": summary.get("total_customers_served", 0),
                "total_customers_balked": summary.get("total_customers_balked", 0),
            }
        )
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@tool
# Oky here the month is defaulting to january, can be a problem.
# i mean shoudn't we pick up the month with the most best overall ticket predcition from the 12 months
# or the criteria should be something different idk, this needs to be explored later.
# Plus the default hardcoded year is 2026, this should be the latest pridcted year(thats what i think).
def fetch_branch_forecast_tool(
    branch_name: str, year: int = 2026, month: int = 1
) -> str:
    """
    Fetches the AI predicted traffic forecast for a branch in a given month.
    Use this tool to discover expected customer daily arrival volumes.

    Args:
        branch_name (str): Name of the branch.
        year (int): Year of the forecast (defaults to 2026).
        month (int): Month index, 1-12 (defaults to 1).

    Returns:
        str: JSON string containing a list of daily predictions, or an error message.
    """
    try:
        from database import get_branch_id_by_name, get_latest_forecasts

        branch_id = get_branch_id_by_name(branch_name)
        if not branch_id:
            return json.dumps({"error": f"Branch '{branch_name}' not found."})

        forecasts = get_latest_forecasts(
            branch_id, category_id=0, month=month, year=year
        )
        if not forecasts:
            return json.dumps(
                {"warning": "No forecasts found for this branch and date range."}
            )

        # Clean down response payload for context length optimization
        minimal_forecasts = []
        for r in forecasts[:31]:
            minimal_forecasts.append(
                {
                    "date": r.get("date"),
                    "day_of_week": r.get("day_of_week"),
                    "predicted": r.get("predicted"),
                }
            )

        return json.dumps(
            {
                "branch_name": branch_name,
                "year": year,
                "month": month,
                "daily_forecast_summary": minimal_forecasts,
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


SYSTEM_PROMPT = """You are an advanced Operational Planning and Queue Analytics AI assistant for ICP (Federal Authority for Identity, Citizenship, Customs and Port Security) branches in the UAE.

Your goal is to analyze queue performance, identify bottlenecks, and suggest optimal seating/counter layouts to ensure high SLA compliance (overall target is 90%+) at minimum teller staffing costs.

You have access to two powerful tools:
1. `simulate_operations_tool`: Runs a stochastic discrete event simulation (DES) on the branch's queue operations (with Poisson arrivals, priority VIP routing, and log-normal transaction times). Accepts a JSON-like counter and services configuration, and returns expected operational performance metrics (SLA %, wait times, teller utilization).
2. `fetch_branch_forecast_tool`: Queries predicted daily arrival volume forecasts from our SQLite database. Use this first to check how many customers are expected to arrive before running simulation stress tests.

GUIDELINES FOR YOUR OPTIMIZATION WORKFLOW:
- When a user asks you to optimize or analyze, ALWAYS check the expected arrival forecast first using `fetch_branch_forecast_tool`.
- Then, try running a simulation test using `simulate_operations_tool` with the baseline layout or your adjusted layout.
- You can run multiple simulations iteratively (up to 3-5 trials) to see how changing counter seats, service ratios, or priority weights impacts SLA compliance and teller utilization.
- Make data-driven, mathematically sound recommendations (e.g., "moving 1 counter from cash deposit to wealth management improves wealth SLA by 15% without hurting deposit SLA").

COMMUNICATION STYLE & COGNITIVE LOAD REDUCTION RULES:
- SPEAK LIKE A PREMIUM CHIEF OF OPERATIONS ADVISOR: Be highly professional, concise, direct, and actionable. Avoid academic clutter, verbose rationale, or overwhelming data dumps.
- NEVER OUTPUT RAW FORECAST LISTS OR TABLES: Summarize forecast insights in 1-2 sentences (e.g., "We expect a peak load of 403 daily arrivals on Monday, Jan 5"). Dumping lists of dates and raw forecast numbers is forbidden.
- NEVER OUTPUT RAW TRIAL REPEATED COMPARISON TABLES: Do not output markdown tables comparing multiple simulated trials. Only output the final winning layout recommendations.
- USE A HIGH-IMPACT BEFORE vs. AFTER KPI SUMMARY: Always summarize the operational improvement in a crisp, highly readable list:
  * **SLA Compliance**: Baseline_SLA ➔ **Optimized_SLA** (Target: 90%+)
  * **Average Wait Time**: Baseline_Wait ➔ **Optimized_Wait**
  * **Balked Customers**: Baseline_Balked ➔ **0 (Resolved)**
  * **Teller Utilization**: Baseline_Util ➔ **Optimized_Util** (Balanced workload)
- ACTIONABLE RECOMMENDATIONS: Keep your recommended seating/layout adjustments to 2-3 direct, bold bullet points explaining exactly what changes are being applied.

IMPORTANT OUTPUT RULE:
Whenever you propose an optimized counter layout, you MUST FIRST write a clear, concise, human-readable summary of the optimization plan using the COMMUNICATION STYLE & COGNITIVE LOAD REDUCTION RULES above (insight, recommendations, and high-impact Before ➔ After comparison).
ONLY AFTER this human-readable explanation, you MUST append the final recommended configuration as a JSON block wrapped inside standard ````json ... ```` tags. The JSON block MUST have this structure:
```json
{
  "type": "simulation_config",
  "start_hour": 9,
  "duration_hours": 8,
  "waiting_capacity": 50,
  "inflow_type": "hourly_flow",
  "hourly_inflows": [30, 35, 48, 59, 66, 60, 49, 38],
  "services": [
    { "name": "Cash Deposit", "ratio": 0.40, "sla_target_mins": 5, "mean_service_time_mins": 8, "std_dev_service_time_mins": 3 },
    { "name": "General Inquiries", "ratio": 0.20, "sla_target_mins": 3, "mean_service_time_mins": 10, "std_dev_service_time_mins": 4 },
    { "name": "New Accounts", "ratio": 0.10, "sla_target_mins": 15, "mean_service_time_mins": 15, "std_dev_service_time_mins": 5 },
    { "name": "Wealth Management", "ratio": 0.10, "sla_target_mins": 5, "mean_service_time_mins": 12, "std_dev_service_time_mins": 4 }
  ],
  "workgroups": [
    {
      "name": "Primary Cluster",
      "counter_count": 6,
      "skills": [
        { "service_name": "Cash Deposit", "is_active": true, "priority": 3, "sla_target_mins": 15 },
        { "service_name": "General Inquiries", "is_active": true, "priority": 3, "sla_target_mins": 15 },
        { "service_name": "New Accounts", "is_active": true, "priority": 3, "sla_target_mins": 15 },
        { "service_name": "Wealth Management", "is_active": true, "priority": 3, "sla_target_mins": 15 }
      ]
    }
  ]
}
```
Ensure all fields are fully populated and reflect the recommended optimizations so that the frontend can dynamically apply it.
"""


def get_llm_model():
    """
    Decoupled factory to instantiate the ChatModel based on .env config.
    Supports native ChatOllama and OpenAI-compatible ChatOpenAI endpoints.
    """
    provider = os.getenv("AGENT_PROVIDER", "ollama").lower()
    base_url = os.getenv("OLLAMA_BASE_URL", "https://ollama.com")
    model_name = os.getenv("OLLAMA_MODEL", "gpt-oss:20b-cloud")
    api_key = os.getenv("OLLAMA_API_KEY", "")

    if provider == "ollama":
        if "v1" in base_url:
            # OpenAI-compatible API Endpoint
            return ChatOpenAI(
                base_url=base_url,
                api_key=api_key if api_key else "ollama",
                model=model_name,
                temperature=0.2,
            )
        else:
            # Native Ollama API
            client_kwargs = {}
            if api_key:
                client_kwargs["headers"] = {"Authorization": f"Bearer {api_key}"}

            return ChatOllama(
                base_url=base_url,
                model=model_name,
                temperature=0.2,
                client_kwargs=client_kwargs if client_kwargs else None,
            )
    else:
        # Fallback to standard OpenAI GPT-4o if configured
        openai_key = os.getenv("OPENAI_API_KEY", "")
        return ChatOpenAI(
            api_key=openai_key,
            model=os.getenv("DEFAULT_MODEL", "gpt-4o"),
            temperature=0.2,
        )


def initialize_agent():
    """
    Compile the stateful LangGraph Prebuilt React Agent.
    """
    chat_model = get_llm_model()
    tools = [simulate_operations_tool, fetch_branch_forecast_tool]

    agent_graph = create_react_agent(
        model=chat_model,
        tools=tools,
        checkpointer=agent_checkpointer,
        prompt=SYSTEM_PROMPT,
    )
    return agent_graph
