"""AgentCore Runtime entrypoint for the GPU Cross-Region Scheduler Agent.

Implements the BedrockAgentCoreApp pattern with:
- Session-level Agent caching (_session_agents dict)
- Interrupt state save/restore for cross-request approval flows
- Authentication check → request routing (prompt vs approval_responses)
  → Agent invocation → interrupt handling → response building

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 3.1, 3.2, 3.3, 3.4, 6.1, 7.1, 7.2, 7.3, 7.4
"""

from __future__ import annotations

import logging
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from src.agent.auth import AuthenticationError, validate_token
from src.agent.main import build_agent
from src.agent.memory import store_conversation, retrieve_conversation
from src.models.responses import AgentResponse, InterruptInfo

logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

# Session-level Agent cache: session_id -> Agent instance  (Req 2.3, 2.4)
_session_agents: dict[str, Any] = {}

# Session-level interrupt state cache: session_id -> serialized _InterruptState dict
# Saved when an interrupt is returned, restored before processing approval_responses.
# This is necessary because AgentCore may not preserve in-process state between requests.
_session_interrupt_cache: dict[str, dict] = {}


def _get_or_create_agent(session_id: str) -> Any:
    """Return the cached Agent for *session_id*, creating one if needed."""
    if session_id not in _session_agents:
        _session_agents[session_id] = build_agent()
    return _session_agents[session_id]


def _reset_launch_guard(agent: Any) -> None:
    """Reset the LaunchGuardHook counters before a new invocation."""
    guard = getattr(agent, "_launch_guard", None)
    if guard is not None:
        guard.reset()
        logger.debug("LaunchGuard reset for new invocation")


def _extract_text(result: Any) -> str:
    """Extract text content from an AgentResult."""
    content = getattr(result, "message", {}).get("content", [])
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and "text" in block:
            parts.append(block["text"])
    return "\n".join(parts) if parts else str(result)



@app.entrypoint
async def invoke(payload: dict, context) -> dict:
    """AgentCore entrypoint function.

    Accepts either a ``prompt`` (new user message) or
    ``approval_responses`` (human approval decisions) in the payload.

    Requirements: 2.1, 2.2, 3.1, 3.2, 6.1, 7.1, 7.2, 7.3, 7.4
    """
    session_id: str = getattr(context, "session_id", None) or ""

    # --- Authentication (Req 6.1) ---
    token = payload.get("token", "")
    try:
        user_info = validate_token(token)
    except AuthenticationError as exc:
        return AgentResponse(
            status="unauthorized",
            session_id=session_id,
            message=str(exc),
        ).model_dump()

    user_id = user_info.get("user_id", "")

    # --- Get or create Agent for this session (Req 2.3, 2.4) ---
    try:
        agent = _get_or_create_agent(session_id)
    except Exception as exc:
        logger.exception("Failed to create agent for session %s", session_id)
        return AgentResponse(
            status="error",
            session_id=session_id,
            message=f"Agent creation failed: {exc}",
            user_id=user_id,
        ).model_dump()

    # --- Route: approval_responses (Req 3.2, 3.3) ---
    approval_responses = payload.get("approval_responses")
    if approval_responses is not None:
        # Store inbound approval response to memory (Req 5.1, 5.2)
        _store_memory(
            session_id=session_id,
            user_id=user_id,
            user_content=str(approval_responses),
            message_type="approval_response",
        )

        try:
            # Restore interrupt state if it was lost between requests
            _restore_interrupt_state(agent, session_id)

            interrupt_responses = [
                {
                    "interruptResponse": {
                        "interruptId": ar["interrupt_id"],
                        "response": "y" if ar.get("decision", "approved") == "approved" else "n",
                    }
                }
                for ar in approval_responses
            ]

            logger.info(
                "Resuming agent with interrupt responses, session=%s, "
                "activated=%s, interrupts=%s",
                session_id,
                agent._interrupt_state.activated,
                list(agent._interrupt_state.interrupts.keys()),
            )

            result = agent(interrupt_responses)
        except Exception as exc:
            logger.exception("Agent error during approval resume, session=%s", session_id)
            return AgentResponse(
                status="error",
                session_id=session_id,
                message=str(exc),
                user_id=user_id,
            ).model_dump()

        resp = _build_response(result, session_id, user_id)
        _store_memory(
            session_id=session_id,
            user_id=user_id,
            agent_content=resp.get("result", resp.get("message", "")),
            message_type="agent_response",
        )
        return resp

    # --- Route: prompt (Req 2.2) ---
    prompt = payload.get("prompt")
    if not prompt:
        return AgentResponse(
            status="error",
            session_id=session_id,
            message="Payload must contain 'prompt' or 'approval_responses'",
            user_id=user_id,
        ).model_dump()

    # Reset launch guard counters for the new invocation (Req 3.7)
    _reset_launch_guard(agent)

    try:
        result = agent(prompt)
    except Exception as exc:
        logger.exception("Agent error, session=%s", session_id)
        return AgentResponse(
            status="error",
            session_id=session_id,
            message=str(exc),
            user_id=user_id,
        ).model_dump()

    resp = _build_response(result, session_id, user_id)

    # Store user message + agent response to memory (Req 5.1, 5.2)
    _store_memory(
        session_id=session_id,
        user_id=user_id,
        user_content=prompt,
        agent_content=resp.get("result", resp.get("message", "")),
        message_type="user_message" if resp.get("status") != "approval_required" else "approval_request",
    )

    return resp


def _store_memory(
    *,
    session_id: str,
    user_id: str,
    user_content: str | None = None,
    agent_content: str | None = None,
    message_type: str = "user_message",
) -> None:
    """Persist conversation turn to AgentCore Memory (non-blocking).

    Failures are logged but never propagated (Req 5.3).
    Requirements: 5.1, 5.2, 5.3
    """
    messages: list[tuple[str, str]] = []
    if user_content:
        messages.append((user_content, "USER"))
    if agent_content:
        messages.append((agent_content, "ASSISTANT"))
    if not messages:
        return
    store_conversation(session_id=session_id, user_id=user_id, messages=messages)


def _build_response(result: Any, session_id: str, user_id: str) -> dict:
    """Convert an AgentResult into a standardised response dict.

    When an interrupt is detected, saves the agent's interrupt state to
    ``_session_interrupt_cache`` so it can be restored on the next request.

    Requirements: 3.1, 3.4, 7.1, 7.2, 7.3, 7.4
    """
    stop_reason = getattr(result, "stop_reason", None)

    # Interrupt → approval_required (Req 3.1, 7.2)
    if stop_reason == "interrupt" and result.interrupts:
        # Save interrupt state for cross-request restore
        _save_interrupt_state(session_id)

        interrupts = [
            InterruptInfo(
                interrupt_id=intr.id,
                reason=_interrupt_reason_text(intr),
            )
            for intr in result.interrupts
        ]
        return AgentResponse(
            status="approval_required",
            session_id=session_id,
            interrupts=interrupts,
            user_id=user_id,
        ).model_dump()

    # Normal completion (Req 3.4, 7.1)
    return AgentResponse(
        status="completed",
        session_id=session_id,
        result=_extract_text(result),
        user_id=user_id,
    ).model_dump()


def _save_interrupt_state(session_id: str) -> None:
    """Snapshot the cached agent's _interrupt_state for later restore.

    Called when returning an ``approval_required`` response so the state
    survives even if the runtime recycles the process between requests.
    """
    agent = _session_agents.get(session_id)
    if agent is None:
        return
    try:
        state_dict = agent._interrupt_state.to_dict()
        _session_interrupt_cache[session_id] = state_dict
        logger.info(
            "Saved interrupt state for session=%s, activated=%s, ids=%s",
            session_id,
            state_dict.get("activated"),
            list(state_dict.get("interrupts", {}).keys()),
        )
    except Exception:
        logger.exception("Failed to save interrupt state for session=%s", session_id)


def _restore_interrupt_state(agent: Any, session_id: str) -> None:
    """Restore a previously saved interrupt state onto *agent*.

    If the agent already has ``activated=True`` (state survived in-process),
    this is a no-op.  Otherwise, deserializes the cached snapshot so that
    ``resume()`` can match interrupt IDs and deliver responses.
    """
    if agent._interrupt_state.activated:
        logger.info("Interrupt state already active for session=%s, skip restore", session_id)
        return

    saved = _session_interrupt_cache.pop(session_id, None)
    if saved is None:
        logger.warning("No saved interrupt state for session=%s", session_id)
        return

    try:
        from strands.interrupt import _InterruptState
        agent._interrupt_state = _InterruptState.from_dict(saved)
        logger.info(
            "Restored interrupt state for session=%s, activated=%s, ids=%s",
            session_id,
            agent._interrupt_state.activated,
            list(agent._interrupt_state.interrupts.keys()),
        )
    except Exception:
        logger.exception("Failed to restore interrupt state for session=%s", session_id)


def _interrupt_reason_text(interrupt: Any) -> str:
    """Extract a human-readable reason string from an Interrupt."""
    reason = getattr(interrupt, "reason", None)
    if isinstance(reason, dict):
        return reason.get("prompt", str(reason))
    return str(reason) if reason else ""


if __name__ == "__main__":
    app.run()
