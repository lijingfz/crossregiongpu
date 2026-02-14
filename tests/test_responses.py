"""Property-based tests for AgentCore response models.

Property 14: AgentResponse 序列化往返
For any valid AgentResponse instance, serializing via model_dump()
and deserializing back produces an equivalent object.

Validates: Requirements 10.5
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from src.models.responses import AgentResponse, ConversationRecord, InterruptInfo

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_status_st = st.sampled_from(["completed", "approval_required", "unauthorized", "error"])

_session_id_st = st.from_regex(r"sess_[a-z0-9]{6,12}", fullmatch=True)

_user_id_st = st.one_of(st.none(), st.from_regex(r"user_[0-9]{3}", fullmatch=True))

_interrupt_st = st.builds(
    InterruptInfo,
    interrupt_id=st.from_regex(r"intr_[a-z0-9]{6}", fullmatch=True),
    reason=st.text(min_size=1, max_size=80, alphabet=st.characters(categories=("L", "N", "Z"))),
)

_interrupts_list_st = st.one_of(st.none(), st.lists(_interrupt_st, min_size=1, max_size=5))

_result_st = st.one_of(st.none(), st.text(min_size=1, max_size=120, alphabet=st.characters(categories=("L", "N", "Z"))))

_message_st = st.one_of(st.none(), st.text(min_size=1, max_size=120, alphabet=st.characters(categories=("L", "N", "Z"))))

agent_response_st = st.builds(
    AgentResponse,
    status=_status_st,
    session_id=_session_id_st,
    result=_result_st,
    interrupts=_interrupts_list_st,
    message=_message_st,
    user_id=_user_id_st,
)

_message_type_st = st.sampled_from([
    "user_message", "agent_response", "approval_request", "approval_response",
])

_timestamp_st = st.from_regex(r"2026-0[1-2]-[012][0-9]T[01][0-9]:[0-5][0-9]:[0-5][0-9]Z", fullmatch=True)

conversation_record_st = st.builds(
    ConversationRecord,
    session_id=_session_id_st,
    timestamp=_timestamp_st,
    user_id=st.from_regex(r"user_[0-9]{3}", fullmatch=True),
    message_type=_message_type_st,
    content=st.text(min_size=1, max_size=200, alphabet=st.characters(categories=("L", "N", "Z"))),
)


# ---------------------------------------------------------------------------
# Property 14: AgentResponse 序列化往返
# Feature: agentcore-deployment, Property 14
# Validates: Requirements 10.5
# ---------------------------------------------------------------------------

class TestProperty14SerializationRoundTrip:
    """model_dump() → Model(**data) produces an equivalent object."""

    @given(resp=agent_response_st)
    @settings(max_examples=100)
    def test_agent_response_round_trip(self, resp: AgentResponse):
        """**Validates: Requirements 10.5**"""
        rebuilt = AgentResponse(**resp.model_dump())
        assert rebuilt == resp

    @given(record=conversation_record_st)
    @settings(max_examples=100)
    def test_conversation_record_round_trip(self, record: ConversationRecord):
        """**Validates: Requirements 5.2**"""
        rebuilt = ConversationRecord(**record.model_dump())
        assert rebuilt == record

    @given(info=_interrupt_st)
    @settings(max_examples=100)
    def test_interrupt_info_round_trip(self, info: InterruptInfo):
        """**Validates: Requirements 7.2**"""
        rebuilt = InterruptInfo(**info.model_dump())
        assert rebuilt == info
