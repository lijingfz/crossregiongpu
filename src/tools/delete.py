"""Tool: ec2_delete_instances – Terminate EC2 instances with human confirmation.

Uses Strands tool_context interrupt mechanism to pause execution and
request human approval before terminating instances. Supports batched
TerminateInstances calls (max 1000 per batch) and partial failure handling.
After successful termination, updates DynamoDB records with terminated status.

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

import boto3
from botocore.exceptions import ClientError
from strands import tool
from strands.types.tools import ToolContext

from src.models.schemas import DeleteResult, TerminatedInstance

logger = logging.getLogger(__name__)


def _build_confirmation_prompt(instances: List[dict]) -> str:
    """Build a human-readable confirmation prompt from instance details."""
    lines = [f"即将终止以下 {len(instances)} 个实例：\n"]
    for inst in instances:
        lines.append(
            f"  - {inst['InstanceId']}  "
            f"type={inst.get('InstanceType', 'N/A')}  "
            f"ip={inst.get('PrivateIpAddress', 'N/A')}  "
            f"subnet={inst.get('SubnetId', 'N/A')}  "
            f"az={inst.get('Placement', {}).get('AvailabilityZone', 'N/A')}"
        )
    lines.append("\n请确认是否继续终止？(y/N)")
    return "\n".join(lines)


def _terminate_batch(client, instance_ids: List[str]) -> tuple[List[TerminatedInstance], List[str]]:
    """Terminate a single batch of instances. Returns (terminated, errors)."""
    terminated: List[TerminatedInstance] = []
    errors: List[str] = []
    try:
        resp = client.terminate_instances(InstanceIds=instance_ids)
        for change in resp.get("TerminatingInstances", []):
            terminated.append(
                TerminatedInstance(
                    instance_id=change["InstanceId"],
                    current_state=change.get("CurrentState", {}).get("Name", "unknown"),
                )
            )
    except ClientError as exc:
        errors.append(f"TerminateInstances failed for {instance_ids}: {exc}")
    return terminated, errors


def _update_ddb_status(
    table_name: str,
    region: str,
    instance_ids: List[str],
    dynamodb_region: str,
) -> List[str]:
    """Update DynamoDB records to mark instances as terminated.

    Scans for matching region#instance_id sort keys and updates their status.
    Returns a list of error messages (empty on full success).
    """
    errors: List[str] = []
    ddb = boto3.resource("dynamodb", region_name=dynamodb_region)
    table = ddb.Table(table_name)
    now = datetime.now(timezone.utc).isoformat()

    for instance_id in instance_ids:
        sk_value = f"{region}#{instance_id}"
        try:
            # Scan for items with this sort key (could belong to any request_id)
            resp = table.scan(
                FilterExpression="region_instance_id = :sk",
                ExpressionAttributeValues={":sk": sk_value},
                ProjectionExpression="request_id, region_instance_id",
            )
            for item in resp.get("Items", []):
                table.update_item(
                    Key={
                        "request_id": item["request_id"],
                        "region_instance_id": item["region_instance_id"],
                    },
                    UpdateExpression="SET #s = :status, terminated_at = :ts",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={
                        ":status": "terminated",
                        ":ts": now,
                    },
                )
        except Exception as exc:
            errors.append(f"DynamoDB update failed for {instance_id}: {exc}")
            logger.warning("DynamoDB update failed for %s: %s", instance_id, exc)

    return errors


@tool(context=True)
def ec2_delete_instances(
    tool_context: ToolContext,
    region: str,
    instance_ids: List[str],
    dynamodb_table: Optional[str] = None,
    dynamodb_region: Optional[str] = None,
) -> dict:
    """终止指定实例。通过 Strands Interrupt 机制在执行前请求人工确认。

    Looks up instance details via DescribeInstances, presents a confirmation
    prompt through the Strands interrupt system, and terminates instances
    only after the user confirms. After successful termination, updates
    DynamoDB records with terminated status if dynamodb_table is provided.
    """
    client = boto3.client("ec2", region_name=region)

    # Describe instances to get details for confirmation prompt
    try:
        resp = client.describe_instances(InstanceIds=instance_ids)
    except ClientError as exc:
        return DeleteResult(
            deleted_count=0,
            terminated_instances=[],
            errors=[f"DescribeInstances failed: {exc}"],
        ).model_dump()

    instances_detail: List[dict] = []
    for reservation in resp.get("Reservations", []):
        for inst in reservation.get("Instances", []):
            instances_detail.append(inst)

    if not instances_detail:
        return DeleteResult(
            deleted_count=0,
            terminated_instances=[],
            errors=["未找到匹配的实例。"],
        ).model_dump()

    # Build confirmation prompt and interrupt for human approval
    prompt = _build_confirmation_prompt(instances_detail)
    approval = tool_context.interrupt(
        "ec2-delete-approval",
        reason={"prompt": prompt, "instance_ids": instance_ids, "region": region},
    )

    # Check user response
    if str(approval).strip().lower() not in ("y", "yes"):
        return DeleteResult(
            deleted_count=0,
            terminated_instances=[],
            errors=["操作已取消。"],
        ).model_dump()

    # Execute termination in batches of 1000
    all_terminated: List[TerminatedInstance] = []
    all_errors: List[str] = []
    batch_size = 1000

    for i in range(0, len(instance_ids), batch_size):
        batch = instance_ids[i : i + batch_size]
        terminated, errors = _terminate_batch(client, batch)
        all_terminated.extend(terminated)
        all_errors.extend(errors)

    # Update DynamoDB records with terminated status
    if all_terminated and dynamodb_table:
        terminated_ids = [t.instance_id for t in all_terminated]
        ddb_errors = _update_ddb_status(
            table_name=dynamodb_table,
            region=region,
            instance_ids=terminated_ids,
            dynamodb_region=dynamodb_region or "us-west-2",
        )
        all_errors.extend(ddb_errors)

    return DeleteResult(
        deleted_count=len(all_terminated),
        terminated_instances=all_terminated,
        errors=all_errors,
    ).model_dump()
