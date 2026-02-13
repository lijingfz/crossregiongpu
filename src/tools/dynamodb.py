"""Tool: dynamodb_put_instances – Persist launched instances to DynamoDB.

Uses BatchWriteItem to store instance records with:
  PK = request_id
  SK = region#instance_id

Requirements: 5.1, 5.2, 5.3
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

import boto3
from strands import tool


@tool
def dynamodb_put_instances(
    table: str,
    request_id: str,
    goal_region: str,
    region: str,
    instance_type: str,
    instances: List[Dict],
    step_id: str = "",
    allocation_status: str = "PARTIAL",
    dynamodb_region: str = "us-east-1",
) -> dict:
    """Write launched instance records to DynamoDB in batches.

    Each *instances* dict should contain at minimum:
      instance_id, az, private_ip, and optionally public_ip.

    Returns {"written": int, "errors": list}.
    """
    client = boto3.resource("dynamodb", region_name=dynamodb_region).Table(table)
    now = datetime.now(timezone.utc).isoformat()

    written = 0
    errors: List[str] = []

    # DynamoDB batch_writer handles chunking into 25-item batches
    with client.batch_writer() as batch:
        for inst in instances:
            item = {
                "request_id": request_id,
                "region_instance_id": f"{region}#{inst['instance_id']}",
                "goal_region": goal_region,
                "region": region,
                "instance_type": instance_type,
                "instance_id": inst["instance_id"],
                "az": inst.get("az", ""),
                "private_ip": inst.get("private_ip", ""),
                "public_ip": inst.get("public_ip", ""),
                "status": inst.get("status", "launched"),
                "launched_at": now,
                "step_id": step_id,
                "allocation_status": allocation_status,
            }
            try:
                batch.put_item(Item=item)
                written += 1
            except Exception as exc:
                errors.append(f"{inst.get('instance_id', '?')}: {exc}")

    return {"written": written, "errors": errors}
