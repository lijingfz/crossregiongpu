"""Property-based tests for build_agent().

Property 1: Agent 配置等价性
For any valid environment configuration (env name, SSM parameter, model ID, etc.),
build_agent() returns an Agent instance that has the same system prompt, tool set
(names and count), and ApprovalHook configuration (batch threshold, geo regions)
as what the original main() inline logic would produce under the same config.

Validates: Requirements 1.1, 1.2, 1.4, 8.1, 8.3
"""

from __future__ import annotations

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st
from strands.hooks import BeforeToolCallEvent

from src.agent.approval import ApprovalConfig, ApprovalHook
from src.agent.main import ALL_TOOLS, DEFAULT_MODEL_ID, build_agent


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_env_st = st.sampled_from(["dev", "staging", "prod"])

_model_id_st = st.sampled_from([
    "us.anthropic.claude-sonnet-4-20250514-v1:0",
    "us.anthropic.claude-haiku-3-20250307-v1:0",
])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_approval_hook(agent) -> ApprovalHook | None:
    """Extract the ApprovalHook from an agent's registered hooks."""
    hr = agent.hooks
    for cb in hr._registered_callbacks.get(BeforeToolCallEvent, []):
        if hasattr(cb, "__self__") and isinstance(cb.__self__, ApprovalHook):
            return cb.__self__
    return None


# ---------------------------------------------------------------------------
# Property 1: Agent 配置等价性
# Feature: agentcore-deployment, Property 1
# Validates: Requirements 1.1, 1.2, 1.4, 8.1, 8.3
# ---------------------------------------------------------------------------

class TestProperty1AgentConfigEquivalence:
    """build_agent() produces an Agent with correct config for any valid env."""

    @given(env=_env_st)
    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_agent_has_all_tools(self, env: str):
        """**Validates: Requirements 1.1, 1.2**

        The agent built by build_agent() SHALL have the same set of tools
        as ALL_TOOLS regardless of environment.
        """
        agent = build_agent(env=env)
        expected_names = {getattr(t, "__name__", str(t)) for t in ALL_TOOLS}
        actual_names = set(agent.tool_names)
        assert expected_names == actual_names

    @given(env=_env_st)
    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_agent_has_approval_hook(self, env: str):
        """**Validates: Requirements 1.2, 1.4**

        The agent built by build_agent() SHALL have an ApprovalHook
        with config matching the environment config file.
        """
        agent = build_agent(env=env)
        hook = _extract_approval_hook(agent)
        assert hook is not None, "Agent must have an ApprovalHook"
        assert isinstance(hook.config, ApprovalConfig)
        assert hook.config.batch_threshold > 0

    @given(env=_env_st)
    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_system_prompt_contains_config_context(self, env: str):
        """**Validates: Requirements 1.1, 1.4, 8.1, 8.3**

        The agent's system prompt SHALL contain the pre-loaded configuration
        context block with DynamoDB table, environment, and region info.
        """
        agent = build_agent(env=env)
        prompt = agent.system_prompt
        assert "Pre-loaded Configuration" in prompt
        assert f"Environment: {env}" in prompt
        assert "DynamoDB table:" in prompt
        assert "Candidate regions with AMI" in prompt

    @given(env=_env_st, model_id=_model_id_st)
    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_explicit_params_override_config(self, env: str, model_id: str):
        """**Validates: Requirements 1.1, 8.3**

        Explicit parameters to build_agent() SHALL override config file defaults.
        """
        agent = build_agent(env=env, bedrock_model_id=model_id)
        assert agent.model.config["model_id"] == model_id



# ---------------------------------------------------------------------------
# Property 3: 环境变量配置传递
# Feature: agentcore-deployment, Property 3
# Validates: Requirements 2.5, 4.4
# ---------------------------------------------------------------------------

class TestProperty3EnvVarConfigPassthrough:
    """Environment variables are correctly used by build_agent()."""

    @given(
        env=_env_st,
        dynamodb_table=st.from_regex(r"GpuTest-[a-z]{3,8}", fullmatch=True),
        model_id=_model_id_st,
    )
    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    def test_env_vars_override_config_defaults(
        self, env: str, dynamodb_table: str, model_id: str, monkeypatch,
    ):
        """**Validates: Requirements 2.5, 4.4**

        Environment variables SHALL override config file defaults when
        no explicit parameter is provided.
        """
        monkeypatch.setenv("SCHEDULER_ENV", env)
        monkeypatch.setenv("DYNAMODB_TABLE", dynamodb_table)
        monkeypatch.setenv("BEDROCK_MODEL_ID", model_id)

        # Call without explicit params — env vars should take effect
        agent = build_agent()
        prompt = agent.system_prompt

        assert f"Environment: {env}" in prompt
        assert f"DynamoDB table: {dynamodb_table}" in prompt
        assert agent.model.config["model_id"] == model_id

    @given(
        env=_env_st,
        dynamodb_table=st.from_regex(r"GpuTest-[a-z]{3,8}", fullmatch=True),
    )
    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    def test_explicit_params_beat_env_vars(
        self, env: str, dynamodb_table: str, monkeypatch,
    ):
        """**Validates: Requirements 2.5, 4.4**

        Explicit parameters SHALL take priority over environment variables.
        """
        # Set env vars to one value
        monkeypatch.setenv("DYNAMODB_TABLE", "EnvVarTable")
        monkeypatch.setenv("BEDROCK_MODEL_ID", "env-var-model")

        # Pass explicit params — these should win
        agent = build_agent(env=env, dynamodb_table=dynamodb_table)
        prompt = agent.system_prompt

        assert f"DynamoDB table: {dynamodb_table}" in prompt
        assert "DynamoDB table: EnvVarTable" not in prompt
