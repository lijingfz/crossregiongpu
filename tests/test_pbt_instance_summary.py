"""Property-based tests for InstanceSummary field completeness.

Property 3: InstanceSummary 字段完整性
For any InstanceSummary returned by ec2_query_instances, the required
fields (instance_id, instance_type, private_ip, subnet_id, az, state,
launch_time) are all non-empty strings.

Feature: instance-query-and-deletion, Property 3
Validates: Requirements 1.5
"""

from __future__ import annotations

import boto3
from hypothesis import given, settings, assume
from hypothesis import strategies as st
from moto import mock_aws

from src.tools.query import ec2_query_instances

# ---------------------------------------------------------------------------
# Constants & strategies
# ---------------------------------------------------------------------------

REGION = "us-east-1"
AZ = f"{REGION}a"

_instance_type_pool = ["g5.xlarge", "g5.2xlarge", "g6.xlarge", "t2.micro"]
_instance_type_st = st.sampled_from(_instance_type_pool)

REQUIRED_FIELDS = [
    "instance_id",
    "instance_type",
    "private_ip",
    "subnet_id",
    "az",
    "state",
    "launch_time",
]


@st.composite
def instance_scenario(draw):
    """Generate a scenario with random instance types and counts."""
    n_groups = draw(st.integers(min_value=1, max_value=3))
    groups = []
    for _ in range(n_groups):
        itype = draw(_instance_type_st)
        count = draw(st.integers(min_value=1, max_value=3))
        groups.append({"instance_type": itype, "count": count})
    # Pick one type to query (or None to query all via subnet)
    query_by_type = draw(st.booleans())
    target_type = draw(_instance_type_st) if query_by_type else None
    return {"groups": groups, "target_type": target_type}


# ---------------------------------------------------------------------------
# Property 3: InstanceSummary 字段完整性
# Feature: instance-query-and-deletion, Property 3
# Validates: Requirements 1.5
# ---------------------------------------------------------------------------


class TestProperty3InstanceSummaryCompleteness:
    """Every InstanceSummary returned has all required fields as non-empty strings."""

    @settings(max_examples=100, deadline=None)
    @given(scenario=instance_scenario())
    @mock_aws
    def test_required_fields_are_non_empty(self, scenario):
        """**Validates: Requirements 1.5**

        For any randomly generated set of EC2 instances, every
        InstanceSummary returned by ec2_query_instances must have
        instance_id, instance_type, private_ip, subnet_id, az, state,
        and launch_time as non-empty strings.
        """
        ec2 = boto3.client("ec2", region_name=REGION)

        # Create VPC + subnet
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        subnet = ec2.create_subnet(
            VpcId=vpc["Vpc"]["VpcId"],
            CidrBlock="10.0.1.0/24",
            AvailabilityZone=AZ,
        )
        subnet_id = subnet["Subnet"]["SubnetId"]

        # Launch instance groups
        for group in scenario["groups"]:
            ec2.run_instances(
                ImageId="ami-12345678",
                InstanceType=group["instance_type"],
                MinCount=group["count"],
                MaxCount=group["count"],
                SubnetId=subnet_id,
            )

        # Query
        query_kwargs = {"region": REGION, "state": "running"}
        if scenario["target_type"]:
            query_kwargs["instance_type"] = scenario["target_type"]
        else:
            query_kwargs["subnet_id"] = subnet_id

        result = ec2_query_instances(**query_kwargs)
        assume(result["total_count"] > 0)

        for inst in result["instances"]:
            for field in REQUIRED_FIELDS:
                value = inst.get(field)
                assert isinstance(value, str), (
                    f"Field '{field}' on instance {inst.get('instance_id')} "
                    f"is {type(value).__name__}, expected str"
                )
                assert value != "", (
                    f"Field '{field}' on instance {inst.get('instance_id')} "
                    f"is empty"
                )
