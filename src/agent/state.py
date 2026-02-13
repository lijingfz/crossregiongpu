"""Agent State management helpers.

Provides typed read/write access to the Strands AgentState key-value store
for plan, remaining, cursor, results, instances, and decision_trace.

The AgentState is a key-value store that lives outside the model's context
window, saving tokens while keeping execution progress recoverable.

Requirements: 10.1, 10.2, 10.3, 10.4
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from strands import Agent

from src.models.schemas import (
    InstanceInfo,
    NextAction,
    Plan,
    StepResult,
)

# ---------------------------------------------------------------------------
# State keys
# ---------------------------------------------------------------------------

KEY_PLAN = "plan"
KEY_REGION_MODE = "region_mode"
KEY_REMAINING = "remaining"
KEY_CURSOR = "cursor"
KEY_RESULTS = "results"
KEY_INSTANCES = "instances"
KEY_DECISION_TRACE = "decision_trace"


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def set_plan(agent: Agent, plan: Plan) -> None:
    """Store the execution plan in agent state."""
    agent.state.set(KEY_PLAN, plan.model_dump())


def set_remaining(agent: Agent, remaining: int) -> None:
    """Update the remaining instance count."""
    agent.state.set(KEY_REMAINING, remaining)


def set_cursor(agent: Agent, cursor: int) -> None:
    """Update the current region cursor index."""
    agent.state.set(KEY_CURSOR, cursor)


def set_region_mode(agent: Agent, mode: str) -> None:
    """Store the region scheduling mode (multi_region / single_region)."""
    agent.state.set(KEY_REGION_MODE, mode)


def append_result(agent: Agent, result: StepResult) -> None:
    """Append a step result to the results list in state."""
    results = agent.state.get(KEY_RESULTS) or []
    results.append(result.model_dump())
    agent.state.set(KEY_RESULTS, results)


def append_instances(agent: Agent, instances: List[InstanceInfo]) -> None:
    """Append launched instances to the instances list in state."""
    existing = agent.state.get(KEY_INSTANCES) or []
    existing.extend(inst.model_dump() for inst in instances)
    agent.state.set(KEY_INSTANCES, existing)


def append_decision(agent: Agent, decision: NextAction) -> None:
    """Append a decision to the decision trace in state."""
    trace = agent.state.get(KEY_DECISION_TRACE) or []
    trace.append(decision.model_dump())
    agent.state.set(KEY_DECISION_TRACE, trace)


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def get_plan(agent: Agent) -> Optional[Plan]:
    """Retrieve the plan from agent state, or None if not set."""
    raw = agent.state.get(KEY_PLAN)
    if raw is None:
        return None
    return Plan(**raw)


def get_remaining(agent: Agent) -> int:
    """Retrieve the remaining count (defaults to 0)."""
    return agent.state.get(KEY_REMAINING) or 0


def get_cursor(agent: Agent) -> int:
    """Retrieve the current region cursor (defaults to 0)."""
    return agent.state.get(KEY_CURSOR) or 0


def get_region_mode(agent: Agent) -> str:
    """Retrieve the region mode (defaults to 'multi_region')."""
    return agent.state.get(KEY_REGION_MODE) or "multi_region"


def get_results(agent: Agent) -> List[StepResult]:
    """Retrieve all step results from state."""
    raw = agent.state.get(KEY_RESULTS) or []
    return [StepResult(**r) for r in raw]


def get_instances(agent: Agent) -> List[InstanceInfo]:
    """Retrieve all launched instances from state."""
    raw = agent.state.get(KEY_INSTANCES) or []
    return [InstanceInfo(**i) for i in raw]


def get_decision_trace(agent: Agent) -> List[NextAction]:
    """Retrieve the full decision trace from state."""
    raw = agent.state.get(KEY_DECISION_TRACE) or []
    return [NextAction(**d) for d in raw]


# ---------------------------------------------------------------------------
# Bulk initializer
# ---------------------------------------------------------------------------

def init_state(
    agent: Agent,
    *,
    plan: Plan,
    remaining: int,
    cursor: int = 0,
) -> None:
    """Initialize all state keys for a new scheduling run."""
    set_plan(agent, plan)
    set_region_mode(agent, plan.region_mode)
    set_remaining(agent, remaining)
    set_cursor(agent, cursor)
    agent.state.set(KEY_RESULTS, [])
    agent.state.set(KEY_INSTANCES, [])
    agent.state.set(KEY_DECISION_TRACE, [])
