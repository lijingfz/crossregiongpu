"""Launch guard hook: prevents runaway Agent tool-call loops.

Strands Agent has no built-in max_turns limit. When the LLM fails to
recognise that a scheduling task is complete, it can call
ec2_launch_instances indefinitely, over-provisioning GPU instances.

This hook tracks cumulative launched instances via AfterToolCallEvent
and blocks further launch calls via BeforeToolCallEvent once the
target is met or a hard call-count ceiling is reached.

Additionally, it maintains a **self-arming fulfilled flag** that
activates immediately when the target is met inside ``_after_launch``.
This flag blocks any subsequent launch calls — including those from
AgentCore Runtime re-invocations that bypass the entrypoint — without
requiring an external ``reset()`` call.

Only an explicit ``reset()`` (called at the ``invoke()`` entrypoint
when a genuine new user message arrives) clears the flag, allowing
the next user request to proceed normally.

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
    - ``_fulfilled`` is True (target met, auto-armed by _after_launch), OR
    - ``_total_launched >= _target_count`` (goal already met), OR
    - ``_launch_call_count >= max_launch_calls`` (hard ceiling).

    Reset ``reset()`` before each new user prompt / invocation.
    """

    max_launch_calls: int = DEFAULT_MAX_LAUNCH_CALLS

    # --- internal state (reset per invocation) ---
    _launch_call_count: int = field(default=0, init=False, repr=False)
    _total_launched: int = field(default=0, init=False, repr=False)
    _target_count: int = field(default=0, init=False, repr=False)

    # Self-arming flag: set to True by _after_launch when target is met.
    # Blocks ALL subsequent launch calls until reset() is called.
    # This is the primary defence against AgentCore re-invocations that
    # bypass the entrypoint and therefore never call reset().
    _fulfilled: bool = field(default=False, init=False, repr=False)

    def reset(self) -> None:
        """Reset per-invocation counters for a new user message.

        Called by the entrypoint before each new user message. This is the
        ONLY way to clear the ``_fulfilled`` flag, which is what allows a
        genuine new user request (e.g. a second "launch 3") to proceed
        while blocking re-invocations that skip the entrypoint.
        """
        self._launch_call_count = 0
        self._total_launched = 0
        self._target_count = 0
        self._fulfilled = False

    def reset_session(self) -> None:
        """Full reset — alias for reset() since there is no session-level state."""
        self.reset()

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

        print(
            f"=== GUARD _before_launch: fulfilled={self._fulfilled}, "
            f"launched={self._total_launched}, target={self._target_count}, "
            f"calls={self._launch_call_count} ==="
        )

        # Guard 0 (PRIMARY): self-arming fulfilled flag.
        # This fires when _after_launch detected target-met and set
        # _fulfilled=True.  It catches re-invocations that bypass the
        # entrypoint (and therefore never call reset()).
        if self._fulfilled:
            msg = (
                f"LAUNCH BLOCKED (task already fulfilled): "
                f"{self._total_launched} instances were already launched "
                f"for a target of {self._target_count}. "
                "This task is complete. Do NOT launch again. "
                "Inform the user that the instances are already running."
            )
            event.cancel_tool = msg
            print(f"=== GUARD0 BLOCKED: fulfilled=True, launched={self._total_launched}, target={self._target_count} ===")
            logger.warning(
                "LaunchGuard blocked (fulfilled flag): "
                "launched=%d, target=%d",
                self._total_launched, self._target_count,
            )
            return

        # Guard 1: goal already satisfied (within this invocation)
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

        # Extract launched count from the tool result.
        #
        # Strands wraps tool return values in ToolResult format:
        #   {"status": "success", "content": [...], "toolUseId": "..."}
        #
        # The content depends on what the @tool function returns:
        # - If the return dict has both "status" and "content" keys,
        #   Strands treats it as pre-formatted ToolResult (passthrough).
        # - Otherwise, Strands wraps it as: [{"text": str(return_value)}]
        #
        # Our ec2_launch_instances returns StepResult.model_dump() which
        # has "status" but NOT "content", so Strands wraps it as text.
        # We must parse both formats.
        result = event.result
        launched = self._extract_launched(result)
        if launched > 0:
            self._total_launched += launched

        # Also try to capture target from result if we missed it in _before_launch
        requested = self._extract_requested(result)
        if self._target_count == 0 and requested > 0:
            self._target_count = requested

        print(
            f"=== GUARD _after_launch: call#{self._launch_call_count}, "
            f"extracted_launched={launched}, total={self._total_launched}, "
            f"target={self._target_count} ==="
        )

        # Auto-arm the fulfilled flag when target is met.
        # This is the key defence: once armed, _before_launch will block
        # all subsequent launch calls until reset() is explicitly called.
        if (
            self._target_count > 0
            and self._total_launched >= self._target_count
            and not self._fulfilled
        ):
            self._fulfilled = True
            print(
                f"=== GUARD FULFILLED ARMED: "
                f"launched={self._total_launched}, target={self._target_count} ==="
            )
            logger.info(
                "LaunchGuard: target met, fulfilled flag ARMED "
                "(launched=%d, target=%d)",
                self._total_launched, self._target_count,
            )

        logger.info(
            "LaunchGuard: call #%d, total=%d, target=%d, fulfilled=%s",
            self._launch_call_count,
            self._total_launched,
            self._target_count,
            self._fulfilled,
        )

    # ------------------------------------------------------------------
    # Result parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_step_result_dict(data: dict) -> tuple[int, int]:
        """Extract (launched, requested) from a StepResult-like dict."""
        launched = data.get("launched", 0)
        requested = data.get("requested", 0)
        if not isinstance(launched, int):
            launched = 0
        if not isinstance(requested, int):
            requested = 0
        return launched, requested

    @staticmethod
    def _try_parse_text_as_dict(text: str) -> dict | None:
        """Try to parse a text string as a Python dict or JSON."""
        import ast
        import json

        # Try JSON first
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass

        # Try Python literal (str(dict) produces Python repr, not JSON)
        try:
            obj = ast.literal_eval(text)
            if isinstance(obj, dict):
                return obj
        except (ValueError, SyntaxError):
            pass

        return None

    def _extract_from_content(self, result: dict) -> tuple[int, int]:
        """Extract (launched, requested) from ToolResult content blocks."""
        for content_block in result.get("content", []):
            # Format 1: json content block (pre-formatted ToolResult)
            json_data = content_block.get("json")
            if isinstance(json_data, dict):
                launched, requested = self._parse_step_result_dict(json_data)
                if launched > 0 or requested > 0:
                    return launched, requested

            # Format 2: text content block (Strands-wrapped str(dict))
            text_data = content_block.get("text")
            if isinstance(text_data, str) and "launched" in text_data:
                parsed = self._try_parse_text_as_dict(text_data)
                if parsed is not None:
                    launched, requested = self._parse_step_result_dict(parsed)
                    if launched > 0 or requested > 0:
                        return launched, requested

        return 0, 0

    def _extract_launched(self, result: object) -> int:
        """Extract the launched count from a ToolResult."""
        if not isinstance(result, dict):
            return 0
        if result.get("status") != "success":
            return 0
        launched, _ = self._extract_from_content(result)
        return launched

    def _extract_requested(self, result: object) -> int:
        """Extract the requested count from a ToolResult."""
        if not isinstance(result, dict):
            return 0
        if result.get("status") != "success":
            return 0
        _, requested = self._extract_from_content(result)
        return requested
