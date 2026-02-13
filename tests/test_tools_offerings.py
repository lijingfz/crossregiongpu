"""Tests for src/tools/offerings.py using moto."""

from __future__ import annotations

from moto import mock_aws

from src.tools.offerings import describe_instance_type_offerings


@mock_aws
def test_supported_instance_type_returns_true():
    result = describe_instance_type_offerings(
        region="us-east-1",
        instance_type="t2.micro",
    )
    assert result["supported"] is True
    assert len(result["offerings"]) > 0


@mock_aws
def test_unsupported_instance_type_returns_false():
    result = describe_instance_type_offerings(
        region="us-east-1",
        instance_type="x99.nonexistent",
    )
    assert result["supported"] is False
    assert result["offerings"] == []


@mock_aws
def test_az_level_query():
    result = describe_instance_type_offerings(
        region="us-east-1",
        instance_type="t2.micro",
        az="us-east-1a",
    )
    assert isinstance(result["supported"], bool)
    assert isinstance(result["offerings"], list)
