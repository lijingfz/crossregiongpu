"""Tests for src/tools/finalize.py."""

from __future__ import annotations

from src.tools.finalize import finalize


def _step(region, status, requested, launched, error_code=None, message=""):
    return {
        "region": region,
        "status": status,
        "requested": requested,
        "launched": launched,
        "remaining": requested - launched,
        "error_code": error_code,
        "message": message,
        "instances": [],
    }


def _inst(instance_id, instance_type="g6.xlarge", region="us-east-1", az="us-east-1a",
          private_ip="10.0.0.1", public_ip=None):
    return {
        "instance_id": instance_id,
        "instance_type": instance_type,
        "region": region,
        "az": az,
        "private_ip": private_ip,
        "public_ip": public_ip,
    }


def test_finalize_success():
    result = finalize(
        total_requested=4,
        total_launched=4,
        region_results=[_step("us-east-1", "FULL", 4, 4)],
        all_instances=[_inst(f"i-{i}") for i in range(4)],
        ddb_written=4,
    )
    assert result["status"] == "SUCCESS"
    assert result["gap"] == 0
    assert result["ddb_written"] == 4
    assert len(result["gpu_list"]) == 4
    assert result["errors"] == []


def test_finalize_partial():
    result = finalize(
        total_requested=10,
        total_launched=6,
        region_results=[
            _step("us-east-1", "PARTIAL", 10, 3),
            _step("us-west-2", "PARTIAL", 7, 3),
            _step("eu-west-1", "NONE", 4, 0, error_code="NOT_OFFERED",
                  message="g6.xlarge not offered"),
        ],
        all_instances=[_inst(f"i-{i}") for i in range(6)],
        ddb_written=6,
    )
    assert result["status"] == "PARTIAL"
    assert result["gap"] == 4
    assert result["total_launched"] == 6
    assert len(result["errors"]) == 1
    assert result["errors"][0]["error_code"] == "NOT_OFFERED"


def test_finalize_failed():
    result = finalize(
        total_requested=5,
        total_launched=0,
        region_results=[
            _step("us-east-1", "NONE", 5, 0, error_code="NOT_OFFERED"),
            _step("us-west-2", "ERROR", 5, 0, error_code="VcpuLimitExceeded",
                  message="quota exceeded"),
        ],
        all_instances=[],
        ddb_written=0,
    )
    assert result["status"] == "FAILED"
    assert result["gap"] == 5
    assert len(result["gpu_list"]) == 0
    assert len(result["errors"]) == 2


def test_finalize_region_summary():
    result = finalize(
        total_requested=8,
        total_launched=8,
        region_results=[
            _step("ap-south-1", "PARTIAL", 8, 5),
            _step("ap-northeast-1", "FULL", 3, 3),
        ],
        all_instances=[_inst(f"i-{i}") for i in range(8)],
    )
    summary = result["region_summary"]
    assert len(summary) == 2
    assert summary[0]["region"] == "ap-south-1"
    assert summary[0]["status"] == "PARTIAL"
    assert summary[1]["region"] == "ap-northeast-1"
    assert summary[1]["status"] == "FULL"


def test_finalize_gpu_list_fields():
    result = finalize(
        total_requested=1,
        total_launched=1,
        region_results=[_step("us-east-1", "FULL", 1, 1)],
        all_instances=[_inst("i-abc", region="us-east-1", az="us-east-1b",
                             private_ip="10.0.1.5", public_ip="54.2.3.4")],
    )
    gpu = result["gpu_list"][0]
    assert gpu["instance_id"] == "i-abc"
    assert gpu["region"] == "us-east-1"
    assert gpu["az"] == "us-east-1b"
    assert gpu["private_ip"] == "10.0.1.5"
    assert gpu["public_ip"] == "54.2.3.4"
    assert gpu["instance_type"] == "g6.xlarge"
