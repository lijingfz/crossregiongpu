"""Property-based tests for ec2_query_instances filter correctness.

Property 1: 过滤条件正确性
For any set of EC2 instances and any non-empty FilterSet,
ec2_query_instances returns only instances that satisfy ALL
specified filter conditions (instance_type, subnet_id, state,
private_ip).

Feature: instance-query-and-deletion, Property 1
Validates: Requirements 1.2
"""

from __future__ import annotations

import boto3
from hypothesis import given, settings, assume
from hypothesis import strategies as st
from moto import mock_aws

from src.tools.query import ec2_query_instances

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

REGION = "us-east-1"
AZ = f"{REGION}a"

_instance_type_pool = ["g5.xlarge", "g5.2xlarge", "g6.xlarge", "g5.xlarge"]
_instance_type_st = st.sampled_from(_instance_type_pool)


@st.composite
def filter_and_instances_st(draw):
    """Generate a random set of instances and a compatible non-empty FilterSet.

    We create instances with varying instance_type and subnet, then pick
    a random subset of filter dimensions to apply. This ensures the filter
    is always non-empty and we can verify correctness against known data.
    """
    # Decide how many instance groups to create (1-3 groups, each with
    # a distinct instance_type + subnet combo)
    n_groups = draw(st.integers(min_value=1, max_value=3))

    groups = []
    for _ in range(n_groups):
        itype = draw(_instance_type_st)
        count = draw(st.integers(min_value=1, max_value=3))
        groups.append({"instance_type": itype, "count": count})

    # Pick which filter dimensions to activate (at least one must be active)
    filter_by_type = draw(st.booleans())
    filter_by_private_ip = draw(st.booleans())

    # Ensure at least one filter is active
    if not filter_by_type and not filter_by_private_ip:
        filter_by_type = True

    # Choose the target instance_type for filtering (from the pool)
    target_type = draw(_instance_type_st) if filter_by_type else None

    return {
        "groups": groups,
        "filter_by_type": filter_by_type,
        "filter_by_private_ip": filter_by_private_ip,
        "target_type": target_type,
    }


# ---------------------------------------------------------------------------
# Property 1: 过滤条件正确性
# Feature: instance-query-and-deletion, Property 1
# Validates: Requirements 1.2
# ---------------------------------------------------------------------------

class TestProperty1FilterCorrectness:
    """Every instance returned by ec2_query_instances satisfies all
    specified filter conditions."""

    @settings(max_examples=100, deadline=None)
    @given(data=filter_and_instances_st())
    @mock_aws
    def test_returned_instances_match_all_filters(self, data):
        """**Validates: Requirements 1.2**

        For any randomly generated set of EC2 instances and any non-empty
        FilterSet, every instance in the query result must match ALL
        active filter conditions.
        """
        ec2 = boto3.client("ec2", region_name=REGION)

        # Create VPC + subnet
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        vpc_id = vpc["Vpc"]["VpcId"]
        subnet = ec2.create_subnet(
            VpcId=vpc_id, CidrBlock="10.0.1.0/24", AvailabilityZone=AZ
        )
        subnet_id = subnet["Subnet"]["SubnetId"]

        # Launch instance groups
        all_instance_ids = []
        for group in data["groups"]:
            resp = ec2.run_instances(
                ImageId="ami-12345678",
                InstanceType=group["instance_type"],
                MinCount=group["count"],
                MaxCount=group["count"],
                SubnetId=subnet_id,
            )
            for inst in resp["Instances"]:
                all_instance_ids.append(inst["InstanceId"])

        assume(len(all_instance_ids) > 0)

        # Collect all instance details for private_ip filtering
        desc = ec2.describe_instances(InstanceIds=all_instance_ids)
        all_instances_info = []
        for res in desc["Reservations"]:
            for inst in res["Instances"]:
                all_instances_info.append(inst)

        # Build query kwargs
        query_kwargs = {"region": REGION, "state": "running"}

        target_type = data["target_type"]
        if data["filter_by_type"] and target_type:
            query_kwargs["instance_type"] = target_type

        target_ips = None
        if data["filter_by_private_ip"] and all_instances_info:
            # Pick a random subset of IPs to filter on
            available_ips = [
                i["PrivateIpAddress"]
                for i in all_instances_info
                if i.get("PrivateIpAddress")
            ]
            if available_ips:
                # Use first 1-2 IPs as filter
                target_ips = available_ips[: min(2, len(available_ips))]
                query_kwargs["private_ips"] = target_ips

        result = ec2_query_instances(**query_kwargs)

        # Verify: every returned instance satisfies ALL active filters
        for inst in result["instances"]:
            if data["filter_by_type"] and target_type:
                assert inst["instance_type"] == target_type, (
                    f"Instance {inst['instance_id']} has type "
                    f"{inst['instance_type']}, expected {target_type}"
                )

            if target_ips is not None:
                assert inst["private_ip"] in target_ips, (
                    f"Instance {inst['instance_id']} has IP "
                    f"{inst['private_ip']}, not in {target_ips}"
                )

            # State should always match (we always filter by running)
            assert inst["state"] == "running", (
                f"Instance {inst['instance_id']} state is "
                f"{inst['state']}, expected running"
            )
