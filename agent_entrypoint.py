"""AgentCore Runtime entrypoint for the GPU Cross-Region Scheduler Agent.

Implements the BedrockAgentCoreApp pattern with:
- Session-level Agent caching (_session_agents dict)
- Interrupt state save/restore for cross-request approval flows
- Authentication check → request routing (prompt vs approval_responses)
  → Agent invocation → interrupt handling → response building

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 3.1, 3.2, 3.3, 3.4, 6.1, 7.1, 7.2, 7.3, 7.4
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.runtime.context import BedrockAgentCoreContext

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

# ---------------------------------------------------------------------------
# Re-invocation response cache (Req 3.7 — idempotency)
#
# AgentCore Runtime infrastructure may re-invoke invoke() with the same
# session_id + prompt immediately after a successful completion.  Without
# caching, the second call runs the agent again, producing a confusing
# "already launched" message instead of the real result.
#
# Cache key:  (session_id, prompt_hash)
# Cache value: (response_dict, timestamp, request_id)
#
# On entry we check: if same (session_id, prompt_hash) was completed
# within REINVOKE_WINDOW_SECONDS AND the current request_id differs
# from the one that produced the cached response, it is a re-invocation
# → return cached response immediately.
#
# A genuinely new user request with the same prompt will arrive with a
# time gap > REINVOKE_WINDOW_SECONDS (user cannot type + submit in <2s).
# ---------------------------------------------------------------------------
_response_cache: dict[tuple[str, str], tuple[dict, float, str]] = {}
REINVOKE_WINDOW_SECONDS = 10  # re-invocations arrive in <1s; 10s is sufficient


def _prompt_hash(prompt: str) -> str:
    """Return a short hash of the prompt for cache keying."""
    return hashlib.sha256(prompt.encode()).hexdigest()[:16]


def _check_reinvoke_cache(
    session_id: str, prompt: str, current_request_id: str,
) -> dict | None:
    """Return cached response if this is a re-invocation, else None."""
    key = (session_id, _prompt_hash(prompt))
    cached = _response_cache.get(key)
    if cached is None:
        return None

    resp, ts, orig_request_id = cached
    elapsed = time.time() - ts

    # Same request_id → not a re-invocation (shouldn't happen, but safe)
    if current_request_id == orig_request_id:
        return None

    # Outside the window → genuine new request
    if elapsed > REINVOKE_WINDOW_SECONDS:
        # Evict stale entry
        _response_cache.pop(key, None)
        return None

    print(
        f"=== REINVOKE CACHE HIT === session={session_id}, "
        f"elapsed={elapsed:.3f}s, orig_req={orig_request_id}, "
        f"new_req={current_request_id}"
    )
    return resp


def _store_reinvoke_cache(
    session_id: str, prompt: str, response: dict, request_id: str,
) -> None:
    """Cache a successful response for re-invocation detection."""
    key = (session_id, _prompt_hash(prompt))
    _response_cache[key] = (response, time.time(), request_id)


def _get_or_create_agent(session_id: str) -> Any:
    """Return the cached Agent for *session_id*, creating one if needed."""
    if session_id not in _session_agents:
        _session_agents[session_id] = build_agent()
    return _session_agents[session_id]


def _reset_launch_guard(agent: Any) -> None:
    """Reset the LaunchGuardHook counters before a new invocation."""
    guard = getattr(agent, "_launch_guard", None)
    if guard is not None:
        print(
            f"=== LAUNCH_GUARD PRE-RESET: "
            f"call_count={guard._launch_call_count}, "
            f"total_launched={guard._total_launched}, "
            f"target={guard._target_count}, "
            f"fulfilled={guard._fulfilled} ==="
        )
        guard.reset()
        print("=== LAUNCH_GUARD RESET DONE ===")


def _extract_text(result: Any) -> str:
    """Extract text content from an AgentResult."""
    content = getattr(result, "message", {}).get("content", [])
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and "text" in block:
            parts.append(block["text"])
    return "\n".join(parts) if parts else str(result)


def _sanitize_messages(agent: Any) -> None:
    """Ensure agent.messages has valid toolUse/toolResult pairing.

    Bedrock ConverseStream requires that every user message containing
    toolResult blocks has exactly as many results as the preceding
    assistant message has toolUse blocks.  If a previous invocation
    was interrupted (exception, timeout, re-invocation race), the
    messages list can end up in an inconsistent state.

    This function walks the messages and removes any trailing messages
    that would violate the pairing constraint.  It is intentionally
    conservative: it only trims from the end, never modifies middle
    messages.
    """
    messages = getattr(agent, "messages", None)
    if not messages:
        return

    # Walk backwards and remove trailing messages that are broken
    while messages:
        last = messages[-1]
        role = last.get("role", "")
        content = last.get("content", [])

        # If the last message is an assistant with toolUse but no
        # following toolResult → remove it (incomplete turn)
        if role == "assistant" and any("toolUse" in c for c in content):
            print(f"=== SANITIZE: removing trailing assistant toolUse message ===")
            messages.pop()
            continue

        # If the last message is a user with toolResult, verify it
        # matches the preceding assistant's toolUse count
        if role == "user" and any("toolResult" in c for c in content):
            tool_result_count = sum(1 for c in content if "toolResult" in c)

            # Find the preceding assistant message
            if len(messages) >= 2:
                prev = messages[-2]
                prev_content = prev.get("content", [])
                if prev.get("role") == "assistant":
                    tool_use_count = sum(1 for c in prev_content if "toolUse" in c)
                    if tool_result_count != tool_use_count:
                        print(
                            f"=== SANITIZE: toolResult count ({tool_result_count}) != "
                            f"toolUse count ({tool_use_count}), removing last 2 messages ==="
                        )
                        messages.pop()  # remove mismatched toolResult
                        messages.pop()  # remove the toolUse that has no valid result
                        continue

        # No issues found at the tail
        break



@app.entrypoint
async def invoke(payload: dict, context):
    """AgentCore entrypoint function (streaming).

    Uses ``agent.stream_async()`` + ``yield`` so that AgentCore Runtime
    returns ``text/event-stream`` to the caller.  Intermediate events
    carry tool-call progress; the final event is the complete
    AgentResponse dict.

    All yields are dicts (not JSON strings) — AgentCore Runtime
    serializes them into SSE ``data:`` lines automatically.

    Accepts either a ``prompt`` (new user message) or
    ``approval_responses`` (human approval decisions) in the payload.

    Requirements: 2.1, 2.2, 3.1, 3.2, 6.1, 7.1, 7.2, 7.3, 7.4
    """
    session_id: str = getattr(context, "session_id", None) or ""
    current_request_id: str = BedrockAgentCoreContext.get_request_id() or ""

    print(
        f"=== INVOKE ENTRY === session={session_id}, "
        f"request_id={current_request_id}, "
        f"payload_keys={list(payload.keys())}"
    )

    logger.info(
        "=== invoke() called, session=%s, payload_keys=%s ===",
        session_id, list(payload.keys()),
    )

    # --- Authentication (Req 6.1) ---
    token = payload.get("token", "")
    try:
        user_info = validate_token(token)
    except AuthenticationError as exc:
        yield AgentResponse(
            status="unauthorized",
            session_id=session_id,
            message=str(exc),
        ).model_dump()
        return

    user_id = user_info.get("user_id", "")

    # --- Get or create Agent for this session (Req 2.3, 2.4) ---
    try:
        agent = _get_or_create_agent(session_id)
    except Exception as exc:
        logger.exception("Failed to create agent for session %s", session_id)
        yield AgentResponse(
            status="error",
            session_id=session_id,
            message=f"Agent creation failed: {exc}",
            user_id=user_id,
        ).model_dump()
        return

    # --- Route: approval_responses (Req 3.2, 3.3) ---
    approval_responses = payload.get("approval_responses")
    if approval_responses is not None:
        _store_memory(
            session_id=session_id,
            user_id=user_id,
            user_content=str(approval_responses),
            message_type="approval_response",
        )

        try:
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
            yield AgentResponse(
                status="error",
                session_id=session_id,
                message=str(exc),
                user_id=user_id,
            ).model_dump()
            return

        resp = _build_response(result, session_id, user_id)
        _store_memory(
            session_id=session_id,
            user_id=user_id,
            agent_content=resp.get("result", resp.get("message", "")),
            message_type="agent_response",
        )
        yield resp
        return

    # --- Route: prompt (Req 2.2) ---
    prompt = payload.get("prompt")
    if not prompt:
        yield AgentResponse(
            status="error",
            session_id=session_id,
            message="Payload must contain 'prompt' or 'approval_responses'",
            user_id=user_id,
        ).model_dump()
        return

    # --- Re-invocation detection (Req 3.7) ---
    cached_resp = _check_reinvoke_cache(session_id, prompt, current_request_id)
    if cached_resp is not None:
        print(
            f"=== RETURNING CACHED RESPONSE === session={session_id}, "
            f"request_id={current_request_id}"
        )
        yield cached_resp
        return

    # Reset per-invocation launch guard counters (Req 3.7).
    _reset_launch_guard(agent)

    # Sanitize messages to fix any toolUse/toolResult mismatches
    # left by a previous interrupted invocation (Req 3.7).
    _sanitize_messages(agent)

    print(f"=== CALLING agent.stream_async(prompt), fulfilled={getattr(getattr(agent, '_launch_guard', None), '_fulfilled', 'N/A')} ===")

    # --- Streaming prompt execution ---
    # Use stream_async to yield intermediate tool-call progress events.
    # Each yielded dict becomes an SSE "data:" line in the
    # text/event-stream response that AgentCore Runtime sends to the caller.
    #
    # Strands stream_async event types (from SDK source):
    #   ToolUseStreamEvent: {"type":"tool_use_stream", "current_tool_use":{...}, "delta":...}
    #   TextStreamEvent:    {"data": "text...", "delta": ...}
    #   AgentResultEvent:   {"result": AgentResult}  ← final event
    result = None
    active_tool: str | None = None

    try:
        stream = agent.stream_async(prompt)
        async for event in stream:
            if not isinstance(event, dict):
                continue

            # --- Tool-call progress ---
            tool_use = event.get("current_tool_use")
            if isinstance(tool_use, dict):
                tool_name = tool_use.get("name", "")
                if tool_name and tool_name != active_tool:
                    if active_tool:
                        yield {"type": "tool_end", "tool": active_tool}
                    active_tool = tool_name
                    yield {"type": "tool_start", "tool": tool_name}

            # --- Check for final AgentResult event ---
            # AgentResultEvent has {"result": AgentResult} — no "complete" key
            agent_result = event.get("result")
            if agent_result is not None:
                if active_tool:
                    yield {"type": "tool_end", "tool": active_tool}
                    active_tool = None
                result = agent_result

    except Exception as exc:
        logger.exception("Agent stream error, session=%s", session_id)
        if active_tool:
            yield {"type": "tool_end", "tool": active_tool}
        yield AgentResponse(
            status="error",
            session_id=session_id,
            message=str(exc),
            user_id=user_id,
        ).model_dump()
        return

    # Close any dangling tool card
    if active_tool:
        yield {"type": "tool_end", "tool": active_tool}

    # Build final response
    if result is None:
        resp = AgentResponse(
            status="error",
            session_id=session_id,
            message="Agent stream ended without a result",
            user_id=user_id,
        ).model_dump()
    else:
        resp = _build_response(result, session_id, user_id)

    # Cache the response for re-invocation detection
    _store_reinvoke_cache(session_id, prompt, resp, current_request_id)

    # Store to memory
    _store_memory(
        session_id=session_id,
        user_id=user_id,
        user_content=prompt,
        agent_content=resp.get("result", resp.get("message", "")),
        message_type="user_message" if resp.get("status") != "approval_required" else "approval_request",
    )

    # Yield the final AgentResponse as the last SSE event
    yield resp


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
