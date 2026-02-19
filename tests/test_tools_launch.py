"""Tests for src/tools/launch.py using moto."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import boto3
from botocore.exceptions import ClientError
from moto import mock_aws

from src.tools.launch import _generate_client_token, _run_instances, ec2_launch_instances


def _setup_vpc(region: str):
    """Create a VPC + subnets in the moto mock for launching instances."""
    ec2 = boto3.client("ec2", region_name=region)
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]

    sub1 = ec2.create_subnet(
        VpcId=vpc_id, CidrBlock="10.0.1.0/24", AvailabilityZone=f"{region}a"
    )
    sub2 = ec2.create_subnet(
        VpcId=vpc_id, CidrBlock="10.0.2.0/24", AvailabilityZone=f"{region}b"
    )
    sg = ec2.create_security_group(
        GroupName="test-sg", Description="test", VpcId=vpc_id
    )
    return (
        sub1["Subnet"]["SubnetId"],
        sub2["Subnet"]["SubnetId"],
        sg["GroupId"],
    )


class TestClientToken:
    def test_tokens_are_unique(self):
        t1 = _generate_client_token("req1", "us-east-1", "subnet-abc", 0)
        t2 = _generate_client_token("req1", "us-east-1", "subnet-abc", 1)
        assert t1 != t2

    def test_token_contains_context(self):
        t = _generate_client_token("req42", "ap-south-1", "subnet-xyz123", 5)
        assert "req42" in t
        assert "ap-south-1" in t


@mock_aws
def test_full_launch():
    """Launch exactly target_count → status=FULL."""
    region = "us-east-1"
    sub1, sub2, sg = _setup_vpc(region)

    result = ec2_launch_instances(
        region=region,
        instance_type="g5.xlarge",
        target_count=3,
        subnets=[sub1, sub2],
        ami="ami-12345678",
        security_group_ids=[sg],
        batch_max=4,
        request_id="test-req-1",
    )

    assert result["status"] == "FULL"
    assert result["launched"] == 3
    assert result["remaining"] == 0
    assert len(result["instances"]) == 3


@mock_aws
def test_partial_launch_single_subnet():
    """With only 1 subnet and small batch, should still launch all in moto."""
    region = "us-east-1"
    sub1, _, sg = _setup_vpc(region)

    result = ec2_launch_instances(
        region=region,
        instance_type="g5.xlarge",
        target_count=2,
        subnets=[sub1],
        ami="ami-12345678",
        security_group_ids=[sg],
        batch_max=2,
        request_id="test-req-2",
    )

    assert result["status"] == "FULL"
    assert result["launched"] == 2


@mock_aws
def test_instances_have_required_fields():
    """Each instance in the result should have id, type, az, private_ip."""
    region = "us-east-1"
    sub1, _, sg = _setup_vpc(region)

    result = ec2_launch_instances(
        region=region,
        instance_type="g5.xlarge",
        target_count=1,
        subnets=[sub1],
        ami="ami-12345678",
        security_group_ids=[sg],
        request_id="test-req-3",
    )

    inst = result["instances"][0]
    assert inst["instance_id"].startswith("i-")
    assert inst["instance_type"] == "g5.xlarge"
    assert inst["az"] != ""
    assert inst["private_ip"] != ""


# ---------------------------------------------------------------------------
# Binary backoff tests (Requirement 3.3, 3.4)
# ---------------------------------------------------------------------------

def _make_capacity_error():
    """Create a ClientError mimicking InsufficientInstanceCapacity."""
    return ClientError(
        {"Error": {"Code": "InsufficientInstanceCapacity", "Message": "not enough"}},
        "RunInstances",
    )


@mock_aws
def test_binary_backoff_halves_batch():
    """When capacity fails, batch should halve until 1, then move to next subnet."""
    region = "us-east-1"
    sub1, sub2, sg = _setup_vpc(region)

    call_log = []

    original_run = _run_instances

    def tracking_run(client, count, **kwargs):
        call_log.append({"count": count, "subnet": kwargs.get("subnet", "")})
        # Fail all attempts on first subnet, succeed on second
        if kwargs.get("subnet") == sub1:
            raise _make_capacity_error()
        return original_run(client=client, count=count, **kwargs)

    with patch("src.tools.launch._run_instances", side_effect=tracking_run):
        result = ec2_launch_instances(
            region=region,
            instance_type="g5.xlarge",
            target_count=4,
            subnets=[sub1, sub2],
            ami="ami-12345678",
            security_group_ids=[sg],
            batch_max=4,
            max_attempts_per_subnet=5,
            request_id="backoff-test",
        )

    # Verify binary backoff happened on sub1: 4 → 2 → 1 → break
    sub1_counts = [c["count"] for c in call_log if c["subnet"] == sub1]
    assert sub1_counts[0] == 4
    assert sub1_counts[1] == 2
    assert sub1_counts[2] == 1

    # Should have succeeded on sub2
    assert result["status"] == "FULL"
    assert result["launched"] == 4


@mock_aws
def test_all_subnets_exhausted_returns_none():
    """When all subnets fail at batch=1, result should be NONE."""
    region = "us-east-1"
    sub1, sub2, sg = _setup_vpc(region)

    def always_fail(client, count, **kwargs):
        raise _make_capacity_error()

    with patch("src.tools.launch._run_instances", side_effect=always_fail):
        result = ec2_launch_instances(
            region=region,
            instance_type="g5.xlarge",
            target_count=2,
            subnets=[sub1, sub2],
            ami="ami-12345678",
            security_group_ids=[sg],
            batch_max=4,
            max_attempts_per_subnet=5,
            request_id="exhaust-test",
        )

    assert result["status"] == "NONE"
    assert result["launched"] == 0


# ---------------------------------------------------------------------------
# Subnet rotation test (Requirement 3.5, 3.6)
# ---------------------------------------------------------------------------

@mock_aws
def test_subnet_rotation_on_partial_capacity():
    """First subnet exhausts after some successes, second subnet completes the rest."""
    region = "us-east-1"
    sub1, sub2, sg = _setup_vpc(region)

    original_run = _run_instances
    sub1_calls = 0

    def partial_sub1(client, count, **kwargs):
        nonlocal sub1_calls
        if kwargs.get("subnet") == sub1:
            sub1_calls += 1
            # Allow first call on sub1, fail all subsequent ones
            if sub1_calls == 1:
                return original_run(client=client, count=count, **kwargs)
            raise _make_capacity_error()
        return original_run(client=client, count=count, **kwargs)

    with patch("src.tools.launch._run_instances", side_effect=partial_sub1):
        result = ec2_launch_instances(
            region=region,
            instance_type="g5.xlarge",
            target_count=5,
            subnets=[sub1, sub2],
            ami="ami-12345678",
            security_group_ids=[sg],
            batch_max=4,
            max_attempts_per_subnet=5,
            request_id="rotation-test",
        )

    # sub1 launched batch_max=4 on first call, then exhausted → rotated to sub2 for remaining 1
    assert result["launched"] == 5
    assert result["status"] == "FULL"


# ---------------------------------------------------------------------------
# Chunked launch test (Requirement 3.1)
# ---------------------------------------------------------------------------

@mock_aws
def test_batch_never_exceeds_batch_max():
    """Every RunInstances call should request at most batch_max instances."""
    region = "us-east-1"
    sub1, sub2, sg = _setup_vpc(region)

    call_counts = []
    original_run = _run_instances

    def tracking_run(client, count, **kwargs):
        call_counts.append(count)
        return original_run(client=client, count=count, **kwargs)

    with patch("src.tools.launch._run_instances", side_effect=tracking_run):
        ec2_launch_instances(
            region=region,
            instance_type="g5.xlarge",
            target_count=10,
            subnets=[sub1, sub2],
            ami="ami-12345678",
            security_group_ids=[sg],
            batch_max=3,
            request_id="chunk-test",
        )

    assert all(c <= 3 for c in call_counts), f"Some calls exceeded batch_max: {call_counts}"


# ---------------------------------------------------------------------------
# Non-capacity error returns ERROR status
# ---------------------------------------------------------------------------

@mock_aws
def test_non_capacity_error_returns_error_status():
    """A non-capacity ClientError should immediately return ERROR."""
    region = "us-east-1"
    sub1, _, sg = _setup_vpc(region)

    def auth_fail(client, count, **kwargs):
        raise ClientError(
            {"Error": {"Code": "UnauthorizedOperation", "Message": "no perms"}},
            "RunInstances",
        )

    with patch("src.tools.launch._run_instances", side_effect=auth_fail):
        result = ec2_launch_instances(
            region=region,
            instance_type="g5.xlarge",
            target_count=2,
            subnets=[sub1],
            ami="ami-12345678",
            security_group_ids=[sg],
            request_id="err-test",
        )

    assert result["status"] == "ERROR"
    assert result["error_code"] == "UnauthorizedOperation"

# ---------------------------------------------------------------------------
# target_count guard tests (prevents LLM hallucination over-provisioning)
# ---------------------------------------------------------------------------

def test_target_count_zero_returns_error():
    """target_count=0 should be rejected without calling AWS."""
    result = ec2_launch_instances(
        region="us-east-1",
        instance_type="g5.xlarge",
        target_count=0,
        subnets=["subnet-fake"],
        ami="ami-12345678",
        security_group_ids=[],
    )
    assert result["status"] == "ERROR"
    assert result["error_code"] == "INVALID_TARGET_COUNT"


def test_target_count_negative_returns_error():
    """Negative target_count should be rejected."""
    result = ec2_launch_instances(
        region="us-east-1",
        instance_type="g5.xlarge",
        target_count=-5,
        subnets=["subnet-fake"],
        ami="ami-12345678",
        security_group_ids=[],
    )
    assert result["status"] == "ERROR"
    assert result["error_code"] == "INVALID_TARGET_COUNT"


def test_target_count_exceeds_max_returns_error():
    """target_count above MAX_TARGET_COUNT should be rejected."""
    from src.tools.launch import MAX_TARGET_COUNT

    result = ec2_launch_instances(
        region="us-east-1",
        instance_type="g5.xlarge",
        target_count=MAX_TARGET_COUNT + 1,
        subnets=["subnet-fake"],
        ami="ami-12345678",
        security_group_ids=[],
    )
    assert result["status"] == "ERROR"
    assert result["error_code"] == "TARGET_COUNT_EXCEEDED"
    assert str(MAX_TARGET_COUNT) in result["message"]


def test_target_count_at_max_is_allowed():
    """target_count exactly at MAX_TARGET_COUNT should NOT be rejected by the guard."""
    from src.tools.launch import MAX_TARGET_COUNT

    # This will fail at the AWS call level (no real VPC), but should pass
    # the target_count guard. We just verify it doesn't return TARGET_COUNT_EXCEEDED.
    result = ec2_launch_instances(
        region="us-east-1",
        instance_type="g5.xlarge",
        target_count=MAX_TARGET_COUNT,
        subnets=["subnet-fake"],
        ami="ami-12345678",
        security_group_ids=[],
    )
    assert result.get("error_code") != "TARGET_COUNT_EXCEEDED"


