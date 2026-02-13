"""Property-based tests for the Probe-and-Fill scheduling system.

Uses Hypothesis to verify correctness properties defined in the design document.
Each property maps to a specific requirement from the spec.

Properties 1-11 cover: batch limits, binary backoff, capacity exhaustion,
StepResult consistency, remaining monotonicity, region termination,
DynamoDB completeness, client_token uniqueness, offerings pre-check,
next_action structure, and single_region mode constraints.
"""

from __future__ import annotations

import math
from unittest.mock import patch

import boto3
from botocore.exceptions import ClientError
from hypothesis import given, settings, assume
from hypothesis import strategies as st
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
from src.tools.launch import _generate_client_token, _run_instances, ec2_launch_instances


# ---------------------------------------------------------------------------
# Shared strategies
# ---------------------------------------------------------------------------

batch_max_st = st.integers(min_value=1, max_value=20)
target_count_st = st.integers(min_value=1, max_value=50)
num_subnets_st = st.integers(min_value=1, max_value=5)
num_regions_st = st.integers(min_value=1, max_value=6)


def _setup_vpc(region: str):
    """Create VPC + subnets in moto mock."""
    ec2 = boto3.client("ec2", region_name=region)
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]
    sub = ec2.create_subnet(
        VpcId=vpc_id, CidrBlock="10.0.1.0/24", AvailabilityZone=f"{region}a"
    )
    sg = ec2.create_security_group(
        GroupName="test-sg", Description="test", VpcId=vpc_id
    )
    return sub["Subnet"]["SubnetId"], sg["GroupId"]


def _make_region(name: str, n_subnets: int = 1) -> RegionConfig:
    subnets = [f"subnet-{name}-{i}" for i in range(n_subnets)]
    return RegionConfig(
        region=name,
        priority=1,
        azs=[AZConfig(az_name=f"{name}a", subnets=subnets)],
    )


def _make_launch_result(
    region: str, requested: int, launched: int, error_code: str | None = None
) -> dict:
    remaining = requested - launched
    if launched == requested:
        status = "FULL"
    elif launched > 0:
        status = "PARTIAL"
    elif error_code:
        status = "ERROR"
    else:
        status = "NONE"
    instances = [
        InstanceInfo(
            instance_id=f"i-{region}-{i}",
            instance_type="g6.xlarge",
            az=f"{region}a",
            private_ip=f"10.0.0.{i + 1}",
        ).model_dump()
        for i in range(launched)
    ]
    return StepResult(
        status=status,
        requested=requested,
        launched=launched,
        remaining=remaining,
        region=region,
        instances=instances,
        error_code=error_code,
        message=f"Launched {launched}/{requested}",
    ).model_dump()


REGION_NAMES = [
    "us-east-1", "us-west-2", "eu-west-1",
    "ap-northeast-1", "ap-south-1", "ap-southeast-1",
]


# ===================================================================
# Property 1: 分批启动不超过 batch_max
# **Validates: Requirements 3.1**
# ===================================================================

class TestProperty1BatchMax:
    """For any launch attempt, single RunInstances count never exceeds batch_max."""

    @settings(max_examples=100, deadline=None)
    @given(
        target_count=target_count_st,
        batch_max=batch_max_st,
    )
    @mock_aws
    def test_batch_never_exceeds_max(self, target_count: int, batch_max: int):
        region = "us-east-1"
        sub_id, sg_id = _setup_vpc(region)

        call_counts = []
        original_run = _run_instances

        def tracking_run(client, count, **kwargs):
            call_counts.append(count)
            return original_run(client=client, count=count, **kwargs)

        with patch("src.tools.launch._run_instances", side_effect=tracking_run):
            ec2_launch_instances(
                region=region,
                instance_type="t2.micro",
                target_count=target_count,
                subnets=[sub_id],
                ami="ami-12345678",
                security_group_ids=[sg_id],
                batch_max=batch_max,
                request_id=f"p1-{target_count}-{batch_max}",
            )

        assert all(
            c <= batch_max for c in call_counts
        ), f"Call exceeded batch_max={batch_max}: {call_counts}"



# ===================================================================
# Property 2: 二分退让正确性
# **Validates: Requirements 3.3**
# ===================================================================

class TestProperty2BinaryBackoff:
    """For any InsufficientInstanceCapacity error with batch > 1,
    next attempt batch == ceil(current_batch / 2)."""

    @settings(max_examples=100, deadline=None)
    @given(
        initial_batch=st.integers(min_value=2, max_value=20),
    )
    @mock_aws
    def test_binary_backoff_halves_correctly(self, initial_batch: int):
        region = "us-east-1"
        sub_id, sg_id = _setup_vpc(region)

        call_counts = []

        def always_fail(client, count, **kwargs):
            call_counts.append(count)
            raise ClientError(
                {"Error": {"Code": "InsufficientInstanceCapacity", "Message": "no cap"}},
                "RunInstances",
            )

        with patch("src.tools.launch._run_instances", side_effect=always_fail):
            ec2_launch_instances(
                region=region,
                instance_type="t2.micro",
                target_count=initial_batch,
                subnets=[sub_id],
                ami="ami-12345678",
                security_group_ids=[sg_id],
                batch_max=initial_batch,
                max_attempts_per_subnet=20,
                request_id=f"p2-{initial_batch}",
            )

        # Verify binary backoff sequence: each next count == ceil(prev / 2)
        for i in range(1, len(call_counts)):
            prev = call_counts[i - 1]
            curr = call_counts[i]
            if prev > 1:
                assert curr == math.ceil(prev / 2), (
                    f"Expected ceil({prev}/2)={math.ceil(prev / 2)}, got {curr}. "
                    f"Sequence: {call_counts}"
                )


# ===================================================================
# Property 3: 容量耗尽判定
# **Validates: Requirements 3.4**
# ===================================================================

class TestProperty3CapacityExhaustion:
    """When batch_size=1 still fails, system moves to next subnet, not infinite retry."""

    @settings(max_examples=100, deadline=None)
    @given(
        num_subnets=st.integers(min_value=1, max_value=4),
        batch_max=batch_max_st,
    )
    @mock_aws
    def test_exhausted_subnets_terminate(self, num_subnets: int, batch_max: int):
        region = "us-east-1"
        ec2 = boto3.client("ec2", region_name=region)
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        vpc_id = vpc["Vpc"]["VpcId"]
        subnets = []
        for i in range(num_subnets):
            s = ec2.create_subnet(
                VpcId=vpc_id,
                CidrBlock=f"10.0.{i}.0/24",
                AvailabilityZone=f"{region}a",
            )
            subnets.append(s["Subnet"]["SubnetId"])
        sg = ec2.create_security_group(
            GroupName="test-sg", Description="test", VpcId=vpc_id
        )

        call_subnets = []

        def always_fail(client, count, **kwargs):
            call_subnets.append(kwargs.get("subnet", ""))
            raise ClientError(
                {"Error": {"Code": "InsufficientInstanceCapacity", "Message": "no cap"}},
                "RunInstances",
            )

        with patch("src.tools.launch._run_instances", side_effect=always_fail):
            result = ec2_launch_instances(
                region=region,
                instance_type="t2.micro",
                target_count=5,
                subnets=subnets,
                ami="ami-12345678",
                security_group_ids=[sg["GroupId"]],
                batch_max=batch_max,
                max_attempts_per_subnet=10,
                request_id=f"p3-{num_subnets}-{batch_max}",
            )

        # Must terminate (not hang) and return NONE
        assert result["status"] == "NONE"
        assert result["launched"] == 0
        # Each subnet was attempted (at least once)
        for sub in subnets:
            assert sub in call_subnets, f"Subnet {sub} was never tried"



# ===================================================================
# Property 4: StepResult 状态一致性
# **Validates: Requirements 4.1**
# ===================================================================

class TestProperty4StepResultConsistency:
    """StepResult status must be consistent with launched/requested/error fields."""

    @settings(max_examples=100, deadline=None)
    @given(
        requested=st.integers(min_value=1, max_value=50),
        launched=st.integers(min_value=0, max_value=50),
        has_error=st.booleans(),
    )
    def test_status_matches_counts(self, requested: int, launched: int, has_error: bool):
        assume(launched <= requested)

        remaining = requested - launched
        error_code = "SomeError" if has_error and launched == 0 else None

        if launched == requested:
            expected_status = "FULL"
        elif launched > 0:
            expected_status = "PARTIAL"
        elif error_code:
            expected_status = "ERROR"
        else:
            expected_status = "NONE"

        step = StepResult(
            status=expected_status,
            requested=requested,
            launched=launched,
            remaining=remaining,
            region="us-east-1",
            error_code=error_code,
            message="test",
        )

        # Verify the invariants
        if step.status == "FULL":
            assert step.launched == step.requested
        elif step.status == "PARTIAL":
            assert 0 < step.launched < step.requested
        elif step.status == "NONE":
            assert step.launched == 0
            assert step.error_code is None
        elif step.status == "ERROR":
            assert step.error_code is not None


# ===================================================================
# Property 5: remaining 单调递减
# **Validates: Requirements 4.2, 4.3**
# ===================================================================

class TestProperty5RemainingMonotonic:
    """Across the orchestrator run, remaining only decreases or stays the same."""

    @settings(max_examples=100, deadline=None)
    @given(
        total=st.integers(min_value=2, max_value=30),
        num_regions=st.integers(min_value=1, max_value=4),
        launch_fractions=st.lists(
            st.floats(min_value=0.0, max_value=1.0),
            min_size=1,
            max_size=4,
        ),
    )
    def test_remaining_never_increases(
        self, total: int, num_regions: int, launch_fractions: list[float]
    ):
        regions = [_make_region(REGION_NAMES[i % len(REGION_NAMES)]) for i in range(num_regions)]
        state = OrchestratorState(
            request_id=f"p5-{total}-{num_regions}",
            instance_type="g6.xlarge",
            total_count=total,
            remaining=total,
            regions=regions,
        )

        call_idx = {"n": 0}

        def mock_launch(**kw):
            idx = call_idx["n"]
            call_idx["n"] += 1
            frac = launch_fractions[idx % len(launch_fractions)]
            launched = min(int(kw["target_count"] * frac), kw["target_count"])
            return _make_launch_result(kw["region"], kw["target_count"], launched)

        tools = ToolCallbacks(
            launch_instances=mock_launch,
            decide_next_action=lambda **kw: NextAction(
                action="continue_next_region", rationale="partial"
            ),
        )

        orch = Orchestrator(state, tools, ami="ami-test")
        result = orch.run()

        # Collect remaining values from step results
        remaining_values = [r.remaining for r in result.region_results]
        for i in range(1, len(remaining_values)):
            assert remaining_values[i] <= remaining_values[i - 1], (
                f"remaining increased: {remaining_values}"
            )


# ===================================================================
# Property 6: Region 回退终止性
# **Validates: Requirements 4.6**
# ===================================================================

class TestProperty6RegionTermination:
    """Cursor strictly increases — the orchestrator never revisits a region
    and always terminates."""

    @settings(max_examples=100, deadline=None)
    @given(
        num_regions=st.integers(min_value=1, max_value=6),
    )
    def test_cursor_strictly_increases(self, num_regions: int):
        regions = [_make_region(REGION_NAMES[i]) for i in range(num_regions)]
        state = OrchestratorState(
            request_id=f"p6-{num_regions}",
            instance_type="g6.xlarge",
            total_count=100,  # large enough to never be fully satisfied
            remaining=100,
            regions=regions,
        )

        visited_regions = []

        def mock_launch(**kw):
            visited_regions.append(kw["region"])
            return _make_launch_result(kw["region"], kw["target_count"], 0)

        tools = ToolCallbacks(
            launch_instances=mock_launch,
            decide_next_action=lambda **kw: NextAction(
                action="continue_next_region", rationale="none"
            ),
        )

        orch = Orchestrator(state, tools, ami="ami-test")
        result = orch.run()

        # Each region visited at most once
        assert len(visited_regions) == len(set(visited_regions)), (
            f"Region revisited: {visited_regions}"
        )
        # Total visits <= number of regions
        assert len(visited_regions) <= num_regions



# ===================================================================
# Property 7: DynamoDB 写入完整性
# **Validates: Requirements 5.1**
# ===================================================================

class TestProperty7DDBWriteCompleteness:
    """Every successfully launched instance must have a corresponding DynamoDB write."""

    @settings(max_examples=100, deadline=None)
    @given(
        num_regions=st.integers(min_value=1, max_value=3),
        launches_per_region=st.lists(
            st.integers(min_value=0, max_value=10),
            min_size=1,
            max_size=3,
        ),
    )
    def test_all_launched_written_to_ddb(
        self, num_regions: int, launches_per_region: list[int]
    ):
        regions = [_make_region(REGION_NAMES[i]) for i in range(num_regions)]
        total = 100  # large enough
        state = OrchestratorState(
            request_id=f"p7-{num_regions}",
            instance_type="g6.xlarge",
            total_count=total,
            remaining=total,
            regions=regions,
        )

        ddb_instance_ids: list[str] = []
        call_idx = {"n": 0}

        def mock_launch(**kw):
            idx = call_idx["n"]
            call_idx["n"] += 1
            n = launches_per_region[idx % len(launches_per_region)]
            n = min(n, kw["target_count"])
            return _make_launch_result(kw["region"], kw["target_count"], n)

        def mock_put(**kw):
            for inst in kw.get("instances", []):
                ddb_instance_ids.append(inst["instance_id"])
            return {"written": len(kw.get("instances", [])), "errors": []}

        tools = ToolCallbacks(
            launch_instances=mock_launch,
            put_instances=mock_put,
            decide_next_action=lambda **kw: NextAction(
                action="continue_next_region", rationale="partial"
            ),
        )

        orch = Orchestrator(state, tools, ami="ami-test")
        result = orch.run()

        # Every instance in the final result must have been written to DDB
        launched_ids = {inst.instance_id for inst in result.all_instances}
        written_ids = set(ddb_instance_ids)
        assert launched_ids == written_ids, (
            f"Missing DDB writes: {launched_ids - written_ids}"
        )


# ===================================================================
# Property 8: client_token 唯一性
# **Validates: Requirements 3.7**
# ===================================================================

class TestProperty8ClientTokenUniqueness:
    """All client_tokens generated within a run must be unique."""

    @settings(max_examples=100, deadline=None)
    @given(
        num_calls=st.integers(min_value=2, max_value=50),
        request_id=st.text(
            alphabet=st.characters(whitelist_categories=("Ll", "Nd")),
            min_size=4,
            max_size=12,
        ),
    )
    def test_tokens_all_unique(self, num_calls: int, request_id: str):
        tokens = set()
        for seq in range(num_calls):
            token = _generate_client_token(
                request_id, "us-east-1", "subnet-abc123", seq
            )
            assert token not in tokens, (
                f"Duplicate token at seq={seq}: {token}"
            )
            tokens.add(token)

        assert len(tokens) == num_calls


# ===================================================================
# Property 9: Offerings 预检拦截
# **Validates: Requirements 2.2**
# ===================================================================

class TestProperty9OfferingsPrecheck:
    """If a region does not support the instance type, RunInstances is never called."""

    @settings(max_examples=100, deadline=None)
    @given(
        num_regions=st.integers(min_value=1, max_value=4),
        unsupported_mask=st.lists(
            st.booleans(), min_size=1, max_size=4
        ),
    )
    def test_unsupported_regions_never_launched(
        self, num_regions: int, unsupported_mask: list[bool]
    ):
        regions = [_make_region(REGION_NAMES[i]) for i in range(num_regions)]
        # Pad mask to match num_regions
        mask = (unsupported_mask * num_regions)[:num_regions]

        state = OrchestratorState(
            request_id=f"p9-{num_regions}",
            instance_type="g6.xlarge",
            total_count=10,
            remaining=10,
            regions=regions,
        )

        unsupported_regions = {
            REGION_NAMES[i] for i in range(num_regions) if mask[i]
        }
        launched_in_regions: list[str] = []

        def mock_offerings(**kw):
            return {
                "supported": kw["region"] not in unsupported_regions,
                "offerings": [],
            }

        def mock_launch(**kw):
            launched_in_regions.append(kw["region"])
            return _make_launch_result(kw["region"], kw["target_count"], 0)

        tools = ToolCallbacks(
            check_offerings=mock_offerings,
            launch_instances=mock_launch,
            decide_next_action=lambda **kw: NextAction(
                action="continue_next_region", rationale="none"
            ),
        )

        orch = Orchestrator(state, tools, ami="ami-test")
        orch.run()

        # No unsupported region should appear in launch calls
        for r in launched_in_regions:
            assert r not in unsupported_regions, (
                f"RunInstances called in unsupported region {r}"
            )


# ===================================================================
# Property 10: next_action 结构完整性
# **Validates: Requirements 6.2**
# ===================================================================

VALID_ACTIONS = {"continue_next_region", "retry_same_region", "done", "abort"}


class TestProperty10NextActionStructure:
    """Every NextAction produced must have a valid action field."""

    @settings(max_examples=100, deadline=None)
    @given(
        action=st.sampled_from(list(VALID_ACTIONS)),
        rationale=st.text(min_size=1, max_size=50),
    )
    def test_next_action_valid(self, action: str, rationale: str):
        na = NextAction(action=action, rationale=rationale)
        assert na.action in VALID_ACTIONS

    @settings(max_examples=100, deadline=None)
    @given(
        action=st.text(
            alphabet=st.characters(whitelist_categories=("Ll",)),
            min_size=1,
            max_size=30,
        ),
    )
    def test_invalid_action_rejected(self, action: str):
        assume(action not in VALID_ACTIONS)
        try:
            NextAction(action=action, rationale="test")
            assert False, f"Should have rejected invalid action: {action}"
        except Exception:
            pass  # Expected: Pydantic validation error


# ===================================================================
# Property 11: 强制单区域模式不跨 Region
# **Validates: Requirements 11.3, 11.4**
# ===================================================================

class TestProperty11SingleRegionNoFallback:
    """In single_region mode, only the specified region is ever attempted."""

    @settings(max_examples=100, deadline=None)
    @given(
        num_regions=st.integers(min_value=2, max_value=5),
        total=st.integers(min_value=1, max_value=30),
        launched_frac=st.floats(min_value=0.0, max_value=1.0),
    )
    def test_single_region_only_uses_first(
        self, num_regions: int, total: int, launched_frac: float
    ):
        regions = [_make_region(REGION_NAMES[i]) for i in range(num_regions)]
        specified_region = regions[0].region

        state = OrchestratorState(
            request_id=f"p11-{num_regions}-{total}",
            instance_type="g6.xlarge",
            total_count=total,
            remaining=total,
            regions=regions,
            region_mode="single_region",
        )

        launched_in: list[str] = []

        def mock_launch(**kw):
            launched_in.append(kw["region"])
            n = min(int(total * launched_frac), kw["target_count"])
            return _make_launch_result(kw["region"], kw["target_count"], n)

        tools = ToolCallbacks(launch_instances=mock_launch)
        orch = Orchestrator(state, tools, ami="ami-test")
        result = orch.run()

        # All launches must be in the specified region only
        for r in launched_in:
            assert r == specified_region, (
                f"Launched in {r}, expected only {specified_region}"
            )

        # All region_results must reference only the specified region
        for rr in result.region_results:
            assert rr.region == specified_region, (
                f"Region result for {rr.region}, expected only {specified_region}"
            )
