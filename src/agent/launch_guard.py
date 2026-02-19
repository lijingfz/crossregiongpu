"""Launch guard hook: prevents runaway Agent tool-call loops.

Strands Agent has no built-in max_turns limit. When the LLM fails to
recognise that a scheduling task is complete, it can call
ec2_launch_instances indefinitely, over-provisioning GPU instances.

This hook tracks cumulative launched instances via AfterToolCallEvent
and blocks further launch calls via BeforeToolCallEvent once the
target is met or a hard call-count ceiling is reached.

Requirements: 3.7 (idempotency / safety)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from strands.hooks import (
    AfterToolCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

logger = logging.getLogger(__name__)

# Hard ceiling: maximum number of ec2_launch_instances calls per
# single Agent invocation, regardless of target_count.
DEFAULT_MAX_LAUNCH_CALLS = 8


@dataclass
class LaunchGuardHook(HookProvider):
    """Prevents the Agent from calling ec2_launch_instances in a loop.

    Tracks two things per Agent invocation:
    1. ``_launch_call_count`` — number of times launch was called.
    2. ``_total_launched`` — cumulative instances launched (from StepResult).

    Blocks the next launch call when:
    - ``_total_launched >= _target_count`` (goal already met), OR
    - ``_launch_call_count >= max_launch_calls`` (hard ceiling).

    Reset ``reset()`` before each new user prompt / invocation.
    """

    max_launch_calls: int = DEFAULT_MAX_LAUNCH_CALLS

    # --- internal state (reset per invocation) ---
    _launch_call_count: int = field(default=0, init=False, repr=False)
    _total_launched: int = field(default=0, init=False, repr=False)
    _target_count: int = field(default=0, init=False, repr=False)

    def reset(self) -> None:
        """Reset counters for a new Agent invocation."""
        self._launch_call_count = 0
        self._total_launched = 0
        self._target_count = 0

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before_launch)
        registry.add_callback(AfterToolCallEvent, self._after_launch)

    # ------------------------------------------------------------------
    # Before: block if goal met or ceiling reached
    # ------------------------------------------------------------------

    def _before_launch(self, event: BeforeToolCallEvent) -> None:
        tool_name = event.tool_use.get("name", "")
        if tool_name != "ec2_launch_instances":
            return

        tool_input = event.tool_use.get("input", {})

        # Capture the first target_count we see as the goal
        if self._target_count == 0:
            tc = tool_input.get("target_count", 0)
            if isinstance(tc, int) and tc > 0:
                self._target_count = tc

        # Guard 1: goal already satisfied
        if self._target_count > 0 and self._total_launched >= self._target_count:
            event.cancel_tool = (
                f"LAUNCH BLOCKED: {self._total_launched} instances already "
                f"launched, meeting the target of {self._target_count}. "
                "Do NOT call ec2_launch_instances again. "
                "Proceed to dynamodb_put_instances and finalize."
            )
            logger.warning(
                "LaunchGuard blocked launch: launched=%d >= target=%d",
                self._total_launched, self._target_count,
            )
            return

        # Guard 2: hard call-count ceiling
        if self._launch_call_count >= self.max_launch_calls:
            event.cancel_tool = (
                f"LAUNCH BLOCKED: ec2_launch_instances has been called "
                f"{self._launch_call_count} times (max {self.max_launch_calls}). "
                "Stop launching and proceed to finalize with current results."
            )
            logger.warning(
                "LaunchGuard blocked launch: call_count=%d >= max=%d",
                self._launch_call_count, self.max_launch_calls,
            )
            return

    # ------------------------------------------------------------------
    # After: track cumulative launched count
    # ------------------------------------------------------------------

    def _after_launch(self, event: AfterToolCallEvent) -> None:
        tool_use = event.tool_use
        tool_name = tool_use.get("name", "")
        if tool_name != "ec2_launch_instances":
            return

        self._launch_call_count += 1

        # Extract launched count from the tool result
        result = event.result
        if result and result.get("status") == "success":
            for content_block in result.get("content", []):
                json_data = content_block.get("json")
                if isinstance(json_data, dict):
                    launched = json_data.get("launched", 0)
                    if isinstance(launched, int):
                        self._total_launched += launched
                        # Also capture target from result if we missed it
                        if self._target_count == 0:
                            requested = json_data.get("requested", 0)
                            if isinstance(requested, int) and requested > 0:
                                self._target_count = requested

        logger.info(
            "LaunchGuard: call #%d, launched_this_call=%s, total=%d, target=%d",
            self._launch_call_count,
            "see_result",
            self._total_launched,
            self._target_count,
        )
