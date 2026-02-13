"""Tests for src/tools/delete.py using moto.

Requirements: 2.4, 2.6, 2.7, 2.8
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import boto3
from botocore.exceptions import ClientError
from moto import mock_aws

from src.tools.delete import ec2_delete_instances, _build_confirmation_prompt


def _setup_instances(region: str, count: int = 2):
    """Create EC2 instances in moto and return (instance_ids, client)."""
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
    ids = [i["InstanceId"] for i in resp["Instances"]]
    return ids, ec2


def _make_tool_context(approval_response: str = "y") -> MagicMock:
    """Create a mock ToolContext whose interrupt() returns the given response."""
    ctx = MagicMock()
    ctx.interrupt.return_value = approval_response
    return ctx


# ---------------------------------------------------------------
# Test: successful termination after confirmation  (Req 2.4)
# ---------------------------------------------------------------

@mock_aws
def test_delete_confirmed_terminates_instances():
    region = "us-east-1"
    ids, ec2 = _setup_instances(region, 2)
    ctx = _make_tool_context("y")

    result = ec2_delete_instances(ctx, region=region, instance_ids=ids)

    assert result["deleted_count"] == 2
    assert len(result["terminated_instances"]) == 2
    returned_ids = {t["instance_id"] for t in result["terminated_instances"]}
    assert returned_ids == set(ids)
    for t in result["terminated_instances"]:
        assert t["current_state"] in ("shutting-down", "terminated")
    assert result["errors"] == []


# ---------------------------------------------------------------
# Test: user rejects confirmation  (Req 2.5)
# ---------------------------------------------------------------

@mock_aws
def test_delete_rejected_cancels_operation():
    region = "us-east-1"
    ids, ec2 = _setup_instances(region, 1)
    ctx = _make_tool_context("no")

    result = ec2_delete_instances(ctx, region=region, instance_ids=ids)

    assert result["deleted_count"] == 0
    assert result["terminated_instances"] == []
    assert any("取消" in e for e in result["errors"])

    # Verify instances are still running
    desc = ec2.describe_instances(InstanceIds=ids)
    state = desc["Reservations"][0]["Instances"][0]["State"]["Name"]
    assert state == "running"


# ---------------------------------------------------------------
# Test: empty / non-existent instances  (Req 2.6)
# ---------------------------------------------------------------

@mock_aws
def test_delete_nonexistent_instances_returns_error():
    region = "us-east-1"
    # Need at least a VPC for the region to exist in moto
    boto3.client("ec2", region_name=region).create_vpc(CidrBlock="10.0.0.0/16")
    ctx = _make_tool_context("y")

    result = ec2_delete_instances(
        ctx, region=region, instance_ids=["i-nonexistent123"]
    )

    assert result["deleted_count"] == 0
    assert any("DescribeInstances failed" in e or "未找到" in e for e in result["errors"])


# ---------------------------------------------------------------
# Test: API error during termination  (Req 2.8)
# ---------------------------------------------------------------

@mock_aws
def test_delete_api_error_returns_error_info():
    region = "us-east-1"
    ids, _ = _setup_instances(region, 1)
    ctx = _make_tool_context("y")

    with patch("src.tools.delete.boto3.client") as mock_boto:
        mock_ec2 = MagicMock()
        mock_boto.return_value = mock_ec2

        # describe_instances succeeds
        mock_ec2.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": ids[0],
                            "InstanceType": "g5.xlarge",
                            "PrivateIpAddress": "10.0.1.10",
                            "SubnetId": "subnet-abc",
                            "Placement": {"AvailabilityZone": "us-east-1a"},
                        }
                    ]
                }
            ]
        }
        # terminate_instances fails
        mock_ec2.terminate_instances.side_effect = ClientError(
            {"Error": {"Code": "UnauthorizedOperation", "Message": "no perms"}},
            "TerminateInstances",
        )

        result = ec2_delete_instances(ctx, region=region, instance_ids=ids)

    assert result["deleted_count"] == 0
    assert len(result["errors"]) > 0
    assert any("TerminateInstances failed" in e for e in result["errors"])


# ---------------------------------------------------------------
# Test: confirmation prompt contains instance IDs  (Req 2.2)
# ---------------------------------------------------------------

def test_confirmation_prompt_contains_instance_ids():
    instances = [
        {
            "InstanceId": "i-abc123",
            "InstanceType": "g5.xlarge",
            "PrivateIpAddress": "10.0.1.5",
            "SubnetId": "subnet-xyz",
            "Placement": {"AvailabilityZone": "us-east-1a"},
        },
        {
            "InstanceId": "i-def456",
            "InstanceType": "g6e.2xlarge",
            "PrivateIpAddress": "10.0.2.10",
            "SubnetId": "subnet-abc",
            "Placement": {"AvailabilityZone": "us-east-1b"},
        },
    ]
    prompt = _build_confirmation_prompt(instances)

    assert "i-abc123" in prompt
    assert "i-def456" in prompt
    assert "2" in prompt  # count
