"""Response models for the AgentCore Runtime entrypoint.

Defines the standardised response format returned by the AgentCore
entrypoint, the interrupt information model, and the conversation
record model used for Memory persistence.

Requirements: 7.1, 7.2, 7.3, 7.4, 5.2
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class InterruptInfo(BaseModel):
    """Single approval interrupt information.

    Returned when the Agent's ApprovalHook triggers an interrupt,
    containing the interrupt identifier and human-readable reason.
    """

    interrupt_id: str
    reason: str


class AgentResponse(BaseModel):
    """Standardised response format for the AgentCore entrypoint.

    Every response from the entrypoint uses this model so that clients
    can reliably distinguish between normal results, approval requests,
    authentication failures, and errors.

    - status="completed"         → normal result  (Req 7.1)
    - status="approval_required" → interrupts list populated (Req 7.2)
    - status="error"             → message populated (Req 7.3)
    - status="unauthorized"      → authentication failure
    - session_id always present  (Req 7.4)
    """

    status: Literal["completed", "approval_required", "unauthorized", "error"]
    session_id: str
    result: Optional[str] = None
    interrupts: Optional[list[InterruptInfo]] = None
    message: Optional[str] = None
    user_id: Optional[str] = None


class ConversationRecord(BaseModel):
    """Single conversation record stored in AgentCore Memory.

    Captures one turn of interaction (user message, agent response,
    approval request, or approval response) for later retrieval.

    Requirement 5.2
    """

    session_id: str
    timestamp: str
    user_id: str
    message_type: Literal[
        "user_message",
        "agent_response",
        "approval_request",
        "approval_response",
    ]
    content: str
