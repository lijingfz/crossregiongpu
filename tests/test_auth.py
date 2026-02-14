"""Property-based tests for the authentication module.

Property 11: 无效令牌拒绝
For any missing, empty, malformed, or expired token, validate_token()
SHALL raise AuthenticationError.

Property 12: 令牌身份提取
For any valid JWT containing a user_id claim, validate_token() SHALL
return a dict with the same user_id.

Validates: Requirements 6.1, 6.2, 6.3
"""

from __future__ import annotations

import os

import jwt as pyjwt
import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.agent.auth import AuthenticationError, validate_token

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEST_SECRET = "test-secret-key-for-pbt"


def _make_token(payload: dict, secret: str = TEST_SECRET) -> str:
    return pyjwt.encode(payload, secret, algorithm="HS256")


def _with_secret(fn):
    """Set AUTH_SECRET_KEY for the duration of the call, then restore."""
    def wrapper(*args, **kwargs):
        old = os.environ.get("AUTH_SECRET_KEY")
        os.environ["AUTH_SECRET_KEY"] = TEST_SECRET
        try:
            return fn(*args, **kwargs)
        finally:
            if old is None:
                os.environ.pop("AUTH_SECRET_KEY", None)
            else:
                os.environ["AUTH_SECRET_KEY"] = old
    return wrapper


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_user_id_st = st.from_regex(r"user_[0-9]{3,6}", fullmatch=True)
_username_st = st.from_regex(r"[a-z]{3,10}", fullmatch=True)
_roles_st = st.lists(st.sampled_from(["admin", "operator", "viewer"]), min_size=0, max_size=3)

_invalid_token_st = st.one_of(
    st.just(""),
    st.just("   "),
    st.text(min_size=1, max_size=50, alphabet=st.characters(categories=("L", "N"))),
    st.just("not.a.jwt"),
    st.just("eyJhbGciOiJIUzI1NiJ9.invalid.payload"),
)


# ---------------------------------------------------------------------------
# Property 11: 无效令牌拒绝
# Feature: agentcore-deployment, Property 11
# Validates: Requirements 6.1, 6.2
# ---------------------------------------------------------------------------

class TestProperty11InvalidTokenRejection:
    """Invalid tokens must always raise AuthenticationError."""

    @given(token=_invalid_token_st)
    @settings(max_examples=100)
    def test_invalid_tokens_rejected(self, token: str):
        """**Validates: Requirements 6.1, 6.2**"""
        @_with_secret
        def _run():
            with pytest.raises(AuthenticationError):
                validate_token(token)
        _run()

    @given(
        user_id=_user_id_st,
        wrong_secret=st.from_regex(r"wrong-[a-z]{4,8}", fullmatch=True),
    )
    @settings(max_examples=100)
    def test_wrong_secret_rejected(self, user_id: str, wrong_secret: str):
        """**Validates: Requirements 6.2**"""
        assume(wrong_secret != TEST_SECRET)

        @_with_secret
        def _run():
            token = _make_token({"user_id": user_id}, secret=wrong_secret)
            with pytest.raises(AuthenticationError):
                validate_token(token)
        _run()


# ---------------------------------------------------------------------------
# Property 12: 令牌身份提取
# Feature: agentcore-deployment, Property 12
# Validates: Requirements 6.3
# ---------------------------------------------------------------------------

class TestProperty12TokenIdentityExtraction:
    """Valid tokens must yield the correct user_id."""

    @given(user_id=_user_id_st, username=_username_st, roles=_roles_st)
    @settings(max_examples=100)
    def test_valid_token_extracts_identity(
        self, user_id: str, username: str, roles: list[str],
    ):
        """**Validates: Requirements 6.3**"""
        @_with_secret
        def _run():
            token = _make_token({
                "user_id": user_id,
                "username": username,
                "roles": roles,
            })
            result = validate_token(token)
            assert result["user_id"] == user_id
            assert result["username"] == username
            assert result["roles"] == roles
        _run()
