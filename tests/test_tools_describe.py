"""Tests for src/tools/describe.py using moto."""

from __future__ import annotations

import boto3
from moto import mock_aws

from src.tools.describe import ec2_describe_instances


@mock_aws
def test_describe_returns_instance_info():
    region = "us-east-1"
    ec2 = boto3.client("ec2", region_name=region)

    resp = ec2.run_instances(ImageId="ami-12345678", InstanceType="g5.xlarge", MinCount=1, MaxCount=1)
    iid = resp["Instances"][0]["InstanceId"]

    result = ec2_describe_instances(region=region, instance_ids=[iid])

    assert len(result) == 1
    assert result[0]["instance_id"] == iid
    assert result[0]["instance_type"] == "g5.xlarge"
    assert result[0]["az"] != ""


@mock_aws
def test_describe_empty_list():
    result = ec2_describe_instances(region="us-east-1", instance_ids=[])
    assert result == []


@mock_aws
def test_describe_multiple_instances():
    region = "us-east-1"
    ec2 = boto3.client("ec2", region_name=region)

    resp = ec2.run_instances(ImageId="ami-12345678", InstanceType="g5.xlarge", MinCount=3, MaxCount=3)
    ids = [i["InstanceId"] for i in resp["Instances"]]

    result = ec2_describe_instances(region=region, instance_ids=ids)
    assert len(result) == 3
    returned_ids = {r["instance_id"] for r in result}
    assert returned_ids == set(ids)
