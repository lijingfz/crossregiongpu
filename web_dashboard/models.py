"""Web Dashboard request/response Pydantic models.

Defines the API contract for the Web Dashboard endpoints.
All API responses use the unified ``ApiResponse`` format with
``status``, ``data``, and ``message`` fields.

Requirements: 6.6
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    """Login request payload."""

    username: str
    password: str


class ChatRequest(BaseModel):
    """Chat message request payload."""

    session_id: str
    message: str


class ApprovalRequest(BaseModel):
    """Approval decision request payload."""

    session_id: str
    interrupt_id: str
    decision: Literal["approved", "rejected"]


# ---------------------------------------------------------------------------
# Response data models
# ---------------------------------------------------------------------------

class LoginData(BaseModel):
    """Data returned on successful login."""

    token: str
    user_id: str
    username: str


class InterruptData(BaseModel):
    """Approval interrupt information."""

    interrupt_id: str
    reason: str


class ChatData(BaseModel):
    """Chat response data."""

    agent_status: Literal["completed", "approval_required", "error"]
    result: Optional[str] = None
    interrupts: Optional[list[InterruptData]] = None
    error_message: Optional[str] = None


class HistoryMessage(BaseModel):
    """Single conversation history message."""

    role: Literal["user", "assistant"]
    content: str
    timestamp: str


# ---------------------------------------------------------------------------
# Unified API response
# ---------------------------------------------------------------------------

class ApiResponse(BaseModel):
    """Unified API response format.

    All endpoints return this structure to ensure a consistent
    contract between backend and frontend.

    Requirements: 6.6
    """

    status: Literal["success", "error"]
    data: Optional[dict] = None
    message: str = ""
