"""Controller Agent: configuration, initialization, and high-level API.

Creates a Strands Agent backed by Bedrock Claude, registers all @tool
functions, and exposes helpers for Plan generation and NextAction decisions
using structured output (Pydantic schemas).

Requirements: 1.1, 6.1
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from strands import Agent
from strands.models.bedrock import BedrockModel

from src.agent.prompts import (
    NEXT_ACTION_PROMPT_TEMPLATE,
    PLAN_PROMPT_TEMPLATE,
    SYSTEM_PROMPT,
)
from src.models.schemas import NextAction, Plan, StepResult
from src.tools import (
    describe_instance_type_offerings,
    dynamodb_put_instances,
    dynamodb_query_instances,
    ec2_delete_instances,
    ec2_describe_instances,
    ec2_launch_instances,
    finalize,
    get_region_order,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"
DEFAULT_REGION = "us-west-2"
DEFAULT_MAX_TOKENS = 4096

# All tools the agent can invoke
ALL_TOOLS = [
    describe_instance_type_offerings,
    ec2_launch_instances,
    ec2_describe_instances,
    ec2_delete_instances,
    dynamodb_put_instances,
    dynamodb_query_instances,
    finalize,
    get_region_order,
]


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def create_agent(
    *,
    model_id: str = DEFAULT_MODEL_ID,
    region_name: str = DEFAULT_REGION,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    tools: Optional[list] = None,
    system_prompt: str = SYSTEM_PROMPT,
    state: Optional[dict] = None,
    hooks: Optional[list] = None,
) -> Agent:
    """Create and return a configured Controller Agent."""
    model = BedrockModel(
        model_id=model_id,
        region_name=region_name,
        max_tokens=max_tokens,
    )

    agent = Agent(
        model=model,
        tools=tools if tools is not None else ALL_TOOLS,
        system_prompt=system_prompt,
        state=state,
        hooks=hooks,
    )

    return agent


# ---------------------------------------------------------------------------
# Structured output helpers
# ---------------------------------------------------------------------------

def generate_plan(
    agent: Agent,
    *,
    goal: str,
    instance_type: str,
    target_count: int,
    consumer_region: str,
    region_mode: str = "multi_region",
    regions: Optional[List[str]] = None,
    environment: str = "",
    iam_constraints: str = "",
    budget_time: str = "",
    forbidden_actions: str = "",
) -> Plan:
    """Ask the agent to produce a structured Plan via structured_output."""
    prompt = PLAN_PROMPT_TEMPLATE.format(
        goal=goal,
        instance_type=instance_type,
        target_count=target_count,
        consumer_region=consumer_region,
        region_mode=region_mode,
        regions=", ".join(regions) if regions else consumer_region,
        environment=environment or "default",
        iam_constraints=iam_constraints or "none",
        budget_time=budget_time or "no limit",
        forbidden_actions=forbidden_actions or "none",
    )

    result = agent.structured_output(Plan, prompt)
    return result


def decide_next_action(
    agent: Agent,
    *,
    goal: str,
    plan_summary: str,
    region_mode: str,
    step_id: str,
    step_title: str,
    step_result: StepResult,
    total_launched: int,
) -> NextAction:
    """Ask the agent to decide the next action after a step execution."""
    prompt = NEXT_ACTION_PROMPT_TEMPLATE.format(
        goal=goal,
        plan_summary=plan_summary,
        region_mode=region_mode,
        step_id=step_id,
        step_title=step_title,
        status=step_result.status,
        launched=step_result.launched,
        remaining=step_result.remaining,
        region=step_result.region,
        error_code=step_result.error_code or "none",
        message=step_result.message,
        total_launched=total_launched,
    )

    result = agent.structured_output(NextAction, prompt)
    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Interactive CLI entry point for the GPU scheduling agent."""
    import sys

    import yaml

    from src.agent.approval import ApprovalConfig, ApprovalHook
    from src.config.loader import ConfigLoader

    # --- Load environment config ---
    env = sys.argv[1] if len(sys.argv) > 1 else "dev"
    env_config_path = f"config/environments/{env}.yaml"

    try:
        with open(env_config_path, "r", encoding="utf-8") as f:
            env_cfg = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"[error] Environment config not found: {env_config_path}")
        sys.exit(1)

    print(f"[gpu-scheduler] Environment: {env}")
    print(f"[gpu-scheduler] Model: {env_cfg.get('bedrock_model_id', DEFAULT_MODEL_ID)}")
    print(f"[gpu-scheduler] Bedrock region: {env_cfg.get('bedrock_region', DEFAULT_REGION)}")

    # --- Load region config from SSM or local fallback ---
    ssm_param = env_cfg.get("ssm_parameter", f"/gpu-scheduler/{env}/regions")
    try:
        loader = ConfigLoader.from_ssm(ssm_param)
        print(f"[gpu-scheduler] Regions loaded from SSM: {ssm_param}")
    except Exception:
        loader = ConfigLoader.from_yaml("config/regions.yaml")
        print("[gpu-scheduler] Regions loaded from local YAML (SSM unavailable)")

    regions = loader.regions
    print(f"[gpu-scheduler] Candidate regions: {[r.region for r in regions]}")

    # --- Build context block for system prompt ---
    ddb_table = env_cfg.get("dynamodb_table", f"GpuProvisioningInstances-{env}")
    ddb_region = env_cfg.get("bedrock_region", DEFAULT_REGION)
    batch_max = env_cfg.get("batch_max", 4)

    region_lines = []
    for r in regions:
        subnets = []
        for az in r.azs:
            for s in az.subnets:
                subnets.append(f"{az.az_name}:{s}")
        region_lines.append(
            f"  - {r.region} (priority={r.priority}, ami={r.ami_id}, "
            f"key={r.key_name}, subnets=[{', '.join(subnets)}])"
        )

    config_context = (
        "\n\n## Pre-loaded Configuration (use these values directly, "
        "do NOT ask the user)\n\n"
        f"DynamoDB table: {ddb_table}\n"
        f"DynamoDB region: {ddb_region}\n"
        f"Batch max: {batch_max}\n"
        f"Environment: {env}\n"
        "Candidate regions with AMI, Key, and Subnets:\n"
        + "\n".join(region_lines)
    )

    # Add fallback group info to context
    if loader.fallback_groups:
        group_lines = ["\n\nGeographic Compliance Fallback Groups:"]
        for group in loader.fallback_groups:
            group_lines.append(
                f"  - Consumer regions {group.consumer_regions} "
                f"→ allowed fallback: {group.fallback_regions}"
            )
        group_lines.append(
            f"  - Allowed consumer regions: {sorted(loader.all_consumer_regions)}"
        )
        group_lines.append(
            "  - Requests from regions NOT in any group above MUST be rejected."
        )
        config_context += "\n".join(group_lines)

    config_context += (
        "\n\nWhen launching instances, automatically use the ami_id, key_name, "
        "and subnets from the region config above. Generate a unique request_id "
        "for each scheduling run. Use security_group_ids=[] (empty) unless the "
        "user specifies otherwise.\n"
        "When querying GPU history, use dynamodb_query_instances to check DynamoDB records.\n"
        "When deleting instances, always pass dynamodb_table and dynamodb_region "
        "to ec2_delete_instances so DynamoDB records are updated to 'terminated'."
    )

    full_system_prompt = SYSTEM_PROMPT + config_context

    # --- Build approval hook ---
    approval_cfg = env_cfg.get("approval", {})
    approval_hook = ApprovalHook(
        config=ApprovalConfig(
            batch_threshold=approval_cfg.get("batch_threshold", 20),
            allowed_geo_regions=set(approval_cfg.get("allowed_geo_regions", [])),
        )
    )

    # --- Create agent ---
    agent = create_agent(
        model_id=env_cfg.get("bedrock_model_id", DEFAULT_MODEL_ID),
        region_name=env_cfg.get("bedrock_region", DEFAULT_REGION),
        max_tokens=env_cfg.get("max_tokens", DEFAULT_MAX_TOKENS),
        hooks=[approval_hook],
        system_prompt=full_system_prompt,
    )

    print("[gpu-scheduler] Agent ready. Type your scheduling request (Ctrl+C to exit).")
    print()

    # --- Helper for robust input reading ---
    def safe_input(prompt: str = "") -> str:
        """Read input with robust encoding handling."""
        if prompt:
            sys.stdout.write(prompt)
            sys.stdout.flush()
        try:
            # Try reading with explicit UTF-8 handling
            if hasattr(sys.stdin, "buffer"):
                line = sys.stdin.buffer.readline()
                return line.decode("utf-8", errors="replace").strip()
            else:
                return input().strip()
        except UnicodeDecodeError:
            # Fallback: read raw bytes and decode with error handling
            if hasattr(sys.stdin, "buffer"):
                line = sys.stdin.buffer.readline()
                return line.decode("utf-8", errors="replace").strip()
            return ""

    # --- Interactive loop ---
    try:
        while True:
            try:
                user_input = safe_input(">>> ")
            except EOFError:
                break

            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "q"):
                break

            result = agent(user_input)

            # Handle interrupt (human-in-the-loop approval)
            while getattr(result, "stop_reason", None) == "interrupt":
                for interrupt in result.interrupts:
                    reason = interrupt.reason or {}
                    prompt_text = reason.get("prompt", "") if isinstance(reason, dict) else str(reason)
                    if prompt_text:
                        print(prompt_text)

                try:
                    approval = safe_input()
                except EOFError:
                    approval = "n"

                responses = [
                    {
                        "interruptResponse": {
                            "interruptId": interrupt.id,
                            "response": approval,
                        }
                    }
                    for interrupt in result.interrupts
                ]
                result = agent(responses)

            print(result)
            print()
    except KeyboardInterrupt:
        print("\n[gpu-scheduler] Bye.")


if __name__ == "__main__":
    main()
