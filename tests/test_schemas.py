"""Tests for src/models/schemas.py – core data models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models.schemas import (
    AZConfig,
    InstanceInfo,
    NextAction,
    Plan,
    PlanStep,
    RegionConfig,
    StepResult,
)


class TestPlanStep:
    def test_minimal_creation(self):
        step = PlanStep(id="s1", title="Launch in ap-south-1")
        assert step.id == "s1"
        assert step.tool == "ec2_launch_instances"
        assert step.risk_level == "low"

    def test_risk_level_validation(self):
        with pytest.raises(ValidationError):
            PlanStep(id="s1", title="bad", risk_level="critical")


class TestPlan:
    def test_plan_creation(self):
        plan = Plan(
            goal="Launch 10 g6.xlarge",
            instance_type="g6.xlarge",
            total_count=10,
            preferred_regions=["ap-south-1", "us-east-1"],
        )
        assert plan.total_count == 10
        assert plan.stop_conditions == ["remaining=0", "regions_exhausted"]

    def test_plan_with_steps(self):
        step = PlanStep(id="s1", title="Step 1")
        plan = Plan(
            goal="test",
            instance_type="g5.xlarge",
            total_count=5,
            preferred_regions=["us-west-2"],
            steps=[step],
        )
        assert len(plan.steps) == 1


class TestStepResult:
    def test_full_status(self):
        result = StepResult(
            status="FULL",
            requested=4,
            launched=4,
            remaining=0,
            region="ap-south-1",
        )
        assert result.status == "FULL"
        assert result.instances == []

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError):
            StepResult(
                status="UNKNOWN",
                requested=1,
                launched=0,
                remaining=1,
                region="us-east-1",
            )


class TestInstanceInfo:
    def test_creation_with_optional_public_ip(self):
        info = InstanceInfo(
            instance_id="i-abc123",
            instance_type="g6.xlarge",
            az="ap-south-1a",
            private_ip="10.0.0.1",
        )
        assert info.public_ip is None

    def test_creation_with_public_ip(self):
        info = InstanceInfo(
            instance_id="i-abc123",
            instance_type="g6.xlarge",
            az="ap-south-1a",
            private_ip="10.0.0.1",
            public_ip="54.1.2.3",
        )
        assert info.public_ip == "54.1.2.3"


class TestNextAction:
    def test_done_action(self):
        na = NextAction(action="done", final_summary="All launched")
        assert na.action == "done"

    def test_continue_action(self):
        na = NextAction(
            action="continue_next_region",
            next_region="us-east-1",
            desired_count=3,
        )
        assert na.next_region == "us-east-1"

    def test_invalid_action_rejected(self):
        with pytest.raises(ValidationError):
            NextAction(action="explode")


class TestRegionConfig:
    def test_region_with_azs(self):
        rc = RegionConfig(
            region="ap-south-1",
            priority=1,
            azs=[AZConfig(az_name="ap-south-1a", subnets=["subnet-001"])],
        )
        assert len(rc.azs) == 1
        assert rc.azs[0].subnets == ["subnet-001"]

    def test_default_priority(self):
        rc = RegionConfig(region="us-west-2")
        assert rc.priority == 0
        assert rc.azs == []
