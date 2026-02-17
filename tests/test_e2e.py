"""End-to-end checkpoint tests for the GPU Cross-Region Dynamic Scheduler.

Exercises the full pipeline using moto-mocked AWS services:
  Input → Preflight → Chunked Launch → Cross-Region Fallback → DynamoDB Write → Final Output

Validates:
  - Full satisfaction in a single region
  - Partial satisfaction with cross-region fallback
  - All regions fail → FAILED
  - single_region mode (no fallback)
  - DynamoDB records match launched instances
  - Finalize tool produces correct summary
"""

from __future__ import annotations

import boto3
from moto import mock_aws

from src.models.schemas import (
    AZConfig,
    InstanceInfo,
    NextAction,
    RegionConfig,
    StepResult,
)
from src.orchestrator.executor import (
    Orchestrator,
    OrchestratorState,
    ToolCallbacks,
)
from src.tools.dynamodb import dynamodb_put_instances
from src.tools.finalize import finalize
from src.tools.launch import ec2_launch_instances
from src.tools.describe import ec2_describe_instances
from src.tools.offerings import describe_instance_type_offerings


TABLE_NAME = "GpuProvisioningInstances"
DDB_REGION = "us-east-1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_ddb_table():
    """Create the GpuProvisioningInstances table in moto."""
    ddb = boto3.resource("dynamodb", region_name=DDB_REGION)
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


def _setup_vpc(region: str):
    """Create VPC, subnets, and security group in moto for a region."""
    ec2 = boto3.client("ec2", region_name=region)
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]

    sub_a = ec2.create_subnet(
        VpcId=vpc_id, CidrBlock="10.0.1.0/24", AvailabilityZone=f"{region}a"
    )
    sub_b = ec2.create_subnet(
        VpcId=vpc_id, CidrBlock="10.0.2.0/24", AvailabilityZone=f"{region}b"
    )
    sg = ec2.create_security_group(
        GroupName=f"sg-{region}", Description="test", VpcId=vpc_id
    )
    return sub_a["Subnet"]["SubnetId"], sub_b["Subnet"]["SubnetId"], sg["GroupId"]


def _make_region(name: str, subnets: list[str]) -> RegionConfig:
    """Build a RegionConfig from real moto subnet IDs."""
    return RegionConfig(
        region=name,
        priority=1,
        azs=[
            AZConfig(az_name=f"{name}a", subnets=[subnets[0]]),
            AZConfig(az_name=f"{name}b", subnets=[subnets[1]]),
        ],
        ami_id="ami-12345678",
    )


def _real_tools(sg_id: str, ddb_table_name: str = TABLE_NAME) -> ToolCallbacks:
    """Build ToolCallbacks that call real moto-backed AWS tools."""

    def _check_offerings(**kw):
        return describe_instance_type_offerings(
            region=kw["region"], instance_type=kw["instance_type"]
        )

    def _launch(**kw):
        return ec2_launch_instances(
            region=kw["region"],
            instance_type=kw["instance_type"],
            target_count=kw["target_count"],
            subnets=kw["subnets"],
            ami=kw.get("ami", "ami-12345678"),
            security_group_ids=[sg_id],
            iam_profile=kw.get("iam_profile", ""),
            tags=kw.get("tags", {}),
            batch_max=kw.get("batch_max", 4),
            request_id=kw.get("request_id", ""),
            key_name=kw.get("key_name", ""),
        )

    def _describe(**kw):
        return ec2_describe_instances(
            region=kw["region"], instance_ids=kw["instance_ids"]
        )

    def _put(**kw):
        return dynamodb_put_instances(
            table=kw.get("table", ddb_table_name),
            request_id=kw["request_id"],
            goal_region=kw["goal_region"],
            region=kw["region"],
            instance_type=kw["instance_type"],
            instances=kw["instances"],
            step_id=kw.get("step_id", ""),
            allocation_status=kw.get("allocation_status", "PARTIAL"),
            dynamodb_region=DDB_REGION,
        )

    def _decide(**kw):
        step = kw.get("step_result")
        if isinstance(step, dict):
            step = StepResult(**step)
        remaining = kw.get("remaining", 0)
        if remaining <= 0:
            return NextAction(action="done", rationale="all launched")
        return NextAction(action="continue_next_region", rationale="need more")

    return ToolCallbacks(
        check_offerings=_check_offerings,
        launch_instances=_launch,
        describe_instances=_describe,
        put_instances=_put,
        decide_next_action=_decide,
    )


def _query_ddb(request_id: str) -> list[dict]:
    """Query all DynamoDB records for a request_id."""
    table = boto3.resource("dynamodb", region_name=DDB_REGION).Table(TABLE_NAME)
    resp = table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("request_id").eq(request_id),
    )
    return resp.get("Items", [])


# ---------------------------------------------------------------------------
# E2E Test 1: Full satisfaction in first region
# ---------------------------------------------------------------------------

@mock_aws
def test_e2e_full_satisfaction_single_region():
    """Input 3 instances → all launched in first region → DDB has 3 records → SUCCESS."""
    _create_ddb_table()
    sub1, sub2, sg = _setup_vpc("us-east-1")
    region_cfg = _make_region("us-east-1", [sub1, sub2])

    state = OrchestratorState(
        request_id="e2e-full-001",
        instance_type="g5.xlarge",
        total_count=3,
        remaining=3,
        regions=[region_cfg],
    )

    tools = _real_tools(sg)
    orch = Orchestrator(state, tools, ami="ami-12345678", ddb_table=TABLE_NAME, ddb_region=DDB_REGION)
    result = orch.run()

    # Orchestrator result
    assert result.status == "SUCCESS"
    assert result.total_launched == 3
    assert result.remaining == 0
    assert len(result.all_instances) == 3

    # Every instance has enriched fields
    for inst in result.all_instances:
        assert inst.instance_id.startswith("i-")
        assert inst.private_ip != ""

    # DynamoDB records
    items = _query_ddb("e2e-full-001")
    assert len(items) == 3
    instance_ids_in_ddb = {item["instance_id"] for item in items}
    instance_ids_in_result = {inst.instance_id for inst in result.all_instances}
    assert instance_ids_in_ddb == instance_ids_in_result

    # Finalize tool
    summary = finalize(
        total_requested=result.total_requested,
        total_launched=result.total_launched,
        region_results=[r.model_dump() for r in result.region_results],
        all_instances=[i.model_dump() for i in result.all_instances],
        ddb_written=len(items),
    )
    assert summary["status"] == "SUCCESS"
    assert summary["gap"] == 0
    assert len(summary["gpu_list"]) == 3


# ---------------------------------------------------------------------------
# E2E Test 2: Cross-region fallback (partial in R1, rest in R2)
# ---------------------------------------------------------------------------

@mock_aws
def test_e2e_cross_region_fallback():
    """R1 partially satisfies → fallback to R2 → total SUCCESS → DDB has all records."""
    _create_ddb_table()

    sub1_r1, sub2_r1, sg_r1 = _setup_vpc("us-east-1")
    sub1_r2, sub2_r2, sg_r2 = _setup_vpc("us-west-2")

    region1 = _make_region("us-east-1", [sub1_r1, sub2_r1])
    region2 = _make_region("us-west-2", [sub1_r2, sub2_r2])

    state = OrchestratorState(
        request_id="e2e-fallback-001",
        instance_type="g5.xlarge",
        total_count=6,
        remaining=6,
        regions=[region1, region2],
    )

    def partial_launch(**kw):
        """R1 launches 2 (partial), R2 launches the remaining."""
        if kw["region"] == "us-east-1":
            # Launch only 2 in R1, then report partial with correct remaining
            result = ec2_launch_instances(
                region="us-east-1",
                instance_type=kw["instance_type"],
                target_count=2,
                subnets=kw["subnets"],
                ami="ami-12345678",
                security_group_ids=[sg_r1],
                batch_max=kw.get("batch_max", 4),
                request_id=kw.get("request_id", ""),
            )
            # Override to reflect the original request context
            result["requested"] = kw["target_count"]
            result["remaining"] = kw["target_count"] - result["launched"]
            result["status"] = "PARTIAL"
            return result
        else:
            return ec2_launch_instances(
                region="us-west-2",
                instance_type=kw["instance_type"],
                target_count=kw["target_count"],
                subnets=kw["subnets"],
                ami="ami-12345678",
                security_group_ids=[sg_r2],
                batch_max=kw.get("batch_max", 4),
                request_id=kw.get("request_id", ""),
            )

    tools = _real_tools(sg_r1)
    tools.launch_instances = partial_launch

    orch = Orchestrator(state, tools, ami="ami-12345678", ddb_table=TABLE_NAME, ddb_region=DDB_REGION)
    result = orch.run()

    assert result.status == "SUCCESS"
    assert result.total_launched == 6
    assert result.remaining == 0

    # Should have results from both regions
    regions_used = {r.region for r in result.region_results}
    assert "us-east-1" in regions_used
    assert "us-west-2" in regions_used

    # DynamoDB has all 6 records
    items = _query_ddb("e2e-fallback-001")
    assert len(items) == 6

    # Finalize
    summary = finalize(
        total_requested=result.total_requested,
        total_launched=result.total_launched,
        region_results=[r.model_dump() for r in result.region_results],
        all_instances=[i.model_dump() for i in result.all_instances],
        ddb_written=len(items),
    )
    assert summary["status"] == "SUCCESS"
    assert summary["gap"] == 0
    assert summary["ddb_written"] == 6


# ---------------------------------------------------------------------------
# E2E Test 3: All regions fail → FAILED
# ---------------------------------------------------------------------------

@mock_aws
def test_e2e_all_regions_fail():
    """Both regions return 0 instances → FAILED with gap info."""
    _create_ddb_table()

    sub1_r1, sub2_r1, sg_r1 = _setup_vpc("us-east-1")
    sub1_r2, sub2_r2, sg_r2 = _setup_vpc("us-west-2")

    region1 = _make_region("us-east-1", [sub1_r1, sub2_r1])
    region2 = _make_region("us-west-2", [sub1_r2, sub2_r2])

    state = OrchestratorState(
        request_id="e2e-fail-001",
        instance_type="g5.xlarge",
        total_count=4,
        remaining=4,
        regions=[region1, region2],
    )

    # Force all launches to return 0
    def zero_launch(**kw):
        return StepResult(
            status="NONE",
            requested=kw["target_count"],
            launched=0,
            remaining=kw["target_count"],
            region=kw["region"],
            error_code="InsufficientInstanceCapacity",
            message="No capacity",
        ).model_dump()

    tools = _real_tools(sg_r1)
    tools.launch_instances = zero_launch

    orch = Orchestrator(state, tools, ami="ami-12345678", ddb_table=TABLE_NAME, ddb_region=DDB_REGION)
    result = orch.run()

    assert result.status == "FAILED"
    assert result.total_launched == 0
    assert result.remaining == 4

    # No DDB records
    items = _query_ddb("e2e-fail-001")
    assert len(items) == 0

    # Finalize
    summary = finalize(
        total_requested=result.total_requested,
        total_launched=result.total_launched,
        region_results=[r.model_dump() for r in result.region_results],
        all_instances=[],
        ddb_written=0,
    )
    assert summary["status"] == "FAILED"
    assert summary["gap"] == 4
    assert len(summary["errors"]) > 0


# ---------------------------------------------------------------------------
# E2E Test 4: single_region mode — no cross-region fallback
# ---------------------------------------------------------------------------

@mock_aws
def test_e2e_single_region_no_fallback():
    """single_region mode: partial in R1 → no fallback to R2 → PARTIAL."""
    _create_ddb_table()

    sub1_r1, sub2_r1, sg_r1 = _setup_vpc("us-east-1")
    sub1_r2, sub2_r2, sg_r2 = _setup_vpc("us-west-2")

    region1 = _make_region("us-east-1", [sub1_r1, sub2_r1])
    region2 = _make_region("us-west-2", [sub1_r2, sub2_r2])

    state = OrchestratorState(
        request_id="e2e-single-001",
        instance_type="g5.xlarge",
        total_count=6,
        remaining=6,
        regions=[region1, region2],
        region_mode="single_region",
    )

    launched_regions = []

    def capped_launch(**kw):
        launched_regions.append(kw["region"])
        # Launch only 2, but report remaining relative to original request
        result = ec2_launch_instances(
            region=kw["region"],
            instance_type=kw["instance_type"],
            target_count=2,
            subnets=kw["subnets"],
            ami="ami-12345678",
            security_group_ids=[sg_r1],
            batch_max=kw.get("batch_max", 4),
            request_id=kw.get("request_id", ""),
        )
        result["requested"] = kw["target_count"]
        result["remaining"] = kw["target_count"] - result["launched"]
        result["status"] = "PARTIAL"
        return result

    tools = _real_tools(sg_r1)
    tools.launch_instances = capped_launch

    orch = Orchestrator(state, tools, ami="ami-12345678", ddb_table=TABLE_NAME, ddb_region=DDB_REGION)
    result = orch.run()

    assert result.status == "PARTIAL"
    assert result.total_launched == 2
    assert result.remaining == 4
    assert result.single_region_constrained is True

    # Must NOT have tried R2
    assert all(r == "us-east-1" for r in launched_regions)

    # DDB has only the 2 records from R1
    items = _query_ddb("e2e-single-001")
    assert len(items) == 2


# ---------------------------------------------------------------------------
# E2E Test 5: Preflight skip + fallback
# ---------------------------------------------------------------------------

@mock_aws
def test_e2e_preflight_skip_then_fallback():
    """R1 doesn't offer the instance type → skip → R2 fulfills → SUCCESS."""
    _create_ddb_table()

    sub1_r1, sub2_r1, sg_r1 = _setup_vpc("us-east-1")
    sub1_r2, sub2_r2, sg_r2 = _setup_vpc("us-west-2")

    region1 = _make_region("us-east-1", [sub1_r1, sub2_r1])
    region2 = _make_region("us-west-2", [sub1_r2, sub2_r2])

    state = OrchestratorState(
        request_id="e2e-preflight-001",
        instance_type="g5.xlarge",
        total_count=3,
        remaining=3,
        regions=[region1, region2],
    )

    def mock_offerings(**kw):
        if kw["region"] == "us-east-1":
            return {"supported": False, "offerings": []}
        return {"supported": True, "offerings": [kw["region"]]}

    tools = _real_tools(sg_r2)
    tools.check_offerings = mock_offerings

    # Override launch to use correct SG for R2
    def r2_launch(**kw):
        return ec2_launch_instances(
            region=kw["region"],
            instance_type=kw["instance_type"],
            target_count=kw["target_count"],
            subnets=kw["subnets"],
            ami="ami-12345678",
            security_group_ids=[sg_r2],
            batch_max=kw.get("batch_max", 4),
            request_id=kw.get("request_id", ""),
        )

    tools.launch_instances = r2_launch

    orch = Orchestrator(state, tools, ami="ami-12345678", ddb_table=TABLE_NAME, ddb_region=DDB_REGION)
    result = orch.run()

    assert result.status == "SUCCESS"
    assert result.total_launched == 3

    # R1 should have NOT_OFFERED step
    assert any(r.error_code == "NOT_OFFERED" for r in result.region_results)

    # DDB records all from R2
    items = _query_ddb("e2e-preflight-001")
    assert len(items) == 3
    assert all(item["region"] == "us-west-2" for item in items)
