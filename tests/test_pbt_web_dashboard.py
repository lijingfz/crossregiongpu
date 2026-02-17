"""Property-based tests for Web Dashboard models.

Property 6: Unified API response format
For any API response (success or error), the response body is a valid
JSON object containing ``status`` (either "success" or "error"),
``data`` (dict or None), and ``message`` (string) fields.

Validates: Requirements 5.4, 6.6
"""

from __future__ import annotations

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from web_dashboard.models import ApiResponse

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_status_st = st.sampled_from(["success", "error"])

_data_st = st.one_of(
    st.none(),
    st.dictionaries(
        keys=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            min_size=1,
            max_size=10,
        ),
        values=st.one_of(
            st.text(max_size=50),
            st.integers(min_value=-1000, max_value=1000),
            st.booleans(),
            st.none(),
        ),
        min_size=0,
        max_size=5,
    ),
)

_message_st = st.text(max_size=200)


api_response_st = st.builds(
    ApiResponse,
    status=_status_st,
    data=_data_st,
    message=_message_st,
)


# ---------------------------------------------------------------------------
# Property 6: Unified API response format
# Feature: web-dashboard, Property 6
# Validates: Requirements 5.4, 6.6
# ---------------------------------------------------------------------------

class TestProperty6UnifiedApiResponseFormat:
    """Every ApiResponse has status, data, and message fields with correct types."""

    @given(resp=api_response_st)
    @settings(max_examples=100)
    def test_response_contains_required_fields(self, resp: ApiResponse):
        """**Validates: Requirements 5.4, 6.6**"""
        dumped = resp.model_dump()
        assert "status" in dumped
        assert "data" in dumped
        assert "message" in dumped

    @given(resp=api_response_st)
    @settings(max_examples=100)
    def test_status_is_success_or_error(self, resp: ApiResponse):
        """**Validates: Requirements 6.6**"""
        assert resp.status in ("success", "error")

    @given(resp=api_response_st)
    @settings(max_examples=100)
    def test_data_is_dict_or_none(self, resp: ApiResponse):
        """**Validates: Requirements 6.6**"""
        assert resp.data is None or isinstance(resp.data, dict)

    @given(resp=api_response_st)
    @settings(max_examples=100)
    def test_message_is_string(self, resp: ApiResponse):
        """**Validates: Requirements 6.6**"""
        assert isinstance(resp.message, str)

    @given(resp=api_response_st)
    @settings(max_examples=100)
    def test_json_serialization_round_trip(self, resp: ApiResponse):
        """**Validates: Requirements 5.4, 6.6**"""
        json_str = resp.model_dump_json()
        parsed = json.loads(json_str)
        rebuilt = ApiResponse(**parsed)
        assert rebuilt == resp


# ---------------------------------------------------------------------------
# Shared test fixtures for auth property tests
# ---------------------------------------------------------------------------

import os
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from hypothesis import assume

from web_dashboard.dependencies import get_current_user
from web_dashboard.models import LoginRequest

_AUTH_SECRET = "pbt-web-dashboard-secret"


def _build_test_app() -> FastAPI:
    """Build a minimal FastAPI app with stub protected endpoints."""
    app = FastAPI()

    @app.get("/api/chat/history")
    async def _history(user: dict = Depends(get_current_user)):
        return {"status": "success", "data": None, "message": "ok"}

    @app.post("/api/chat/send")
    async def _send(user: dict = Depends(get_current_user)):
        return {"status": "success", "data": None, "message": "ok"}

    @app.post("/api/chat/approve")
    async def _approve(user: dict = Depends(get_current_user)):
        return {"status": "success", "data": None, "message": "ok"}

    return app


def _make_valid_token(
    user_id: str = "u1",
    username: str = "tester",
    roles: list[str] | None = None,
    secret: str = _AUTH_SECRET,
    expires_hours: int = 24,
) -> str:
    now = datetime.now(timezone.utc)
    return pyjwt.encode(
        {
            "user_id": user_id,
            "sub": user_id,
            "username": username,
            "roles": roles or ["operator"],
            "iat": now,
            "exp": now + timedelta(hours=expires_hours),
        },
        secret,
        algorithm="HS256",
    )


# Strategies for auth tests
_invalid_token_st = st.one_of(
    st.just("garbage-token"),
    st.just("not.a.jwt"),
    st.just("eyJhbGciOiJIUzI1NiJ9.invalid.payload"),
    st.from_regex(r"[a-zA-Z0-9]{1,40}", fullmatch=True),
)

_protected_endpoints = [
    ("GET", "/api/chat/history"),
    ("POST", "/api/chat/send"),
    ("POST", "/api/chat/approve"),
]


# ---------------------------------------------------------------------------
# Property 1: Protected endpoint auth rejection
# Feature: web-dashboard, Property 1
# Validates: Requirements 1.1, 1.4, 6.5
# ---------------------------------------------------------------------------

class TestProperty1ProtectedEndpointAuthRejection:
    """Requests with missing, malformed, or expired tokens get HTTP 401."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        old = os.environ.get("AUTH_SECRET_KEY")
        os.environ["AUTH_SECRET_KEY"] = _AUTH_SECRET
        self.app = _build_test_app()
        self.client = TestClient(self.app, raise_server_exceptions=False)
        yield
        if old is None:
            os.environ.pop("AUTH_SECRET_KEY", None)
        else:
            os.environ["AUTH_SECRET_KEY"] = old

    @given(token=_invalid_token_st)
    @settings(max_examples=100)
    def test_invalid_token_returns_401(self, token: str):
        """**Validates: Requirements 1.1, 1.4, 6.5**"""
        for method, path in _protected_endpoints:
            if method == "GET":
                resp = self.client.get(path, headers={"Authorization": f"Bearer {token}"})
            else:
                resp = self.client.post(path, headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code in (401, 403), (
                f"{method} {path} returned {resp.status_code} for token={token!r}"
            )

    @given(
        wrong_secret=st.from_regex(r"wrong-[a-z]{4,8}", fullmatch=True),
    )
    @settings(max_examples=100)
    def test_wrong_secret_returns_401(self, wrong_secret: str):
        """**Validates: Requirements 1.4, 6.5**"""
        assume(wrong_secret != _AUTH_SECRET)
        token = _make_valid_token(secret=wrong_secret)
        for method, path in _protected_endpoints:
            if method == "GET":
                resp = self.client.get(path, headers={"Authorization": f"Bearer {token}"})
            else:
                resp = self.client.post(path, headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code in (401, 403), (
                f"{method} {path} returned {resp.status_code} for wrong-secret token"
            )

    def test_missing_auth_header_returns_401_or_403(self):
        """**Validates: Requirements 1.1, 6.5**"""
        for method, path in _protected_endpoints:
            if method == "GET":
                resp = self.client.get(path)
            else:
                resp = self.client.post(path)
            assert resp.status_code in (401, 403)

    def test_expired_token_returns_401(self):
        """**Validates: Requirements 1.4, 6.5**"""
        now = datetime.now(timezone.utc)
        token = pyjwt.encode(
            {
                "user_id": "u1",
                "sub": "u1",
                "username": "tester",
                "roles": [],
                "iat": now - timedelta(hours=2),
                "exp": now - timedelta(hours=1),
            },
            _AUTH_SECRET,
            algorithm="HS256",
        )
        for method, path in _protected_endpoints:
            if method == "GET":
                resp = self.client.get(path, headers={"Authorization": f"Bearer {token}"})
            else:
                resp = self.client.post(path, headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Property 2: Login token round-trip
# Feature: web-dashboard, Property 2
# Validates: Requirements 1.2
# ---------------------------------------------------------------------------

class TestProperty2LoginTokenRoundTrip:
    """Tokens issued by login can be validated back to the same identity."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        old_secret = os.environ.get("AUTH_SECRET_KEY")
        old_user = os.environ.get("WEB_DASHBOARD_USERNAME")
        old_pass = os.environ.get("WEB_DASHBOARD_PASSWORD")
        os.environ["AUTH_SECRET_KEY"] = _AUTH_SECRET
        yield
        # restore
        for key, old in [
            ("AUTH_SECRET_KEY", old_secret),
            ("WEB_DASHBOARD_USERNAME", old_user),
            ("WEB_DASHBOARD_PASSWORD", old_pass),
        ]:
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

    @given(
        username=st.from_regex(r"[a-z]{3,12}", fullmatch=True),
        password=st.from_regex(r"[a-zA-Z0-9]{6,20}", fullmatch=True),
    )
    @settings(max_examples=100)
    def test_login_token_validates_to_same_user(self, username: str, password: str):
        """**Validates: Requirements 1.2**"""
        from web_dashboard.routers.auth import router as auth_router

        # Configure credentials to match the generated ones
        os.environ["WEB_DASHBOARD_USERNAME"] = username
        os.environ["WEB_DASHBOARD_PASSWORD"] = password

        app = FastAPI()
        app.include_router(auth_router)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["data"]["username"] == username
        assert body["data"]["user_id"] == username

        # Round-trip: validate the issued token
        from src.agent.auth import validate_token

        token = body["data"]["token"]
        user_info = validate_token(token)
        assert user_info["user_id"] == username
        assert user_info["username"] == username


# ---------------------------------------------------------------------------
# Property 5: Session isolation
# Feature: web-dashboard, Property 5
# Validates: Requirements 2.6
# ---------------------------------------------------------------------------


class TestProperty5SessionIsolation:
    """Distinct session IDs yield distinct Agent instances."""

    @given(
        id_a=st.from_regex(r"sess-[a-z]{4,8}", fullmatch=True),
        id_b=st.from_regex(r"sess-[a-z]{4,8}", fullmatch=True),
    )
    @settings(max_examples=100)
    def test_different_sessions_return_different_agents(
        self, id_a: str, id_b: str
    ):
        """**Validates: Requirements 2.6**"""
        assume(id_a != id_b)

        from unittest.mock import MagicMock, patch

        from web_dashboard.session_manager import SessionManager

        mock_agent_a = MagicMock(name="agent_a")
        mock_agent_b = MagicMock(name="agent_b")
        call_count = 0

        def _fake_build_agent(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 1:
                return mock_agent_a
            return mock_agent_b

        sm = SessionManager()
        with patch(
            "src.agent.main.build_agent",
            side_effect=_fake_build_agent,
        ):
            agent_a = sm.get_or_create_agent(id_a)
            agent_b = sm.get_or_create_agent(id_b)

        assert agent_a is not agent_b, (
            f"Sessions {id_a!r} and {id_b!r} returned the same Agent instance"
        )

    @given(session_id=st.from_regex(r"sess-[a-z]{4,8}", fullmatch=True))
    @settings(max_examples=100)
    def test_same_session_returns_same_agent(self, session_id: str):
        """**Validates: Requirements 2.6**"""
        from unittest.mock import MagicMock, patch

        from web_dashboard.session_manager import SessionManager

        mock_agent = MagicMock(name="agent")
        sm = SessionManager()
        with patch(
            "src.agent.main.build_agent",
            return_value=mock_agent,
        ):
            first = sm.get_or_create_agent(session_id)
            second = sm.get_or_create_agent(session_id)

        assert first is second, "Same session ID should return the same Agent"

    @given(session_id=st.from_regex(r"sess-[a-z]{4,8}", fullmatch=True))
    @settings(max_examples=100)
    def test_remove_session_clears_agent(self, session_id: str):
        """**Validates: Requirements 2.6**"""
        from unittest.mock import MagicMock, patch

        from web_dashboard.session_manager import SessionManager

        sm = SessionManager()
        with patch(
            "src.agent.main.build_agent",
            return_value=MagicMock(),
        ):
            sm.get_or_create_agent(session_id)
            sm.remove_session(session_id)
            assert sm.get_agent(session_id) is None


# ---------------------------------------------------------------------------
# Property 3: Chat API returns valid response for valid messages
# Feature: web-dashboard, Property 3
# Validates: Requirements 2.1
# ---------------------------------------------------------------------------


class TestProperty3ChatApiValidResponse:
    """Non-empty messages produce a valid ApiResponse with a recognised agent_status."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        old = os.environ.get("AUTH_SECRET_KEY")
        os.environ["AUTH_SECRET_KEY"] = _AUTH_SECRET
        yield
        if old is None:
            os.environ.pop("AUTH_SECRET_KEY", None)
        else:
            os.environ["AUTH_SECRET_KEY"] = old

    @given(
        message=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
            min_size=1,
            max_size=100,
        ),
    )
    @settings(max_examples=100)
    def test_valid_message_returns_valid_response(self, message: str):
        """**Validates: Requirements 2.1**"""
        assume(message.strip())  # skip whitespace-only

        from unittest.mock import patch

        from web_dashboard.routers.chat import router as chat_router

        # Mock the AgentCore Runtime response (AgentResponse format)
        mock_agentcore_response = {
            "status": "completed",
            "session_id": "s1",
            "result": "mock response",
            "user_id": "test_user",
        }

        app = FastAPI()
        app.include_router(chat_router)
        client = TestClient(app, raise_server_exceptions=False)

        token = _make_valid_token()

        with patch(
            "web_dashboard.routers.chat.invoke_agent",
            return_value=mock_agentcore_response,
        ):
            resp = client.post(
                "/api/chat/send",
                json={"session_id": "s1", "message": message},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in ("success", "error")
        if body["status"] == "success" and body.get("data"):
            assert body["data"]["agent_status"] in (
                "completed",
                "approval_required",
                "error",
            )


# ---------------------------------------------------------------------------
# Property 4: Whitespace message rejection
# Feature: web-dashboard, Property 4
# Validates: Requirements 2.5
# ---------------------------------------------------------------------------


class TestProperty4WhitespaceMessageRejection:
    """Whitespace-only messages are rejected without invoking the Agent."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        old = os.environ.get("AUTH_SECRET_KEY")
        os.environ["AUTH_SECRET_KEY"] = _AUTH_SECRET
        yield
        if old is None:
            os.environ.pop("AUTH_SECRET_KEY", None)
        else:
            os.environ["AUTH_SECRET_KEY"] = old

    @given(
        whitespace=st.from_regex(r"[\s]+", fullmatch=True).filter(
            lambda s: not s.strip()
        ),
    )
    @settings(max_examples=100)
    def test_whitespace_message_rejected(self, whitespace: str):
        """**Validates: Requirements 2.5**"""
        from unittest.mock import MagicMock, patch

        from web_dashboard.routers.chat import router as chat_router

        mock_invoke = MagicMock()

        app = FastAPI()
        app.include_router(chat_router)
        client = TestClient(app, raise_server_exceptions=False)

        token = _make_valid_token()

        with patch(
            "web_dashboard.routers.chat.invoke_agent",
            mock_invoke,
        ):
            resp = client.post(
                "/api/chat/send",
                json={"session_id": "s1", "message": whitespace},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "error"
        mock_invoke.assert_not_called()

    def test_empty_string_rejected(self):
        """**Validates: Requirements 2.5**"""
        from unittest.mock import MagicMock, patch

        from web_dashboard.routers.chat import router as chat_router

        mock_invoke = MagicMock()

        app = FastAPI()
        app.include_router(chat_router)
        client = TestClient(app, raise_server_exceptions=False)

        token = _make_valid_token()

        with patch(
            "web_dashboard.routers.chat.invoke_agent",
            mock_invoke,
        ):
            resp = client.post(
                "/api/chat/send",
                json={"session_id": "s1", "message": ""},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "error"
        mock_invoke.assert_not_called()
