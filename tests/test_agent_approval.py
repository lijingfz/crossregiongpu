"""Tests for src/agent/approval.py – Human-in-the-loop approval hook."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.agent.approval import ApprovalConfig, ApprovalHook


def _make_event(tool_name: str = "ec2_launch_instances", **tool_input) -> MagicMock:
    """Create a mock BeforeToolCallEvent."""
    event = MagicMock()
    event.tool_use = {"name": tool_name, "input": tool_input, "toolUseId": "t-1"}
    event.cancel_tool = False
    return event


class TestApprovalHookBatchThreshold:
    def test_below_threshold_passes(self):
        hook = ApprovalHook(ApprovalConfig(batch_threshold=20))
        event = _make_event(target_count=10)
        hook._check_approval(event)
        assert event.cancel_tool is False

    def test_above_threshold_cancels(self):
        hook = ApprovalHook(ApprovalConfig(batch_threshold=20))
        event = _make_event(target_count=25)
        hook._check_approval(event)
        assert event.cancel_tool  # truthy string
        assert "25" in event.cancel_tool
        assert "threshold" in event.cancel_tool.lower()


class TestApprovalHookGeoRegion:
    def test_allowed_region_passes(self):
        hook = ApprovalHook(ApprovalConfig(
            allowed_geo_regions={"us-east-1", "us-west-2"},
        ))
        event = _make_event(region="us-east-1", target_count=2)
        hook._check_approval(event)
        assert event.cancel_tool is False

    def test_disallowed_region_cancels(self):
        hook = ApprovalHook(ApprovalConfig(
            allowed_geo_regions={"us-east-1", "us-west-2"},
        ))
        event = _make_event(region="eu-west-1", target_count=2)
        hook._check_approval(event)
        assert event.cancel_tool
        assert "eu-west-1" in event.cancel_tool

    def test_empty_allowed_set_passes_all(self):
        hook = ApprovalHook(ApprovalConfig(allowed_geo_regions=set()))
        event = _make_event(region="af-south-1", target_count=2)
        hook._check_approval(event)
        assert event.cancel_tool is False


class TestApprovalHookAlwaysApprove:
    def test_always_approve_tool_cancels(self):
        hook = ApprovalHook(ApprovalConfig(
            always_approve_tools={"dangerous_tool"},
        ))
        event = _make_event(tool_name="dangerous_tool")
        hook._check_approval(event)
        assert event.cancel_tool
        assert "dangerous_tool" in event.cancel_tool

    def test_normal_tool_passes(self):
        hook = ApprovalHook(ApprovalConfig(
            always_approve_tools={"dangerous_tool"},
        ))
        event = _make_event(tool_name="ec2_launch_instances", target_count=2)
        hook._check_approval(event)
        assert event.cancel_tool is False
