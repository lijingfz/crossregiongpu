"""Property-based test for termination operation completeness.

Property 5: 终止操作完整性
For any confirmed delete operation, TerminateInstances is called with all
specified instance_ids, and the returned DeleteResult contains a
terminated_instances list whose length equals the number of requested
instances, with each entry containing the corresponding instance_id and
current_state.

Feature: instance-query-and-deletion, Property 5
Validates: Requirements 2.4, 2.7
"""

from __future__ import annotations

from unittest.mock import MagicMock

import boto3
from hypothesis import given, settings
from hypothesis import strategies as st
from moto import mock_aws

from src.tools.delete import ec2_delete_instances


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool_context(approval: str = "y") -> MagicMock:
    ctx = MagicMock()
    ctx.interrupt.return_value = approval
    return ctx


def _create_instances(region: str, count: int):
    """Spin up *count* instances in moto and return their IDs."""
    ec2 = boto3.client("ec2", region_name=region)
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
    subnet = ec2.create_subnet(
        VpcId=vpc["Vpc"]["VpcId"],
        CidrBlock="10.0.1.0/24",
        AvailabilityZone=f"{region}a",
    )
    resp = ec2.run_instances(
        ImageId="ami-12345678",
        InstanceType="g5.xlarge",
        MinCount=count,
        MaxCount=count,
        SubnetId=subnet["Subnet"]["SubnetId"],
    )
    return [i["InstanceId"] for i in resp["Instances"]]


# ---------------------------------------------------------------------------
# Property 5: 终止操作完整性
# Feature: instance-query-and-deletion, Property 5
# Validates: Requirements 2.4, 2.7
# ---------------------------------------------------------------------------

class TestProperty5TerminateCompleteness:
    """After user confirmation every requested instance must appear in the
    DeleteResult with a valid current_state."""

    @mock_aws
    @given(count=st.integers(min_value=1, max_value=8))
    @settings(max_examples=100, deadline=None)
    def test_all_confirmed_instances_terminated(self, count: int):
        """**Validates: Requirements 2.4, 2.7**"""
        region = "us-east-1"
        ids = _create_instances(region, count)
        ctx = _make_tool_context("y")

        result = ec2_delete_instances(ctx, region=region, instance_ids=ids)

        # deleted_count equals the number of requested instances
        assert result["deleted_count"] == count, (
            f"Expected deleted_count={count}, got {result['deleted_count']}"
        )

        # terminated_instances list length matches
        assert len(result["terminated_instances"]) == count

        # Every requested instance_id appears in the result
        returned_ids = {t["instance_id"] for t in result["terminated_instances"]}
        assert returned_ids == set(ids), (
            f"Mismatch: requested={set(ids)}, returned={returned_ids}"
        )

        # Each entry has a non-empty current_state
        for t in result["terminated_instances"]:
            assert t["current_state"] in ("shutting-down", "terminated"), (
                f"Unexpected state: {t['current_state']}"
            )

        # No errors
        assert result["errors"] == []
