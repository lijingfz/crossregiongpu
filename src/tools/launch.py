"""Tool: ec2_launch_instances – Probe-and-Fill chunked launcher.

Implements the core scheduling logic:
  - Chunked launch (split large requests into small batches)
  - Binary backoff (halve batch on InsufficientInstanceCapacity)
  - Multi-subnet round-robin rotation
  - Unique client_token per RunInstances call for idempotency

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7
"""

from __future__ import annotations

import math
import uuid
from typing import Dict, List, Optional

import boto3
from botocore.exceptions import ClientError
from strands import tool

from src.models.schemas import InstanceInfo, StepResult, validate_gpu_instance_type


def _generate_client_token(request_id: str, region: str, subnet: str, seq: int) -> str:
    """Generate a unique, deterministic-prefix client token for idempotency."""
    unique = uuid.uuid4().hex[:8]
    return f"{request_id}-{region}-{subnet[-6:]}-{seq}-{unique}"


def _run_instances(
    client,
    count: int,
    instance_type: str,
    subnet: str,
    ami: str,
    security_group_ids: List[str],
    iam_profile: str,
    tags: Dict[str, str],
    client_token: str,
    key_name: str = "",
) -> List[dict]:
    """Low-level RunInstances wrapper. Returns list of instance dicts."""
    tag_specs = [
        {
            "ResourceType": "instance",
            "Tags": [{"Key": k, "Value": v} for k, v in tags.items()],
        }
    ] if tags else []

    kwargs: dict = {
        "ImageId": ami,
        "InstanceType": instance_type,
        "MinCount": count,
        "MaxCount": count,
        "SubnetId": subnet,
        "SecurityGroupIds": security_group_ids,
        "ClientToken": client_token,
    }
    if key_name:
        kwargs["KeyName"] = key_name
    if iam_profile:
        kwargs["IamInstanceProfile"] = {"Name": iam_profile}
    if tag_specs:
        kwargs["TagSpecifications"] = tag_specs

    response = client.run_instances(**kwargs)
    return response.get("Instances", [])


# Hard ceiling on instances per single tool call.
# Prevents LLM hallucination from launching an unbounded number of instances
# when the Agent autonomously invokes this tool.
MAX_TARGET_COUNT = 20


@tool
def ec2_launch_instances(
    region: str,
    instance_type: str,
    target_count: int,
    subnets: List[str],
    ami: str,
    security_group_ids: List[str],
    iam_profile: str = "",
    tags: Optional[Dict[str, str]] = None,
    batch_max: int = 4,
    max_attempts_per_subnet: int = 3,
    request_id: str = "",
    key_name: str = "",
) -> dict:
    """Probe-and-Fill launcher for a single Region.

    Splits the request into small batches, rotates across subnets,
    and applies binary backoff on capacity errors.

    Returns a dict matching the StepResult schema.
    """
    if tags is None:
        tags = {}
    if not request_id:
        request_id = uuid.uuid4().hex[:12]

    # --- target_count guard: reject non-positive or excessively large values ---
    if target_count <= 0:
        return StepResult(
            status="ERROR",
            requested=target_count,
            launched=0,
            remaining=0,
            region=region,
            error_code="INVALID_TARGET_COUNT",
            message=f"target_count must be positive, got {target_count}",
        ).model_dump()

    if target_count > MAX_TARGET_COUNT:
        return StepResult(
            status="ERROR",
            requested=target_count,
            launched=0,
            remaining=target_count,
            region=region,
            error_code="TARGET_COUNT_EXCEEDED",
            message=(
                f"target_count={target_count} exceeds the per-call maximum "
                f"of {MAX_TARGET_COUNT}. Split into smaller requests or "
                f"adjust MAX_TARGET_COUNT if this is intentional."
            ),
        ).model_dump()

    # --- GPU-only guard: reject non-GPU instance types ---
    try:
        validate_gpu_instance_type(instance_type)
    except ValueError as exc:
        return StepResult(
            status="ERROR",
            requested=target_count,
            launched=0,
            remaining=target_count,
            region=region,
            error_code="INVALID_INSTANCE_TYPE",
            message=str(exc),
        ).model_dump()

    client = boto3.client("ec2", region_name=region)

    remaining = target_count
    launched_instances: List[InstanceInfo] = []
    call_seq = 0
    client_tokens: List[str] = []

    for subnet in subnets:
        if remaining <= 0:
            break

        batch = min(batch_max, remaining)
        attempts = 0

        while remaining > 0 and attempts < max_attempts_per_subnet:
            n = min(batch, remaining)
            token = _generate_client_token(request_id, region, subnet, call_seq)
            client_tokens.append(token)
            call_seq += 1

            try:
                instances = _run_instances(
                    client=client,
                    count=n,
                    instance_type=instance_type,
                    subnet=subnet,
                    ami=ami,
                    security_group_ids=security_group_ids,
                    iam_profile=iam_profile,
                    tags=tags,
                    client_token=token,
                    key_name=key_name,
                )
                for inst in instances:
                    launched_instances.append(
                        InstanceInfo(
                            instance_id=inst["InstanceId"],
                            instance_type=instance_type,
                            az=inst.get("Placement", {}).get("AvailabilityZone", ""),
                            private_ip=inst.get("PrivateIpAddress", ""),
                            public_ip=inst.get("PublicIpAddress"),
                        )
                    )
                remaining -= n
                # On success we can try the same or larger batch next iteration
                attempts = 0
                batch = min(batch_max, remaining) if remaining > 0 else 0

            except ClientError as exc:
                code = exc.response["Error"]["Code"]
                if code == "InsufficientInstanceCapacity":
                    if n > 1:
                        # Binary backoff: halve the batch
                        batch = math.ceil(n / 2)
                        attempts += 1
                        continue
                    else:
                        # batch=1 still fails → this subnet is exhausted
                        break
                else:
                    # Non-capacity error → return ERROR immediately
                    return StepResult(
                        status="ERROR",
                        requested=target_count,
                        launched=len(launched_instances),
                        remaining=remaining,
                        region=region,
                        instances=launched_instances,
                        error_code=code,
                        message=str(exc),
                    ).model_dump()

    # Determine final status
    total_launched = len(launched_instances)
    if total_launched == target_count:
        status = "FULL"
    elif total_launched > 0:
        status = "PARTIAL"
    else:
        status = "NONE"

    return StepResult(
        status=status,
        requested=target_count,
        launched=total_launched,
        remaining=remaining,
        region=region,
        instances=launched_instances,
        message=f"Launched {total_launched}/{target_count} in {region}",
    ).model_dump()
