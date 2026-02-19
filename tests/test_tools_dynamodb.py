"""Tests for src/tools/dynamodb.py using moto."""

from __future__ import annotations

import boto3
from moto import mock_aws

from src.tools.dynamodb import dynamodb_put_instances

TABLE_NAME = "GpuProvisioningInstances"


def _create_table():
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    ddb.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {"AttributeName": "request_id", "KeyType": "HASH"},
            {"AttributeName": "region_instance_id", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "request_id", "AttributeType": "S"},
            {"AttributeName": "region_instance_id", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    return ddb.Table(TABLE_NAME)


@mock_aws
def test_put_instances_writes_records():
    table = _create_table()

    instances = [
        {"instance_id": "i-aaa", "az": "us-east-1a", "private_ip": "10.0.0.1", "public_ip": "54.1.1.1"},
        {"instance_id": "i-bbb", "az": "us-east-1b", "private_ip": "10.0.0.2"},
    ]

    result = dynamodb_put_instances(
        table=TABLE_NAME,
        request_id="req-001",
        goal_region="us-east-1",
        region="us-east-1",
        instance_type="g6.xlarge",
        instances=instances,
        step_id="s1",
        allocation_status="PARTIAL",
        dynamodb_region="us-east-1",
    )

    assert result["written"] == 2
    assert result["errors"] == []

    resp = table.query(KeyConditionExpression=boto3.dynamodb.conditions.Key("request_id").eq("req-001"))
    assert resp["Count"] == 2


@mock_aws
def test_put_instances_sk_format():
    table = _create_table()

    dynamodb_put_instances(
        table=TABLE_NAME,
        request_id="req-002",
        goal_region="ap-south-1",
        region="us-west-2",
        instance_type="g5.xlarge",
        instances=[{"instance_id": "i-xyz", "az": "us-west-2a", "private_ip": "10.0.1.1"}],
        dynamodb_region="us-east-1",
    )

    resp = table.get_item(Key={"request_id": "req-002", "region_instance_id": "us-west-2#i-xyz"})
    item = resp["Item"]
    assert item["region"] == "us-west-2"
    assert item["goal_region"] == "ap-south-1"
    assert item["instance_type"] == "g5.xlarge"
