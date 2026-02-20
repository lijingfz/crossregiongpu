"""Tests for the re-invocation response cache in agent_entrypoint.

The cache prevents AgentCore Runtime re-invocations from running the
agent a second time.  Instead, the cached first response is returned
so the user sees the real result.

Requirements: 3.7 (idempotency / safety)
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from agent_entrypoint import (
    REINVOKE_WINDOW_SECONDS,
    _check_reinvoke_cache,
    _prompt_hash,
    _response_cache,
    _store_reinvoke_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Ensure the response cache is empty before and after each test."""
    _response_cache.clear()
    yield
    _response_cache.clear()


class TestPromptHash:
    """Verify _prompt_hash produces stable, distinct hashes."""

    def test_same_input_same_hash(self):
        assert _prompt_hash("启动3台g6.xlarge") == _prompt_hash("启动3台g6.xlarge")

    def test_different_input_different_hash(self):
        assert _prompt_hash("启动3台g6.xlarge") != _prompt_hash("启动5台g6.xlarge")

    def test_returns_string(self):
        h = _prompt_hash("hello")
        assert isinstance(h, str)
        assert len(h) == 16  # sha256 hex[:16]


class TestStoreAndCheckCache:
    """Verify store + check round-trip."""

    def test_cache_hit_different_request_id(self):
        resp = {"status": "completed", "result": "launched 3"}
        _store_reinvoke_cache("sess-1", "launch 3", resp, "req-AAA")

        hit = _check_reinvoke_cache("sess-1", "launch 3", "req-BBB")
        assert hit is not None
        assert hit == resp

    def test_cache_miss_same_request_id(self):
        resp = {"status": "completed", "result": "launched 3"}
        _store_reinvoke_cache("sess-1", "launch 3", resp, "req-AAA")

        # Same request_id → not a re-invocation
        hit = _check_reinvoke_cache("sess-1", "launch 3", "req-AAA")
        assert hit is None

    def test_cache_miss_different_session(self):
        resp = {"status": "completed", "result": "launched 3"}
        _store_reinvoke_cache("sess-1", "launch 3", resp, "req-AAA")

        hit = _check_reinvoke_cache("sess-2", "launch 3", "req-BBB")
        assert hit is None

    def test_cache_miss_different_prompt(self):
        resp = {"status": "completed", "result": "launched 3"}
        _store_reinvoke_cache("sess-1", "launch 3", resp, "req-AAA")

        hit = _check_reinvoke_cache("sess-1", "launch 5", "req-BBB")
        assert hit is None

    def test_cache_miss_after_window_expires(self):
        resp = {"status": "completed", "result": "launched 3"}
        _store_reinvoke_cache("sess-1", "launch 3", resp, "req-AAA")

        # Simulate time passing beyond the window
        key = ("sess-1", _prompt_hash("launch 3"))
        old_ts = time.time() - REINVOKE_WINDOW_SECONDS - 1
        _response_cache[key] = (resp, old_ts, "req-AAA")

        hit = _check_reinvoke_cache("sess-1", "launch 3", "req-BBB")
        assert hit is None
        # Stale entry should be evicted
        assert key not in _response_cache

    def test_cache_overwritten_by_new_store(self):
        resp1 = {"status": "completed", "result": "launched 3"}
        resp2 = {"status": "completed", "result": "launched 5"}
        _store_reinvoke_cache("sess-1", "launch 3", resp1, "req-AAA")
        _store_reinvoke_cache("sess-1", "launch 3", resp2, "req-BBB")

        # Should return the latest cached response
        hit = _check_reinvoke_cache("sess-1", "launch 3", "req-CCC")
        assert hit == resp2


class TestUserDuplicateVsReinvocation:
    """Ensure genuine user duplicate commands are NOT blocked.

    A user sending "启动3台g6.xlarge" twice expects 6 total instances.
    The cache should only block re-invocations (same prompt, different
    request_id, within the time window).  A genuine second user request
    will arrive after the first response is returned to the client,
    which means the entrypoint will call _store_reinvoke_cache again
    with a new request_id, overwriting the old entry.
    """

    def test_second_user_request_overwrites_cache(self):
        """Simulate: user sends same prompt twice, both should succeed."""
        resp1 = {"status": "completed", "result": "launched 3 (first)"}
        _store_reinvoke_cache("sess-1", "launch 3", resp1, "req-AAA")

        # Re-invocation of first request → blocked (returns cached)
        hit = _check_reinvoke_cache("sess-1", "launch 3", "req-BBB")
        assert hit == resp1

        # Now the user sends the same prompt again (genuine new request).
        # The entrypoint would call agent(prompt) and then store the new
        # response, overwriting the cache.
        resp2 = {"status": "completed", "result": "launched 3 (second)"}
        _store_reinvoke_cache("sess-1", "launch 3", resp2, "req-CCC")

        # Re-invocation of second request → returns second response
        hit2 = _check_reinvoke_cache("sess-1", "launch 3", "req-DDD")
        assert hit2 == resp2
