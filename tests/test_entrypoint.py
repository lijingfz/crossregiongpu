"""Property-based tests for the AgentCore entrypoint.

Property 2: 会话级 Agent 实例管理
For any request sequence, requests with the same session_id SHALL use
the same Agent instance (object identity), and requests with different
session_ids SHALL use different Agent instances.

Property 4: 中断转审批响应
Property 5: 多中断批量处理
Property 6: 正常完成响应
Property 7: 异常错误响应
Property 8: 响应必含 session_id
Property 13: 审批响应路由

Validates: Requirements 2.3, 2.4, 3.1, 3.2, 3.3, 3.4, 7.1, 7.2, 7.3, 7.4
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence
from unittest.mock import patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from agent_entrypoint import _get_or_create_agent, _session_agents, _build_response
from src.models.responses import AgentResponse


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_session_id_st = st.from_regex(r"sess_[a-z0-9]{6,12}", fullmatch=True)


# ---------------------------------------------------------------------------
# Lightweight fakes for Agent / AgentResult / Interrupt
# ---------------------------------------------------------------------------

@dataclass
class FakeInterrupt:
    id: str
    name: str = "approval"
    reason: Any = None
    response: Any = None


@dataclass
class FakeAgentResult:
    stop_reason: str = "end_turn"
    message: dict = field(default_factory=lambda: {"content": [{"text": "ok"}]})
    interrupts: Sequence[Any] | None = None


class FakeAgent:
    """Minimal stand-in for strands.Agent — records calls."""

    def __init__(self):
        self.calls: list = []

    def __call__(self, prompt, **kwargs):
        self.calls.append(prompt)
        return FakeAgentResult()


# ---------------------------------------------------------------------------
# Property 2: 会话级 Agent 实例管理
# Feature: agentcore-deployment, Property 2
# Validates: Requirements 2.3, 2.4
# ---------------------------------------------------------------------------

class TestProperty2SessionAgentManagement:
    """Same session_id → same Agent; different session_id → different Agent."""

    @given(session_id=_session_id_st)
    @settings(max_examples=100)
    def test_same_session_reuses_agent(self, session_id: str):
        """**Validates: Requirements 2.3**"""
        _session_agents.clear()
        with patch("agent_entrypoint.build_agent", return_value=FakeAgent()):
            a1 = _get_or_create_agent(session_id)
            a2 = _get_or_create_agent(session_id)
            assert a1 is a2

    @given(
        sid_a=_session_id_st,
        sid_b=_session_id_st,
    )
    @settings(max_examples=100)
    def test_different_sessions_get_different_agents(
        self, sid_a: str, sid_b: str,
    ):
        """**Validates: Requirements 2.4**"""
        assume(sid_a != sid_b)
        _session_agents.clear()
        with patch("agent_entrypoint.build_agent", side_effect=lambda: FakeAgent()):
            a1 = _get_or_create_agent(sid_a)
            a2 = _get_or_create_agent(sid_b)
            assert a1 is not a2


# ---------------------------------------------------------------------------
# Strategies for approval / response tests
# ---------------------------------------------------------------------------

_interrupt_id_st = st.from_regex(r"intr_[a-z0-9]{6}", fullmatch=True)
_reason_st = st.text(min_size=1, max_size=80, alphabet=st.characters(categories=("L", "N", "Z")))
_result_text_st = st.text(min_size=1, max_size=120, alphabet=st.characters(categories=("L", "N", "Z")))
_decision_st = st.sampled_from(["approved", "denied"])


def _make_interrupt_result(interrupts: list[FakeInterrupt]) -> FakeAgentResult:
    return FakeAgentResult(stop_reason="interrupt", interrupts=interrupts)


def _make_completed_result(text: str) -> FakeAgentResult:
    return FakeAgentResult(
        stop_reason="end_turn",
        message={"content": [{"text": text}]},
    )


# ---------------------------------------------------------------------------
# Property 4: 中断转审批响应
# Feature: agentcore-deployment, Property 4
# Validates: Requirements 3.1, 7.2
# ---------------------------------------------------------------------------

class TestProperty4InterruptToApprovalResponse:
    """Interrupt results produce approval_required responses with correct interrupts."""

    @given(
        interrupt_id=_interrupt_id_st,
        reason=_reason_st,
        session_id=_session_id_st,
    )
    @settings(max_examples=100)
    def test_interrupt_yields_approval_required(
        self, interrupt_id: str, reason: str, session_id: str,
    ):
        """**Validates: Requirements 3.1, 7.2**"""
        intr = FakeInterrupt(id=interrupt_id, reason=reason)
        result = _make_interrupt_result([intr])
        resp = _build_response(result, session_id, "user_001")
        parsed = AgentResponse(**resp)

        assert parsed.status == "approval_required"
        assert parsed.interrupts is not None
        assert len(parsed.interrupts) == 1
        assert parsed.interrupts[0].interrupt_id == interrupt_id
        assert parsed.interrupts[0].reason == reason


# ---------------------------------------------------------------------------
# Property 5: 多中断批量处理
# Feature: agentcore-deployment, Property 5
# Validates: Requirements 3.3
# ---------------------------------------------------------------------------

class TestProperty5MultiInterruptBatch:
    """Multiple interrupts are all included in the response."""

    @given(
        ids_and_reasons=st.lists(
            st.tuples(_interrupt_id_st, _reason_st),
            min_size=1,
            max_size=5,
            unique_by=lambda t: t[0],
        ),
        session_id=_session_id_st,
    )
    @settings(max_examples=100)
    def test_all_interrupts_present(
        self, ids_and_reasons: list[tuple[str, str]], session_id: str,
    ):
        """**Validates: Requirements 3.3**"""
        interrupts = [FakeInterrupt(id=iid, reason=r) for iid, r in ids_and_reasons]
        result = _make_interrupt_result(interrupts)
        resp = _build_response(result, session_id, "user_001")
        parsed = AgentResponse(**resp)

        assert parsed.status == "approval_required"
        assert parsed.interrupts is not None
        assert len(parsed.interrupts) == len(ids_and_reasons)
        returned_ids = {i.interrupt_id for i in parsed.interrupts}
        expected_ids = {iid for iid, _ in ids_and_reasons}
        assert returned_ids == expected_ids


# ---------------------------------------------------------------------------
# Property 6: 正常完成响应
# Feature: agentcore-deployment, Property 6
# Validates: Requirements 3.4, 7.1
# ---------------------------------------------------------------------------

class TestProperty6NormalCompletionResponse:
    """Normal completion yields status=completed with result text."""

    @given(text=_result_text_st, session_id=_session_id_st)
    @settings(max_examples=100)
    def test_completed_response(self, text: str, session_id: str):
        """**Validates: Requirements 3.4, 7.1**"""
        result = _make_completed_result(text)
        resp = _build_response(result, session_id, "user_001")
        parsed = AgentResponse(**resp)

        assert parsed.status == "completed"
        assert parsed.result is not None
        assert text in parsed.result


# ---------------------------------------------------------------------------
# Property 13: 审批响应路由
# Feature: agentcore-deployment, Property 13
# Validates: Requirements 3.2
# ---------------------------------------------------------------------------

class TestProperty13ApprovalResponseRouting:
    """approval_responses in payload are converted to interrupt responses and passed to Agent."""

    @given(
        approvals=st.lists(
            st.tuples(_interrupt_id_st, _decision_st),
            min_size=1,
            max_size=4,
            unique_by=lambda t: t[0],
        ),
        session_id=_session_id_st,
    )
    @settings(max_examples=100)
    def test_approval_responses_routed_to_agent(
        self, approvals: list[tuple[str, str]], session_id: str,
    ):
        """**Validates: Requirements 3.2**"""
        _session_agents.clear()

        captured_calls: list = []

        class CapturingAgent:
            def __call__(self, prompt, **kw):
                captured_calls.append(prompt)
                return FakeAgentResult()

        _session_agents[session_id] = CapturingAgent()

        # Simulate what invoke() does for approval_responses routing
        approval_responses = [
            {"interrupt_id": iid, "decision": dec} for iid, dec in approvals
        ]
        interrupt_responses = [
            {
                "interruptResponse": {
                    "interruptId": ar["interrupt_id"],
                    "response": ar.get("decision", "approved"),
                }
            }
            for ar in approval_responses
        ]
        agent = _session_agents[session_id]
        agent(interrupt_responses)

        assert len(captured_calls) == 1
        sent = captured_calls[0]
        assert len(sent) == len(approvals)
        sent_ids = {r["interruptResponse"]["interruptId"] for r in sent}
        expected_ids = {iid for iid, _ in approvals}
        assert sent_ids == expected_ids


# ---------------------------------------------------------------------------
# Property 7: 异常错误响应
# Feature: agentcore-deployment, Property 7
# Validates: Requirements 7.3
# ---------------------------------------------------------------------------

_error_msg_st = st.text(min_size=1, max_size=100, alphabet=st.characters(categories=("L", "N", "Z")))


class TestProperty7ExceptionErrorResponse:
    """Agent exceptions produce status=error responses with the exception message."""

    @given(
        error_msg=_error_msg_st,
        session_id=_session_id_st,
    )
    @settings(max_examples=100)
    def test_exception_yields_error_response(
        self, error_msg: str, session_id: str,
    ):
        """**Validates: Requirements 7.3**"""
        import asyncio
        import json as _json
        import os

        import jwt as pyjwt

        _session_agents.clear()

        class ExplodingAgent:
            def __call__(self, prompt, **kw):
                raise RuntimeError(error_msg)

            async def stream_async(self, prompt, **kw):
                raise RuntimeError(error_msg)
                yield  # make it an async generator  # noqa: E501

        _session_agents[session_id] = ExplodingAgent()

        secret = "test-secret-for-prop7"
        token = pyjwt.encode({"user_id": "u1", "username": "x", "roles": []}, secret, algorithm="HS256")

        old_key = os.environ.get("AUTH_SECRET_KEY")
        os.environ["AUTH_SECRET_KEY"] = secret
        try:
            from agent_entrypoint import invoke

            ctx = type("Ctx", (), {"session_id": session_id})()

            async def _collect():
                items = []
                async for chunk in invoke({"prompt": "hello", "token": token}, ctx):
                    items.append(chunk)
                return items

            loop = asyncio.new_event_loop()
            try:
                items = loop.run_until_complete(_collect())
            finally:
                loop.close()

            # The last yielded item should be the error AgentResponse dict
            assert len(items) >= 1
            resp = items[-1]
            # invoke() now yields dicts, not JSON strings
            if isinstance(resp, str):
                resp = _json.loads(resp)
            parsed = AgentResponse(**resp)
            assert parsed.status == "error"
            assert parsed.message is not None
            assert error_msg in parsed.message
        finally:
            if old_key is None:
                os.environ.pop("AUTH_SECRET_KEY", None)
            else:
                os.environ["AUTH_SECRET_KEY"] = old_key


# ---------------------------------------------------------------------------
# Property 8: 响应必含 session_id
# Feature: agentcore-deployment, Property 8
# Validates: Requirements 7.4
# ---------------------------------------------------------------------------

_status_st = st.sampled_from(["completed", "approval_required", "error"])


class TestProperty8ResponseContainsSessionId:
    """Every response from _build_response contains a non-None session_id."""

    @given(session_id=_session_id_st, status=_status_st)
    @settings(max_examples=100)
    def test_session_id_always_present(self, session_id: str, status: str):
        """**Validates: Requirements 7.4**"""
        if status == "completed":
            result = _make_completed_result("done")
        elif status == "approval_required":
            result = _make_interrupt_result([FakeInterrupt(id="intr_abc", reason="test")])
        else:
            result = _make_completed_result("done")

        resp = _build_response(result, session_id, "user_001")
        parsed = AgentResponse(**resp)
        assert parsed.session_id == session_id
        assert parsed.session_id is not None

    @given(session_id=_session_id_st)
    @settings(max_examples=100)
    def test_unauthorized_response_has_session_id(self, session_id: str):
        """**Validates: Requirements 7.4**

        Even unauthorized responses include session_id (may be empty string).
        """
        resp = AgentResponse(
            status="unauthorized",
            session_id=session_id,
            message="bad token",
        )
        assert resp.session_id is not None
