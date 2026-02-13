"""Tool: ec2_query_instances – Flexible EC2 instance query.

Calls EC2 DescribeInstances with server-side Filters and optional
client-side post-filtering (e.g. private_ip). Supports multi-region
fallback when no region is specified.

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7
"""

from __future__ import annotations

from typing import Dict, List, Optional

import boto3
from strands import tool

from src.config.loader import ConfigLoader
from src.models.schemas import FilterSet, InstanceSummary


def _build_filters(fs: FilterSet) -> List[Dict]:
    """Build EC2 DescribeInstances Filters from a FilterSet."""
    filters: List[Dict] = []
    if fs.instance_type:
        filters.append({"Name": "instance-type", "Values": [fs.instance_type]})
    if fs.subnet_id:
        filters.append({"Name": "subnet-id", "Values": [fs.subnet_id]})
    if fs.state:
        filters.append({"Name": "instance-state-name", "Values": [fs.state]})
    if fs.tags:
        for key, value in fs.tags.items():
            filters.append({"Name": f"tag:{key}", "Values": [value]})
    return filters


def _query_region(
    region: str,
    filters: List[Dict],
    instance_ids: Optional[List[str]],
    private_ips: Optional[List[str]],
) -> List[InstanceSummary]:
    """Query a single region and return InstanceSummary list."""
    client = boto3.client("ec2", region_name=region)
    paginator = client.get_paginator("describe_instances")

    kwargs: dict = {}
    if filters:
        kwargs["Filters"] = filters
    if instance_ids:
        kwargs["InstanceIds"] = instance_ids

    summaries: List[InstanceSummary] = []
    for page in paginator.paginate(**kwargs):
        for reservation in page.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                private_ip = inst.get("PrivateIpAddress", "")
                # Client-side post-filter for private_ips
                if private_ips and private_ip not in private_ips:
                    continue
                launch_time = inst.get("LaunchTime")
                summaries.append(
                    InstanceSummary(
                        instance_id=inst["InstanceId"],
                        instance_type=inst.get("InstanceType", ""),
                        private_ip=private_ip,
                        public_ip=inst.get("PublicIpAddress"),
                        subnet_id=inst.get("SubnetId", ""),
                        az=inst.get("Placement", {}).get("AvailabilityZone", ""),
                        state=inst.get("State", {}).get("Name", ""),
                        launch_time=launch_time.isoformat() if launch_time else "",
                    )
                )
    return summaries


@tool
def ec2_query_instances(
    region: Optional[str] = None,
    instance_type: Optional[str] = None,
    subnet_id: Optional[str] = None,
    instance_ids: Optional[List[str]] = None,
    private_ips: Optional[List[str]] = None,
    state: str = "running",
    tags: Optional[Dict[str, str]] = None,
    config_path: Optional[str] = None,
) -> dict:
    """查询匹配过滤条件的 EC2 实例，返回实例摘要列表。

    Supports filtering by region, instance_type, subnet_id, instance_ids,
    private_ips, state, and tags. When region is not specified, queries
    all candidate regions from the configuration file.
    """
    fs = FilterSet(
        region=region,
        instance_type=instance_type,
        subnet_id=subnet_id,
        instance_ids=instance_ids,
        private_ips=private_ips,
        state=state,
        tags=tags,
    )

    if fs.is_empty():
        return {
            "instances": [],
            "total_count": 0,
            "message": "错误：所有过滤条件为空，请提供至少一个过滤条件（region、instance_type、subnet_id、instance_ids、private_ips 或 tags）。",
        }

    filters = _build_filters(fs)
    all_summaries: List[InstanceSummary] = []

    if region:
        all_summaries = _query_region(region, filters, fs.instance_ids, fs.private_ips)
    else:
        # No region specified – iterate all candidate regions from config
        path = config_path or "config/regions.yaml"
        loader = ConfigLoader.from_yaml(path)
        for rc in loader.regions:
            region_summaries = _query_region(
                rc.region, filters, fs.instance_ids, fs.private_ips
            )
            all_summaries.extend(region_summaries)

    instances = [s.model_dump() for s in all_summaries]
    total = len(instances)

    if total == 0:
        message = "未找到匹配的实例。"
    else:
        message = f"找到 {total} 个匹配实例。"

    return {
        "instances": instances,
        "total_count": total,
        "message": message,
    }
