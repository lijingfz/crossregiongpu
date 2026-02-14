"""Tool: dynamodb_query_instances – Query GPU instance history from DynamoDB.

Scans the DynamoDB table with optional filters (region, instance_type, status)
to retrieve historical instance provisioning records.

Requirements: 5.2
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

import boto3
from strands import tool


def _enrich_duration(items: List[Dict]) -> List[Dict]:
    """Add duration_seconds and duration_human to items that have both launched_at and terminated_at."""
    for item in items:
        launched_at = item.get("launched_at")
        terminated_at = item.get("terminated_at")
        if launched_at and terminated_at:
            try:
                t_launch = datetime.fromisoformat(launched_at)
                t_term = datetime.fromisoformat(terminated_at)
                delta = t_term - t_launch
                seconds = int(delta.total_seconds())
                item["duration_seconds"] = seconds
                # Human-readable format
                if seconds < 60:
                    item["duration_human"] = f"{seconds}秒"
                elif seconds < 3600:
                    item["duration_human"] = f"{seconds // 60}分{seconds % 60}秒"
                else:
                    h = seconds // 3600
                    m = (seconds % 3600) // 60
                    item["duration_human"] = f"{h}小时{m}分"
            except (ValueError, TypeError):
                pass
        elif launched_at and item.get("status") == "launched":
            try:
                t_launch = datetime.fromisoformat(launched_at)
                now = datetime.now(timezone.utc)
                delta = now - t_launch
                seconds = int(delta.total_seconds())
                item["duration_seconds"] = seconds
                if seconds < 60:
                    item["duration_human"] = f"{seconds}秒 (仍在运行)"
                elif seconds < 3600:
                    item["duration_human"] = f"{seconds // 60}分{seconds % 60}秒 (仍在运行)"
                else:
                    h = seconds // 3600
                    m = (seconds % 3600) // 60
                    item["duration_human"] = f"{h}小时{m}分 (仍在运行)"
            except (ValueError, TypeError):
                pass
    return items


@tool
def dynamodb_query_instances(
    table: str,
    dynamodb_region: str = "us-west-2",
    region: Optional[str] = None,
    instance_type: Optional[str] = None,
    status: Optional[str] = None,
    request_id: Optional[str] = None,
) -> dict:
    """查询 DynamoDB 中的 GPU 实例历史记录。

    Supports filtering by region, instance_type, status, and request_id.
    When request_id is provided, uses efficient Query; otherwise uses Scan.

    IMPORTANT – status values in DynamoDB are NOT the same as EC2 instance
    states. Valid DynamoDB status values:
      - "launched"   : instance successfully launched and recorded
      - "terminated" : instance has been terminated via this system

    To find currently running instances, query with status="launched"
    (these are instances that have been launched but not yet terminated).
    Do NOT use EC2 state names like "running" or "stopped".

    To find all instances regardless of status, omit the status parameter.

    Returns {"instances": [...], "total_count": int, "message": str}.
    """
    ddb = boto3.resource("dynamodb", region_name=dynamodb_region)
    tbl = ddb.Table(table)

    # If request_id is given, use Query (efficient)
    if request_id:
        resp = tbl.query(
            KeyConditionExpression="request_id = :rid",
            ExpressionAttributeValues={":rid": request_id},
        )
        items = resp.get("Items", [])
    else:
        # Build filter expression for Scan
        filters = []
        expr_values: Dict[str, str] = {}

        if region:
            filters.append("region = :region")
            expr_values[":region"] = region
        if instance_type:
            filters.append("instance_type = :itype")
            expr_values[":itype"] = instance_type
        if status:
            # Backward compatibility: "launched" also matches legacy "pending"
            if status == "launched":
                filters.append("(#s = :status OR #s = :status_legacy)")
                expr_values[":status"] = "launched"
                expr_values[":status_legacy"] = "pending"
            else:
                filters.append("#s = :status")
                expr_values[":status"] = status

        scan_kwargs: dict = {}
        if filters:
            scan_kwargs["FilterExpression"] = " AND ".join(filters)
            scan_kwargs["ExpressionAttributeValues"] = expr_values
            if status:
                scan_kwargs["ExpressionAttributeNames"] = {"#s": "status"}

        items = []
        resp = tbl.scan(**scan_kwargs)
        items.extend(resp.get("Items", []))
        # Handle pagination
        while "LastEvaluatedKey" in resp:
            scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
            resp = tbl.scan(**scan_kwargs)
            items.extend(resp.get("Items", []))

    # Enrich items with computed duration
    items = _enrich_duration(items)

    total = len(items)
    if total == 0:
        message = "未找到匹配的历史记录。"
    else:
        message = f"找到 {total} 条历史记录。"

    return {
        "instances": items,
        "total_count": total,
        "message": message,
    }
