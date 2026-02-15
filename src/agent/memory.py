"""AgentCore Memory integration for conversation record persistence.

Wraps the ``bedrock_agentcore.memory.MemoryClient`` to store and retrieve
conversation records.  All public functions catch exceptions internally
so that memory failures never block the main request/response flow.

Configuration via environment variables:

- ``MEMORY_ID``      – AgentCore Memory resource ID (required for storage)
- ``MEMORY_REGION``  – AWS region for the Memory service (default: us-west-2)

Requirements: 5.1, 5.2, 5.3, 5.4
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _get_memory_client() -> Any:
    """Lazily import and return a ``MemoryClient`` instance."""
    from bedrock_agentcore.memory import MemoryClient

    region = os.environ.get("MEMORY_REGION", "us-west-2")
    return MemoryClient(region_name=region)


def store_conversation(
    *,
    session_id: str,
    user_id: str,
    messages: list[tuple[str, str]],
) -> bool:
    """Store conversation messages to AgentCore Memory.

    Parameters
    ----------
    session_id:
        The session identifier for this conversation.
    user_id:
        The actor / user identifier.
    messages:
        List of ``(content, role)`` tuples where *role* is one of
        ``"USER"``, ``"ASSISTANT"``, ``"TOOL"``.

    Returns
    -------
    bool
        ``True`` if the event was stored successfully, ``False`` otherwise.
        Failures are logged but never raised (Req 5.3).
    """
    memory_id = os.environ.get("MEMORY_ID", "")
    if not memory_id:
        logger.warning("MEMORY_ID not configured – skipping conversation storage")
        return False

    try:
        client = _get_memory_client()
        client.create_event(
            memory_id=memory_id,
            actor_id=user_id,
            session_id=session_id,
            messages=messages,
            event_timestamp=datetime.now(timezone.utc),
        )
        return True
    except Exception:
        logger.exception(
            "Failed to store conversation to Memory (session=%s)", session_id
        )
        return False


def retrieve_conversation(
    *,
    session_id: str,
    user_id: str,
    max_results: int = 100,
) -> list[dict[str, Any]]:
    """Retrieve conversation events for a session from AgentCore Memory.

    Parameters
    ----------
    session_id:
        The session to query.
    user_id:
        The actor / user identifier.
    max_results:
        Maximum number of events to return.

    Returns
    -------
    list[dict]
        List of event dicts, or an empty list on failure (Req 5.4).
    """
    memory_id = os.environ.get("MEMORY_ID", "")
    if not memory_id:
        logger.warning("MEMORY_ID not configured – cannot retrieve conversation")
        return []

    try:
        client = _get_memory_client()
        events = client.list_events(
            memory_id=memory_id,
            actor_id=user_id,
            session_id=session_id,
            max_results=max_results,
        )
        return events
    except Exception:
        logger.exception(
            "Failed to retrieve conversation from Memory (session=%s)", session_id
        )
        return []
