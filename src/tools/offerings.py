"""Tool: describe_instance_type_offerings – Region support pre-check.

Calls EC2 DescribeInstanceTypeOfferings to verify whether a given Region
(and optionally AZ) supports the target instance type before attempting
to launch instances there.

Requirements: 2.1, 2.2, 2.3
"""

from __future__ import annotations

from typing import List, Optional

import boto3
from strands import tool


@tool
def describe_instance_type_offerings(
    region: str,
    instance_type: str,
    az: Optional[str] = None,
) -> dict:
    """Check whether *region* (and optionally a specific *az*) offers *instance_type*.

    Returns a dict with:
      - supported (bool): True if the instance type is available
      - offerings (list[str]): matching location names (Region or AZ)
    """
    client = boto3.client("ec2", region_name=region)

    filters = [{"Name": "instance-type", "Values": [instance_type]}]
    location_type = "availability-zone" if az else "region"

    paginator = client.get_paginator("describe_instance_type_offerings")
    pages = paginator.paginate(
        LocationType=location_type,
        Filters=filters,
    )

    locations: List[str] = []
    for page in pages:
        for offering in page.get("InstanceTypeOfferings", []):
            locations.append(offering["Location"])

    # If caller asked about a specific AZ, filter to that AZ
    if az:
        supported = az in locations
    else:
        supported = len(locations) > 0

    return {
        "supported": supported,
        "offerings": locations,
    }
