"""Unit tests for LaunchGuardHook.

Validates that the hook correctly:
- Blocks ec2_launch_instances when target is met
- Blocks ec2_launch_instances when call ceiling is reached
- Tracks cumulative launched count from AfterToolCallEvent
- Resets state between invocations
- Ignores non-launch tool calls

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


def _make_launch_before(target_count: int = 3) -> FakeBeforeToolCallEvent:
    return FakeBeforeToolCallEvent(
        tool_use={
            "name": "ec2_launch_instances",
            "input": {"target_count": target_count, "region": "ap-southeast-1"},
        }
    )


def _make_launch_after(launched: int = 3, requested: int = 3) -> FakeAfterToolCallEvent:
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
        # After: 3 launched
        guard._after_launch(_make_launch_after(launched=3, requested=3))

        # Second call should be blocked
        event = _make_launch_before(target_count=3)
        guard._before_launch(event)
        assert event.cancel_tool is not None
        assert "LAUNCH BLOCKED" in event.cancel_tool
        assert "3" in event.cancel_tool

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

        # Call 3: should be blocked (5 >= 5)
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


class TestLaunchGuardHookReset:
    """Tests for the reset() method."""

    def test_reset_clears_all_state(self):
        """reset() should zero out all counters."""
        guard = LaunchGuardHook()

        guard._before_launch(_make_launch_before(target_count=3))
        guard._after_launch(_make_launch_after(launched=3, requested=3))
        assert guard._launch_call_count == 1
        assert guard._total_launched == 3
        assert guard._target_count == 3

        guard.reset()
        assert guard._launch_call_count == 0
        assert guard._total_launched == 0
        assert guard._target_count == 0

    def test_allows_launches_after_reset(self):
        """After reset, launches should be allowed again."""
        guard = LaunchGuardHook()

        # Fill target
        guard._before_launch(_make_launch_before(target_count=3))
        guard._after_launch(_make_launch_after(launched=3, requested=3))

        # Blocked
        blocked = _make_launch_before(target_count=3)
        guard._before_launch(blocked)
        assert blocked.cancel_tool is not None

        # Reset
        guard.reset()

        # Now allowed again
        allowed = _make_launch_before(target_count=5)
        guard._before_launch(allowed)
        assert allowed.cancel_tool is None
        assert guard._target_count == 5


class TestLaunchGuardHookEdgeCases:
    """Edge cases and robustness."""

    def test_zero_target_count_not_captured(self):
        """target_count=0 should not be captured as the goal."""
        guard = LaunchGuardHook()
        event = FakeBeforeToolCallEvent(
            tool_use={
                "name": "ec2_launch_instances",
                "input": {"target_count": 0},
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
                "input": {"target_count": -1},
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
