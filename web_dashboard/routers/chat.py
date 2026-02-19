"""Chat routes for the Web Dashboard.

Forwards user messages to the remote AgentCore Runtime agent via
boto3 ``invoke_agent_runtime``, instead of creating local Agent
instances.

Requirements: 2.1, 2.5, 3.2, 3.3, 4.1, 4.3, 4.4, 6.2, 6.3, 6.4
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends

from web_dashboard.agentcore_client import invoke_agent, invoke_approval
from web_dashboard.dependencies import get_current_user
from web_dashboard.models import (
    ApiResponse,
    ApprovalRequest,
    ChatData,
    ChatRequest,
    InterruptData,
)
from web_dashboard.session_registry import list_recent_sessions, record_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _make_agent_token(user: dict) -> str:
    """Generate a short-lived JWT for the AgentCore agent-side auth.

    The AgentCore entrypoint validates this token via
    ``src.agent.auth.validate_token``.
    """
    secret = os.environ.get("AUTH_SECRET_KEY", "")
    if not secret:
        try:
            import yaml

            with open("config/environments/dev.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            secret = cfg.get("auth_secret_key", "")
        except Exception:
            pass
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user.get("user_id", ""),
        "sub": user.get("user_id", ""),
        "username": user.get("username", ""),
        "roles": user.get("roles", []),
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/send")
async def send_message(
    request: ChatRequest,
    user: dict = Depends(get_current_user),
) -> ApiResponse:
    """Forward a user message to the AgentCore Runtime agent.

    Rejects whitespace-only messages (Req 2.5).
    Handles completed / approval_required / error states (Req 2.1).

    Requirements: 2.1, 2.5, 6.2
    """
    if not request.message or not request.message.strip():
        return ApiResponse(
            status="error",
            message="Message must not be empty or whitespace-only",
        )

    token = _make_agent_token(user)

    try:
        resp = invoke_agent(
            session_id=request.session_id,
            prompt=request.message,
            token=token,
        )
    except Exception as exc:
        logger.exception("AgentCore invoke failed, session=%s", request.session_id)
        return ApiResponse(
            status="success",
            data=ChatData(agent_status="error", error_message=str(exc)).model_dump(),
        )

    # Record session for recent conversations sidebar
    try:
        record_session(
            session_id=request.session_id,
            user_id=user.get("user_id", ""),
            preview=request.message,
        )
    except Exception:
        logger.debug("Session registry update failed", exc_info=True)

    return _build_chat_response(resp)


@router.post("/approve")
async def approve(
    request: ApprovalRequest,
    user: dict = Depends(get_current_user),
) -> ApiResponse:
    """Submit an approval decision to the AgentCore Runtime agent.

    Requirements: 3.2, 3.3, 6.3
    """
    token = _make_agent_token(user)

    try:
        resp = invoke_approval(
            session_id=request.session_id,
            approval_responses=[{
                "interrupt_id": request.interrupt_id,
                "decision": request.decision,
            }],
            token=token,
        )
    except Exception as exc:
        logger.exception("AgentCore approval failed, session=%s", request.session_id)
        return ApiResponse(
            status="success",
            data=ChatData(agent_status="error", error_message=str(exc)).model_dump(),
        )

    return _build_chat_response(resp)


@router.get("/history")
async def get_history(
    session_id: str,
    user: dict = Depends(get_current_user),
) -> ApiResponse:
    """Retrieve conversation history via Memory_Module.

    Returns an empty list on failure (Req 4.3).

    Requirements: 4.1, 4.3, 6.4
    """
    user_id = user.get("user_id", "")
    try:
        from src.agent.memory import retrieve_conversation

        events = retrieve_conversation(session_id=session_id, user_id=user_id)
    except Exception:
        logger.exception("Memory retrieval failed (session=%s)", session_id)
        events = []

    messages: list[dict] = []
    for event in events:
        if isinstance(event, dict):
            for msg in event.get("messages", []):
                content, role_raw = msg if isinstance(msg, (list, tuple)) else ("", "")
                role = "user" if str(role_raw).upper() == "USER" else "assistant"
                messages.append({"role": role, "content": str(content), "timestamp": ""})

    return ApiResponse(
        status="success",
        data={"messages": messages},
        message="History retrieved",
    )


@router.get("/sessions")
async def recent_sessions(
    user: dict = Depends(get_current_user),
) -> ApiResponse:
    """Return the 10 most recent conversation sessions for the sidebar."""
    user_id = user.get("user_id", "")
    sessions = list_recent_sessions(user_id=user_id, limit=10)
    return ApiResponse(
        status="success",
        data={"sessions": sessions},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_chat_response(agent_resp: dict) -> ApiResponse:
    """Convert an AgentCore Runtime response dict into an ApiResponse.

    The AgentCore entrypoint returns AgentResponse format:
    {status, session_id, result, interrupts, message, user_id}
    """
    status = agent_resp.get("status", "completed")

    if status == "unauthorized":
        return ApiResponse(
            status="error",
            message=agent_resp.get("message", "Authentication failed"),
        )

    if status == "error":
        return ApiResponse(
            status="success",
            data=ChatData(
                agent_status="error",
                error_message=agent_resp.get("message", "Unknown error"),
            ).model_dump(),
        )

    if status == "approval_required":
        raw_interrupts = agent_resp.get("interrupts", [])
        interrupts = [
            InterruptData(
                interrupt_id=intr.get("interrupt_id", ""),
                reason=intr.get("reason", ""),
            )
            for intr in raw_interrupts
        ]
        return ApiResponse(
            status="success",
            data=ChatData(
                agent_status="approval_required",
                interrupts=interrupts,
            ).model_dump(),
        )

    # completed
    return ApiResponse(
        status="success",
        data=ChatData(
            agent_status="completed",
            result=agent_resp.get("result", ""),
        ).model_dump(),
    )
