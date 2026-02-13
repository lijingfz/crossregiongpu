"""Tool: ec2_describe_instances – Enrich instance details.

Calls EC2 DescribeInstances to fill in IP addresses, AZ, and state
for a list of instance IDs.

Requirements: 4.1
"""

from __future__ import annotations

from typing import List

import boto3
from strands import tool

from src.models.schemas import InstanceInfo


@tool
def ec2_describe_instances(region: str, instance_ids: List[str]) -> list:
    """Describe instances in *region* and return enriched InstanceInfo dicts.

    Useful after RunInstances to fill in private_ip, public_ip, az, and
    current state that may not be immediately available at launch time.
    """
    if not instance_ids:
        return []

    client = boto3.client("ec2", region_name=region)

    # DescribeInstances accepts up to 1000 IDs per call
    all_infos: List[dict] = []
    for i in range(0, len(instance_ids), 200):
        chunk = instance_ids[i : i + 200]
        response = client.describe_instances(InstanceIds=chunk)
        for reservation in response.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                info = InstanceInfo(
                    instance_id=inst["InstanceId"],
                    instance_type=inst.get("InstanceType", ""),
                    az=inst.get("Placement", {}).get("AvailabilityZone", ""),
                    private_ip=inst.get("PrivateIpAddress", ""),
                    public_ip=inst.get("PublicIpAddress"),
                )
                all_infos.append(info.model_dump())

    return all_infos
