"""Controller Agent package – Strands-based GPU scheduling agent."""

from src.agent.approval import ApprovalConfig, ApprovalHook
from src.agent.main import (
    ALL_TOOLS,
    create_agent,
    decide_next_action,
    generate_plan,
)
from src.agent.prompts import (
    NEXT_ACTION_PROMPT_TEMPLATE,
    PLAN_PROMPT_TEMPLATE,
    SYSTEM_PROMPT,
)
from src.agent.state import (
    append_decision,
    append_instances,
    append_result,
    get_cursor,
    get_decision_trace,
    get_instances,
    get_plan,
    get_region_mode,
    get_remaining,
    get_results,
    init_state,
    set_cursor,
    set_plan,
    set_region_mode,
    set_remaining,
)

__all__ = [
    "ALL_TOOLS",
    "ApprovalConfig",
    "ApprovalHook",
    "NEXT_ACTION_PROMPT_TEMPLATE",
    "PLAN_PROMPT_TEMPLATE",
    "SYSTEM_PROMPT",
    "append_decision",
    "append_instances",
    "append_result",
    "create_agent",
    "decide_next_action",
    "generate_plan",
    "get_cursor",
    "get_decision_trace",
    "get_instances",
    "get_plan",
    "get_region_mode",
    "get_remaining",
    "get_results",
    "init_state",
    "set_cursor",
    "set_plan",
    "set_region_mode",
    "set_remaining",
]
