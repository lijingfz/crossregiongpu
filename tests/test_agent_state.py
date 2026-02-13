"""Tests for src/agent/state.py – Agent State read/write helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from strands.agent.state import AgentState

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
from src.models.schemas import (
    InstanceInfo,
    NextAction,
    Plan,
    PlanStep,
    StepResult,
)


def _make_agent_with_state() -> MagicMock:
    """Create a mock Agent with a real AgentState."""
    agent = MagicMock()
    agent.state = AgentState()
    return agent


def _sample_plan() -> Plan:
    return Plan(
        goal="Launch 4 g6.xlarge",
        instance_type="g6.xlarge",
        total_count=4,
        preferred_regions=["ap-south-1", "us-east-1"],
        region_mode="multi_region",
        steps=[
            PlanStep(id="s1", title="Launch in ap-south-1"),
            PlanStep(id="s2", title="Launch in us-east-1"),
            PlanStep(id="s3", title="Finalize"),
        ],
    )


class TestStateWriteAndRead:
    def test_plan_roundtrip(self):
        agent = _make_agent_with_state()
        plan = _sample_plan()
        set_plan(agent, plan)
        recovered = get_plan(agent)
        assert recovered is not None
        assert recovered.goal == plan.goal
        assert recovered.total_count == 4
        assert len(recovered.steps) == 3

    def test_remaining_roundtrip(self):
        agent = _make_agent_with_state()
        set_remaining(agent, 7)
        assert get_remaining(agent) == 7

    def test_cursor_roundtrip(self):
        agent = _make_agent_with_state()
        set_cursor(agent, 2)
        assert get_cursor(agent) == 2

    def test_region_mode_roundtrip(self):
        agent = _make_agent_with_state()
        set_region_mode(agent, "single_region")
        assert get_region_mode(agent) == "single_region"

    def test_defaults_when_empty(self):
        agent = _make_agent_with_state()
        assert get_plan(agent) is None
        assert get_remaining(agent) == 0
        assert get_cursor(agent) == 0
        assert get_region_mode(agent) == "multi_region"
        assert get_results(agent) == []
        assert get_instances(agent) == []
        assert get_decision_trace(agent) == []


class TestAppendHelpers:
    def test_append_result(self):
        agent = _make_agent_with_state()
        agent.state.set("results", [])
        r1 = StepResult(
            status="PARTIAL", requested=4, launched=2,
            remaining=2, region="us-east-1", message="ok",
        )
        r2 = StepResult(
            status="FULL", requested=2, launched=2,
            remaining=0, region="us-west-2", message="done",
        )
        append_result(agent, r1)
        append_result(agent, r2)
        results = get_results(agent)
        assert len(results) == 2
        assert results[0].status == "PARTIAL"
        assert results[1].status == "FULL"

    def test_append_instances(self):
        agent = _make_agent_with_state()
        agent.state.set("instances", [])
        insts = [
            InstanceInfo(
                instance_id="i-001", instance_type="g6.xlarge",
                az="us-east-1a", private_ip="10.0.0.1",
            ),
        ]
        append_instances(agent, insts)
        recovered = get_instances(agent)
        assert len(recovered) == 1
        assert recovered[0].instance_id == "i-001"

    def test_append_decision(self):
        agent = _make_agent_with_state()
        agent.state.set("decision_trace", [])
        d = NextAction(action="done", rationale="all launched")
        append_decision(agent, d)
        trace = get_decision_trace(agent)
        assert len(trace) == 1
        assert trace[0].action == "done"


class TestInitState:
    def test_init_sets_all_keys(self):
        agent = _make_agent_with_state()
        plan = _sample_plan()
        init_state(agent, plan=plan, remaining=4)

        assert get_plan(agent) is not None
        assert get_remaining(agent) == 4
        assert get_cursor(agent) == 0
        assert get_region_mode(agent) == "multi_region"
        assert get_results(agent) == []
        assert get_instances(agent) == []
        assert get_decision_trace(agent) == []
