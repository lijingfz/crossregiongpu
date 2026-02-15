"""Property-based tests for conversation record persistence.

Property 9: 对话记录完整性
For any successfully processed request, the conversation record stored
to Memory SHALL contain session_id, timestamp, user_id, message, and
message_type — all non-empty.

Property 10: 对话记录存取往返
For any stored conversation record set, querying by session_id SHALL
return all records for that session, with content matching what was stored.

Validates: Requirements 5.1, 5.2, 5.4
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

from hypothesis import given, settings
from hypothesis import strategies as st

from src.agent.memory import store_conversation, retrieve_conversation


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_session_id_st = st.from_regex(r"sess_[a-z0-9]{6,12}", fullmatch=True)
_user_id_st = st.from_regex(r"user_[a-z0-9]{3,8}", fullmatch=True)
_content_st = st.text(
    min_size=1, max_size=120,
    alphabet=st.characters(categories=("L", "N", "Z")),
)
_role_st = st.sampled_from(["USER", "ASSISTANT", "TOOL"])


# ---------------------------------------------------------------------------
# Property 9: 对话记录完整性
# Feature: agentcore-deployment, Property 9
# Validates: Requirements 5.1, 5.2
# ---------------------------------------------------------------------------

class TestProperty9ConversationRecordCompleteness:
    """Stored conversation events contain all required fields and are non-empty."""

    @given(
        session_id=_session_id_st,
        user_id=_user_id_st,
        messages=st.lists(
            st.tuples(_content_st, _role_st),
            min_size=1,
            max_size=5,
        ),
    )
    @settings(max_examples=100)
    def test_store_passes_complete_record(
        self,
        session_id: str,
        user_id: str,
        messages: list[tuple[str, str]],
    ):
        """**Validates: Requirements 5.1, 5.2**

        Verifies that store_conversation calls MemoryClient.create_event
        with session_id, actor_id (user_id), messages, and a timestamp —
        all non-empty.
        """
        mock_client = MagicMock()
        mock_client.create_event = MagicMock(return_value={"eventId": "evt_1"})

        with (
            patch("src.agent.memory._get_memory_client", return_value=mock_client),
            patch.dict(os.environ, {"MEMORY_ID": "mem_test123"}),
        ):
            result = store_conversation(
                session_id=session_id,
                user_id=user_id,
                messages=messages,
            )

        assert result is True
        mock_client.create_event.assert_called_once()
        call_kwargs = mock_client.create_event.call_args

        # All required fields present and non-empty
        assert call_kwargs.kwargs["memory_id"] == "mem_test123"
        assert call_kwargs.kwargs["actor_id"] == user_id
        assert call_kwargs.kwargs["session_id"] == session_id
        assert len(call_kwargs.kwargs["messages"]) == len(messages)
        assert isinstance(call_kwargs.kwargs["event_timestamp"], datetime)

        # Verify each message tuple is preserved
        for (orig_content, orig_role), (sent_content, sent_role) in zip(
            messages, call_kwargs.kwargs["messages"]
        ):
            assert sent_content == orig_content
            assert sent_role == orig_role


# ---------------------------------------------------------------------------
# Property 10: 对话记录存取往返
# Feature: agentcore-deployment, Property 10
# Validates: Requirements 5.4
# ---------------------------------------------------------------------------

class TestProperty10ConversationRoundTrip:
    """Stored records can be retrieved by session_id with matching content."""

    @given(
        session_id=_session_id_st,
        user_id=_user_id_st,
        messages=st.lists(
            st.tuples(_content_st, _role_st),
            min_size=1,
            max_size=5,
        ),
    )
    @settings(max_examples=100)
    def test_store_then_retrieve_roundtrip(
        self,
        session_id: str,
        user_id: str,
        messages: list[tuple[str, str]],
    ):
        """**Validates: Requirements 5.1, 5.2, 5.4**

        Stores messages via store_conversation, then retrieves via
        retrieve_conversation and verifies the returned events contain
        the same session_id, actor_id, and message content.
        """
        # Build the fake event that list_events would return
        stored_event: dict[str, Any] = {
            "eventId": "evt_abc",
            "actorId": user_id,
            "sessionId": session_id,
            "payload": {
                "messages": [
                    {"content": c, "role": r} for c, r in messages
                ],
            },
        }

        mock_client = MagicMock()
        mock_client.create_event = MagicMock(return_value={"eventId": "evt_abc"})
        mock_client.list_events = MagicMock(return_value=[stored_event])

        with (
            patch("src.agent.memory._get_memory_client", return_value=mock_client),
            patch.dict(os.environ, {"MEMORY_ID": "mem_test123"}),
        ):
            # Store
            ok = store_conversation(
                session_id=session_id,
                user_id=user_id,
                messages=messages,
            )
            assert ok is True

            # Retrieve
            events = retrieve_conversation(
                session_id=session_id,
                user_id=user_id,
            )

        assert len(events) == 1
        evt = events[0]
        assert evt["sessionId"] == session_id
        assert evt["actorId"] == user_id

        # Verify message content round-trips
        payload_messages = evt["payload"]["messages"]
        assert len(payload_messages) == len(messages)
        for (orig_content, orig_role), returned in zip(messages, payload_messages):
            assert returned["content"] == orig_content
            assert returned["role"] == orig_role
