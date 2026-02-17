"""Agent session management for the Web Dashboard.

Maintains an in-memory mapping of session IDs to Agent instances,
following the same caching pattern used in ``agent_entrypoint.py``.

Requirements: 2.6
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SessionManager:
    """Manage user sessions and their associated Agent instances.

    Each session ID maps to exactly one Agent, ensuring session isolation
    (Req 2.6).  Agents are created lazily via ``build_agent()`` on first
    access.
    """

    def __init__(self) -> None:
        self._agents: dict[str, Any] = {}

    def get_or_create_agent(self, session_id: str) -> Any:
        """Return the cached Agent for *session_id*, creating one if needed.

        Requirements: 2.6
        """
        if session_id not in self._agents:
            from src.agent.main import build_agent

            self._agents[session_id] = build_agent()
            logger.info("Created new Agent for session=%s", session_id)
        return self._agents[session_id]

    def get_agent(self, session_id: str) -> Any | None:
        """Return the cached Agent for *session_id*, or ``None``."""
        return self._agents.get(session_id)

    def remove_session(self, session_id: str) -> None:
        """Remove the session and its associated Agent instance."""
        removed = self._agents.pop(session_id, None)
        if removed is not None:
            logger.info("Removed session=%s", session_id)
