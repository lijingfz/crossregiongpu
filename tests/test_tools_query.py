"""Tests for src/tools/query.py using moto.

Requirements: 1.2, 1.3, 1.4, 1.6, 3.4
"""

from __future__ import annotations

import os
import tempfile

import boto3
import yaml
from moto import mock_aws

from src.tools.query import ec2_query_instances


def _create_instances(region: str, count: int = 1, **kwargs):
    """Helper: create EC2 instances in moto and return instance IDs."""
    ec2 = boto3.client("ec2", region_name=region)
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]
    subnet = ec2.create_subnet(
        VpcId=vpc_id,
        CidrBlock="10.0.1.0/24",
        AvailabilityZone=f"{region}a",
    )
    subnet_id = subnet["Subnet"]["SubnetId"]

    run_kwargs = {
        "ImageId": "ami-12345678",
        "InstanceType": kwargs.get("instance_type", "g5.xlarge"),
        "MinCount": count,
        "MaxCount": count,
        "SubnetId": kwargs.get("subnet_id", subnet_id),
    }
    resp = ec2.run_instances(**run_kwargs)
    ids = [i["InstanceId"] for i in resp["Instances"]]
    return ids, subnet_id, vpc_id


def _write_regions_yaml(regions_data: list) -> str:
    """Write a temporary regions.yaml and return its path."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    )
    yaml.dump({"regions": regions_data}, tmp)
    tmp.close()
    return tmp.name


# ---------------------------------------------------------------
# Test: filter by instance_type  (Req 1.2)
# ---------------------------------------------------------------

@mock_aws
def test_query_by_instance_type():
    region = "us-east-1"
    _create_instances(region, 2, instance_type="g5.xlarge")
    _create_instances(region, 1, instance_type="g6.xlarge")

    result = ec2_query_instances(
        region=region, instance_type="g5.xlarge", state="running"
    )

    assert result["total_count"] == 2
    for inst in result["instances"]:
        assert inst["instance_type"] == "g5.xlarge"


# ---------------------------------------------------------------
# Test: filter by subnet_id  (Req 1.2)
# ---------------------------------------------------------------

@mock_aws
def test_query_by_subnet_id():
    region = "us-east-1"
    ids_a, subnet_a, _ = _create_instances(region, 2)

    # Create a second subnet with different instances
    ec2 = boto3.client("ec2", region_name=region)
    vpc = ec2.create_vpc(CidrBlock="10.1.0.0/16")
    sub_b = ec2.create_subnet(
        VpcId=vpc["Vpc"]["VpcId"],
        CidrBlock="10.1.1.0/24",
        AvailabilityZone=f"{region}a",
    )
    subnet_b = sub_b["Subnet"]["SubnetId"]
    ec2.run_instances(
        ImageId="ami-12345678",
        InstanceType="g5.xlarge",
        MinCount=1,
        MaxCount=1,
        SubnetId=subnet_b,
    )

    result = ec2_query_instances(
        region=region, subnet_id=subnet_a, state="running"
    )

    assert result["total_count"] == 2
    for inst in result["instances"]:
        assert inst["subnet_id"] == subnet_a


# ---------------------------------------------------------------
# Test: client-side private_ip filter  (Req 1.2, 1.7)
# ---------------------------------------------------------------

@mock_aws
def test_query_by_private_ip():
    region = "us-east-1"
    ids, _, _ = _create_instances(region, 3)

    # Get the private IP of the first instance
    ec2 = boto3.client("ec2", region_name=region)
    desc = ec2.describe_instances(InstanceIds=[ids[0]])
    target_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    result = ec2_query_instances(
        region=region, private_ips=[target_ip], state="running"
    )

    assert result["total_count"] == 1
    assert result["instances"][0]["private_ip"] == target_ip


# ---------------------------------------------------------------
# Test: empty filter rejection  (Req 3.4)
# ---------------------------------------------------------------

@mock_aws
def test_empty_filter_rejected():
    result = ec2_query_instances(state="running")

    assert result["total_count"] == 0
    assert "错误" in result["message"] or "过滤条件" in result["message"]


# ---------------------------------------------------------------
# Test: empty result  (Req 1.6)
# ---------------------------------------------------------------

@mock_aws
def test_query_empty_result():
    result = ec2_query_instances(
        region="us-east-1",
        instance_type="p4d.24xlarge",
        state="running",
    )

    assert result["total_count"] == 0
    assert result["instances"] == []
    assert "未找到" in result["message"]


# ---------------------------------------------------------------
# Test: multi-region query  (Req 1.3, 1.4)
# ---------------------------------------------------------------

@mock_aws
def test_query_multi_region():
    r1, r2 = "us-east-1", "us-west-2"
    _create_instances(r1, 1, instance_type="g5.xlarge")
    _create_instances(r2, 2, instance_type="g5.xlarge")

    config_path = _write_regions_yaml([
        {"region": r1, "priority": 1, "azs": []},
        {"region": r2, "priority": 2, "azs": []},
    ])

    try:
        result = ec2_query_instances(
            instance_type="g5.xlarge",
            state="running",
            config_path=config_path,
        )
        assert result["total_count"] == 3
    finally:
        os.unlink(config_path)
