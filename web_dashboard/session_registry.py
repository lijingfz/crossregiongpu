"""Lightweight session registry for tracking recent conversations.

Stores session metadata (id, first message preview, timestamp) in a
local JSON file so the Web Dashboard can display recent conversations
in a sidebar. AgentCore Memory is session-scoped and has no "list all
sessions" API, so we maintain this index ourselves.

The registry is capped at 50 entries (oldest evicted first).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REGISTRY_PATH = os.environ.get(
    "SESSION_REGISTRY_PATH",
    str(Path(__file__).resolve().parent / ".session_registry.json"),
)
_MAX_ENTRIES = 50
_lock = threading.Lock()


def _load() -> list[dict[str, Any]]:
    try:
        with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(entries: list[dict[str, Any]]) -> None:
    try:
        with open(_REGISTRY_PATH, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception("Failed to save session registry")


def record_session(
    session_id: str,
    user_id: str,
    preview: str,
) -> None:
    """Record or update a session entry with a message preview."""
    with _lock:
        entries = _load()
        now = datetime.now(timezone.utc).isoformat()

        # Update existing or append new
        found = False
        for entry in entries:
            if entry.get("session_id") == session_id:
                entry["updated_at"] = now
                entry["preview"] = preview[:80]
                found = True
                break

        if not found:
            entries.append({
                "session_id": session_id,
                "user_id": user_id,
                "preview": preview[:80],
                "created_at": now,
                "updated_at": now,
            })

        # Sort by updated_at desc, cap at _MAX_ENTRIES
        entries.sort(key=lambda e: e.get("updated_at", ""), reverse=True)
        entries = entries[:_MAX_ENTRIES]
        _save(entries)


def list_recent_sessions(user_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return the most recent sessions for a user."""
    with _lock:
        entries = _load()
    # Filter by user_id and return up to limit
    result = [e for e in entries if e.get("user_id") == user_id]
    return result[:limit]
