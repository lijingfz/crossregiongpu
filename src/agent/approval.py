"""Human-in-the-loop approval hook for high-risk operations.

Uses Strands BeforeToolCallEvent to intercept tool calls that exceed
configurable risk thresholds (batch size, cross-continent launches, etc.)
and request human confirmation before proceeding.

Requirements: 9.1, 9.2, 9.3, 9.4
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Set

from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry

logger = logging.getLogger(__name__)


@dataclass
class ApprovalConfig:
    """Thresholds that trigger human approval."""

    # Max instances in a single request before requiring approval
    batch_threshold: int = 20

    # Regions outside this set trigger geographic approval
    allowed_geo_regions: Set[str] = field(default_factory=lambda: set())

    # Tool names that always require approval
    always_approve_tools: Set[str] = field(default_factory=lambda: set())


class ApprovalHook(HookProvider):
    """HookProvider that intercepts high-risk tool calls for human approval.

    When a tool call exceeds the configured thresholds, the hook sets
    ``cancel_tool`` on the BeforeToolCallEvent with a descriptive message,
    which Strands surfaces as an Interrupt for the caller to handle.

    Usage::

        hook = ApprovalHook(config=ApprovalConfig(batch_threshold=10))
        agent = Agent(..., hooks=[hook])
    """

    def __init__(self, config: Optional[ApprovalConfig] = None) -> None:
        self.config = config or ApprovalConfig()

    def register_hooks(self, registry: HookRegistry) -> None:
        """Register the before-tool-call hook with the Strands registry."""
        registry.add_callback(BeforeToolCallEvent, self._check_approval)

    def _check_approval(self, event: BeforeToolCallEvent) -> None:
        """Evaluate whether the pending tool call requires human approval."""
        tool_use = event.tool_use
        tool_name = tool_use.get("name", "")
        tool_input = tool_use.get("input", {})

        # Check always-approve list
        if tool_name in self.config.always_approve_tools:
            event.cancel_tool = (
                f"Tool '{tool_name}' requires human approval. "
                "Please confirm to proceed."
            )
            logger.info("Approval required: tool %s is in always_approve list", tool_name)
            return

        # Check batch size threshold (Requirement 9.2)
        target_count = tool_input.get("target_count", 0)
        if isinstance(target_count, int) and target_count > self.config.batch_threshold:
            event.cancel_tool = (
                f"High-risk: launching {target_count} instances exceeds "
                f"threshold of {self.config.batch_threshold}. "
                "Human approval required."
            )
            logger.info(
                "Approval required: target_count=%d > threshold=%d",
                target_count, self.config.batch_threshold,
            )
            return

        # Check geographic boundary (Requirement 9.3)
        region = tool_input.get("region", "")
        if (
            self.config.allowed_geo_regions
            and region
            and region not in self.config.allowed_geo_regions
        ):
            event.cancel_tool = (
                f"High-risk: region '{region}' is outside the allowed "
                f"geographic boundary {self.config.allowed_geo_regions}. "
                "Human approval required."
            )
            logger.info(
                "Approval required: region %s not in allowed set", region,
            )
            return
