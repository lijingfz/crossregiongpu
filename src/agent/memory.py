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


def _get_memory_id() -> str:
    """Return the Memory resource ID from env var or dev.yaml fallback."""
    mid = os.environ.get("MEMORY_ID", "")
    if mid:
        return mid
    try:
        import yaml
        with open("config/environments/dev.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        mid = cfg.get("memory_id", "")
    except Exception:
        pass
    return mid


def _get_memory_region() -> str:
    """Return the Memory region from env var or dev.yaml fallback."""
    region = os.environ.get("MEMORY_REGION", "")
    if region:
        return region
    try:
        import yaml
        with open("config/environments/dev.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        region = cfg.get("memory_region", "")
    except Exception:
        pass
    return region or "us-west-2"


def _get_memory_client() -> Any:
    """Lazily import and return a ``MemoryClient`` instance."""
    from bedrock_agentcore.memory import MemoryClient

    region = _get_memory_region()
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
    memory_id = _get_memory_id()
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
    memory_id = _get_memory_id()
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


def search_ltm(
    *,
    query: str,
    namespace: str = "gpu_scheduler",
    max_results: int = 5,
) -> list[dict[str, Any]]:
    """Search long-term memories via semantic query.

    Parameters
    ----------
    query:
        Natural-language query for semantic search.
    namespace:
        LTM namespace (must match the strategy namespace).
    max_results:
        Maximum number of memory records to return.

    Returns
    -------
    list[dict]
        List of LTM records, or empty list on failure.

    Requirements: 5.5
    """
    memory_id = _get_memory_id()
    if not memory_id:
        logger.warning("MEMORY_ID not configured – cannot search LTM")
        return []

    try:
        client = _get_memory_client()
        results = client.retrieve_memories(
            memory_id=memory_id,
            namespace=namespace,
            query=query,
            top_k=max_results,
        )
        return results if results else []
    except Exception:
        logger.exception("Failed to search LTM (query=%s)", query)
        return []


def retrieve_ltm_context(max_results: int = 5, max_chars: int = 1000) -> str:
    """Retrieve LTM knowledge and format as a context string for the agent.

    Queries LTM for GPU scheduling experience and returns a formatted
    text block suitable for injection into the system prompt.

    Parameters
    ----------
    max_results:
        Maximum number of LTM records to retrieve.
    max_chars:
        Hard cap on the total character length of the returned context.
        Prevents prompt bloat as LTM accumulates over time.

    Returns
    -------
    str
        Formatted LTM context string, or empty string if no memories found.

    Requirements: 5.5
    """
    memories = search_ltm(
        query="GPU instance capacity scheduling experience and region availability",
        max_results=max_results,
    )
    if not memories:
        return ""

    lines = ["\n\n## Historical Knowledge (from Long-Term Memory)\n"]
    total_len = len(lines[0])
    for i, mem in enumerate(memories, 1):
        content = (
            mem.get("content", "") if isinstance(mem, dict) else str(mem)
        )
        if not content:
            continue
        entry = f"{i}. {content}"
        if total_len + len(entry) + 1 > max_chars:
            break
        lines.append(entry)
        total_len += len(entry) + 1

    return "\n".join(lines) if len(lines) > 1 else ""
