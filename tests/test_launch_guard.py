"""Unit tests for LaunchGuardHook.

Validates that the hook correctly:
- Blocks ec2_launch_instances when target is met (self-arming fulfilled flag)
- Blocks ec2_launch_instances when call ceiling is reached
- Tracks cumulative launched count from AfterToolCallEvent
- Resets state between invocations
- Ignores non-launch tool calls
- Blocks re-invocations that bypass the entrypoint (no reset)
- Allows genuine duplicate user requests after reset

Requirements: 3.7 (idempotency / safety)
"""

from __future__ import annotations

import pytest

from src.agent.launch_guard import DEFAULT_MAX_LAUNCH_CALLS, LaunchGuardHook


# ---------------------------------------------------------------------------
# Helpers: fake event objects that mimic Strands hook events
# ---------------------------------------------------------------------------

class FakeBeforeToolCallEvent:
    """Mimics strands.hooks.BeforeToolCallEvent."""

    def __init__(self, tool_use: dict):
        self.tool_use = tool_use
        self.cancel_tool: str | None = None


class FakeAfterToolCallEvent:
    """Mimics strands.hooks.AfterToolCallEvent."""

    def __init__(self, tool_use: dict, result: dict | None = None):
        self.tool_use = tool_use
        self.result = result


def _make_launch_before(
    target_count: int = 3,
    instance_type: str = "g6.xlarge",
) -> FakeBeforeToolCallEvent:
    return FakeBeforeToolCallEvent(
        tool_use={
            "name": "ec2_launch_instances",
            "input": {
                "target_count": target_count,
                "instance_type": instance_type,
                "region": "ap-southeast-1",
            },
        }
    )


def _make_launch_after(launched: int = 3, requested: int = 3) -> FakeAfterToolCallEvent:
    """Create a fake AfterToolCallEvent for ec2_launch_instances.

    Strands wraps StepResult.model_dump() as text (not json) because
    the dict has "status" but no "content" key. We simulate both formats.
    """
    # This is the actual format Strands produces: str(dict) in text field
    step_result_dict = {
        "status": "FULL" if launched >= requested else "PARTIAL",
        "launched": launched,
        "requested": requested,
        "remaining": requested - launched,
    }
    return FakeAfterToolCallEvent(
        tool_use={"name": "ec2_launch_instances"},
        result={
            "status": "success",
            "content": [
                {
                    "text": str(step_result_dict),
                }
            ],
        },
    )


def _make_launch_after_json(launched: int = 3, requested: int = 3) -> FakeAfterToolCallEvent:
    """Create a fake AfterToolCallEvent with json content (pre-formatted ToolResult)."""
    return FakeAfterToolCallEvent(
        tool_use={"name": "ec2_launch_instances"},
        result={
            "status": "success",
            "content": [
                {
                    "json": {
                        "status": "FULL",
                        "launched": launched,
                        "requested": requested,
                        "remaining": requested - launched,
                    }
                }
            ],
        },
    )


def _make_other_before(tool_name: str = "finalize") -> FakeBeforeToolCallEvent:
    return FakeBeforeToolCallEvent(tool_use={"name": tool_name, "input": {}})


def _make_other_after(tool_name: str = "finalize") -> FakeAfterToolCallEvent:
    return FakeAfterToolCallEvent(tool_use={"name": tool_name}, result=None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLaunchGuardHookBasic:
    """Basic behavior of LaunchGuardHook."""

    def test_first_call_allowed(self):
        """First launch call should not be blocked."""
        guard = LaunchGuardHook()
        event = _make_launch_before(target_count=3)
        guard._before_launch(event)
        assert event.cancel_tool is None

    def test_non_launch_tool_ignored_before(self):
        """Non-launch tools should pass through without blocking."""
        guard = LaunchGuardHook()
        event = _make_other_before("finalize")
        guard._before_launch(event)
        assert event.cancel_tool is None

    def test_non_launch_tool_ignored_after(self):
        """Non-launch tools should not increment counters."""
        guard = LaunchGuardHook()
        guard._after_launch(_make_other_after("finalize"))
        assert guard._launch_call_count == 0
        assert guard._total_launched == 0

    def test_default_max_launch_calls(self):
        """Default max_launch_calls should be DEFAULT_MAX_LAUNCH_CALLS."""
        guard = LaunchGuardHook()
        assert guard.max_launch_calls == DEFAULT_MAX_LAUNCH_CALLS

    def test_custom_max_launch_calls(self):
        """Custom max_launch_calls should be respected."""
        guard = LaunchGuardHook(max_launch_calls=3)
        assert guard.max_launch_calls == 3


class TestLaunchGuardHookBlocking:
    """Tests that the hook blocks launches correctly."""

    def test_blocks_after_target_met(self):
        """Once target is met, further launch calls should be blocked."""
        guard = LaunchGuardHook()

        # First call: before (captures target=3)
        guard._before_launch(_make_launch_before(target_count=3))
        # After: 3 launched → fulfilled flag armed
        guard._after_launch(_make_launch_after(launched=3, requested=3))

        # Second call should be blocked by fulfilled flag
        event = _make_launch_before(target_count=3)
        guard._before_launch(event)
        assert event.cancel_tool is not None
        assert "LAUNCH BLOCKED" in event.cancel_tool

    def test_blocks_after_partial_fills_meet_target(self):
        """Multiple partial launches that sum to target should block."""
        guard = LaunchGuardHook()

        # Call 1: target=5, launched=2
        guard._before_launch(_make_launch_before(target_count=5))
        guard._after_launch(_make_launch_after(launched=2, requested=5))

        # Call 2: launched=3 more (total=5, meets target)
        before2 = _make_launch_before(target_count=3)
        guard._before_launch(before2)
        assert before2.cancel_tool is None  # still allowed
        guard._after_launch(_make_launch_after(launched=3, requested=3))

        # Call 3: should be blocked (5 >= 5, fulfilled flag armed)
        before3 = _make_launch_before(target_count=3)
        guard._before_launch(before3)
        assert before3.cancel_tool is not None
        assert "LAUNCH BLOCKED" in before3.cancel_tool

    def test_blocks_at_call_ceiling(self):
        """Should block when call count reaches max_launch_calls."""
        guard = LaunchGuardHook(max_launch_calls=2)

        # Call 1
        guard._before_launch(_make_launch_before(target_count=10))
        guard._after_launch(_make_launch_after(launched=1, requested=10))

        # Call 2
        guard._before_launch(_make_launch_before(target_count=9))
        guard._after_launch(_make_launch_after(launched=1, requested=9))

        # Call 3: should be blocked (2 calls already made)
        event = _make_launch_before(target_count=8)
        guard._before_launch(event)
        assert event.cancel_tool is not None
        assert "LAUNCH BLOCKED" in event.cancel_tool
        assert "2 times" in event.cancel_tool


class TestLaunchGuardHookTracking:
    """Tests for cumulative tracking logic."""

    def test_tracks_launched_count(self):
        """_total_launched should accumulate across calls."""
        guard = LaunchGuardHook()

        guard._before_launch(_make_launch_before(target_count=10))
        guard._after_launch(_make_launch_after(launched=3, requested=10))
        assert guard._total_launched == 3
        assert guard._launch_call_count == 1

        guard._after_launch(_make_launch_after(launched=2, requested=10))
        assert guard._total_launched == 5
        assert guard._launch_call_count == 2

    def test_captures_target_from_first_before(self):
        """_target_count should be set from the first before event."""
        guard = LaunchGuardHook()
        guard._before_launch(_make_launch_before(target_count=7))
        assert guard._target_count == 7

    def test_captures_target_from_after_if_missed(self):
        """If target wasn't captured in before, capture from after result."""
        guard = LaunchGuardHook()
        # Skip before, go straight to after
        guard._after_launch(_make_launch_after(launched=3, requested=5))
        assert guard._target_count == 5

    def test_does_not_overwrite_target(self):
        """Once target is set, subsequent calls should not change it."""
        guard = LaunchGuardHook()
        guard._before_launch(_make_launch_before(target_count=3))
        assert guard._target_count == 3

        # Second call with different target_count should not overwrite
        guard._before_launch(_make_launch_before(target_count=10))
        assert guard._target_count == 3

    def test_fulfilled_flag_armed_on_target_met(self):
        """_fulfilled should be set to True when _total_launched >= _target_count."""
        guard = LaunchGuardHook()
        assert guard._fulfilled is False

        guard._before_launch(_make_launch_before(target_count=3))
        guard._after_launch(_make_launch_after(launched=3, requested=3))
        assert guard._fulfilled is True


class TestLaunchGuardHookReset:
    """Tests for the reset() method."""

    def test_reset_clears_per_invocation_state(self):
        """reset() should zero out all counters including fulfilled flag."""
        guard = LaunchGuardHook()

        guard._before_launch(_make_launch_before(target_count=3))
        guard._after_launch(_make_launch_after(launched=3, requested=3))
        assert guard._launch_call_count == 1
        assert guard._total_launched == 3
        assert guard._target_count == 3
        assert guard._fulfilled is True

        guard.reset()
        assert guard._launch_call_count == 0
        assert guard._total_launched == 0
        assert guard._target_count == 0
        assert guard._fulfilled is False

    def test_reset_session_clears_everything(self):
        """reset_session() should clear all state (alias for reset)."""
        guard = LaunchGuardHook()

        guard._before_launch(_make_launch_before(target_count=3))
        guard._after_launch(_make_launch_after(launched=3, requested=3))

        guard.reset_session()
        assert guard._launch_call_count == 0
        assert guard._total_launched == 0
        assert guard._target_count == 0
        assert guard._fulfilled is False

    def test_allows_different_task_after_reset(self):
        """After reset, a different task (different instance_type) should be allowed."""
        guard = LaunchGuardHook()

        # Complete a g6.xlarge task
        guard._before_launch(_make_launch_before(target_count=3, instance_type="g6.xlarge"))
        guard._after_launch(_make_launch_after(launched=3, requested=3))
        guard.reset()

        # A different instance type should be allowed
        allowed = _make_launch_before(target_count=5, instance_type="g5.xlarge")
        guard._before_launch(allowed)
        assert allowed.cancel_tool is None
        assert guard._target_count == 5

    def test_allows_same_task_after_reset(self):
        """After reset, the same task fingerprint should be ALLOWED.

        This supports the user sending "launch 3 g6.xlarge" twice,
        expecting 6 total instances. reset() clears the fulfilled flag,
        so the second identical command proceeds normally.
        """
        guard = LaunchGuardHook()

        # Complete a g6.xlarge x 4 task
        guard._before_launch(_make_launch_before(target_count=4, instance_type="g6.xlarge"))
        guard._after_launch(_make_launch_after(launched=4, requested=4))
        guard.reset()

        # Same fingerprint should now be ALLOWED (no session dedup)
        allowed = _make_launch_before(target_count=4, instance_type="g6.xlarge")
        guard._before_launch(allowed)
        assert allowed.cancel_tool is None


class TestLaunchGuardReInvocation:
    """Tests for AgentCore Runtime re-invocation protection.

    Re-invocations bypass the entrypoint, so reset() is never called.
    The fulfilled flag must block these without external help.
    """

    def test_blocks_re_invocation_without_reset(self):
        """After target met, launch is blocked even without reset() being called."""
        guard = LaunchGuardHook()

        # First invocation: launch 3, target met
        guard._before_launch(_make_launch_before(target_count=3))
        guard._after_launch(_make_launch_after(launched=3, requested=3))
        assert guard._fulfilled is True

        # Re-invocation: NO reset() called — simulates AgentCore re-invoke
        event = _make_launch_before(target_count=3)
        guard._before_launch(event)
        assert event.cancel_tool is not None
        assert "already fulfilled" in event.cancel_tool

    def test_allows_after_reset_then_blocks_re_invocation(self):
        """User sends same command → allowed after reset. Re-invoke → blocked.

        This is the core scenario: user sends "launch 3 g6.xlarge",
        it completes (fulfilled=True). Then:
        1. User sends same command again → entrypoint calls reset() → allowed
        2. AgentCore re-invokes (no reset) → blocked by fulfilled flag
        """
        guard = LaunchGuardHook()

        # Invocation 1: launch 3
        guard._before_launch(_make_launch_before(target_count=3, instance_type="g6.xlarge"))
        guard._after_launch(_make_launch_after(launched=3, requested=3))
        assert guard._fulfilled is True

        # User sends new message → reset() called at entrypoint
        guard.reset()
        assert guard._fulfilled is False

        # Invocation 2: same command, should be ALLOWED after reset
        allowed = _make_launch_before(target_count=3, instance_type="g6.xlarge")
        guard._before_launch(allowed)
        assert allowed.cancel_tool is None

        # Complete invocation 2
        guard._after_launch(_make_launch_after(launched=3, requested=3))
        assert guard._fulfilled is True

        # AgentCore re-invocation (no reset) → blocked
        blocked = _make_launch_before(target_count=3, instance_type="g6.xlarge")
        guard._before_launch(blocked)
        assert blocked.cancel_tool is not None
        assert "already fulfilled" in blocked.cancel_tool

    def test_user_duplicate_command_launches_twice(self):
        """User sends "launch 3 g6.xlarge" twice → should launch 6 total.

        After the first task completes and reset() is called, the
        fulfilled flag is cleared. The second identical command proceeds
        normally, launching 3 more instances for a total of 6.
        """
        guard = LaunchGuardHook()

        # First "launch 3 g6.xlarge"
        guard._before_launch(_make_launch_before(target_count=3, instance_type="g6.xlarge"))
        guard._after_launch(_make_launch_after(launched=3, requested=3))
        assert guard._fulfilled is True
        guard.reset()

        # Second "launch 3 g6.xlarge" — same fingerprint, should be allowed
        allowed = _make_launch_before(target_count=3, instance_type="g6.xlarge")
        guard._before_launch(allowed)
        assert allowed.cancel_tool is None

        # Complete second invocation
        guard._after_launch(_make_launch_after(launched=3, requested=3))
        assert guard._fulfilled is True
        assert guard._total_launched == 3  # per-invocation count

    def test_re_invocation_blocked_even_with_different_target(self):
        """Re-invocation with any target_count is blocked when fulfilled."""
        guard = LaunchGuardHook()

        guard._before_launch(_make_launch_before(target_count=4))
        guard._after_launch(_make_launch_after(launched=4, requested=4))

        # Re-invocation (no reset) with different target — still blocked
        event = _make_launch_before(target_count=10)
        guard._before_launch(event)
        assert event.cancel_tool is not None
        assert "already fulfilled" in event.cancel_tool

    def test_multiple_re_invocations_all_blocked(self):
        """Multiple re-invocations without reset are all blocked."""
        guard = LaunchGuardHook()

        guard._before_launch(_make_launch_before(target_count=3))
        guard._after_launch(_make_launch_after(launched=3, requested=3))

        for _ in range(5):
            event = _make_launch_before(target_count=3)
            guard._before_launch(event)
            assert event.cancel_tool is not None
            assert "already fulfilled" in event.cancel_tool


class TestLaunchGuardHookEdgeCases:
    """Edge cases and robustness."""

    def test_zero_target_count_not_captured(self):
        """target_count=0 should not be captured as the goal."""
        guard = LaunchGuardHook()
        event = FakeBeforeToolCallEvent(
            tool_use={
                "name": "ec2_launch_instances",
                "input": {"target_count": 0, "instance_type": "g6.xlarge"},
            }
        )
        guard._before_launch(event)
        assert guard._target_count == 0

    def test_negative_target_count_not_captured(self):
        """Negative target_count should not be captured."""
        guard = LaunchGuardHook()
        event = FakeBeforeToolCallEvent(
            tool_use={
                "name": "ec2_launch_instances",
                "input": {"target_count": -1, "instance_type": "g6.xlarge"},
            }
        )
        guard._before_launch(event)
        assert guard._target_count == 0

    def test_missing_result_does_not_crash(self):
        """AfterToolCallEvent with None result should not crash."""
        guard = LaunchGuardHook()
        event = FakeAfterToolCallEvent(
            tool_use={"name": "ec2_launch_instances"},
            result=None,
        )
        guard._after_launch(event)
        assert guard._launch_call_count == 1
        assert guard._total_launched == 0

    def test_failed_result_does_not_count_launched(self):
        """A failed tool result should increment call count but not launched."""
        guard = LaunchGuardHook()
        event = FakeAfterToolCallEvent(
            tool_use={"name": "ec2_launch_instances"},
            result={
                "status": "error",
                "content": [{"text": "something went wrong"}],
            },
        )
        guard._after_launch(event)
        assert guard._launch_call_count == 1
        assert guard._total_launched == 0
        assert guard._fulfilled is False

    def test_fulfilled_not_armed_on_partial(self):
        """Fulfilled flag should NOT be armed when launch is partial."""
        guard = LaunchGuardHook()
        guard._before_launch(_make_launch_before(target_count=5))
        guard._after_launch(_make_launch_after(launched=2, requested=5))
        assert guard._fulfilled is False
        assert guard._total_launched == 2

    def test_parses_json_content_format(self):
        """Should parse launched count from json content blocks."""
        guard = LaunchGuardHook()
        guard._before_launch(_make_launch_before(target_count=3))
        guard._after_launch(_make_launch_after_json(launched=3, requested=3))
        assert guard._total_launched == 3
        assert guard._fulfilled is True

    def test_parses_text_content_format(self):
        """Should parse launched count from text content blocks (Strands default)."""
        guard = LaunchGuardHook()
        guard._before_launch(_make_launch_before(target_count=3))
        guard._after_launch(_make_launch_after(launched=3, requested=3))
        assert guard._total_launched == 3
        assert guard._fulfilled is True

    def test_captures_target_from_text_result(self):
        """Should capture target_count from text result if missed in before."""
        guard = LaunchGuardHook()
        # Skip before, go straight to after with text format
        guard._after_launch(_make_launch_after(launched=3, requested=5))
        assert guard._target_count == 5
