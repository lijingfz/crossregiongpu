"""Tests for src/orchestrator/executor.py."""

from __future__ import annotations

from src.models.schemas import (
    AZConfig,
    InstanceInfo,
    NextAction,
    RegionConfig,
    StepResult,
)
from src.orchestrator.executor import (
    ErrorCategory,
    Orchestrator,
    OrchestratorState,
    ToolCallbacks,
    classify_error,
    exponential_backoff,
    generate_client_token,
    generate_request_id,
    get_error_action,
)


# ---------------------------------------------------------------------------
# Error classification tests
# ---------------------------------------------------------------------------

class TestErrorClassification:
    def test_capacity_errors(self):
        assert classify_error("InsufficientInstanceCapacity") == ErrorCategory.CAPACITY

    def test_quota_errors(self):
        assert classify_error("VcpuLimitExceeded") == ErrorCategory.QUOTA
        assert classify_error("InstanceLimitExceeded") == ErrorCategory.QUOTA

    def test_config_errors(self):
        assert classify_error("InvalidSubnetID.NotFound") == ErrorCategory.CONFIG
        assert classify_error("InvalidAMIID.NotFound") == ErrorCategory.CONFIG

    def test_throttle_errors(self):
        assert classify_error("RequestLimitExceeded") == ErrorCategory.THROTTLE
        assert classify_error("Throttling") == ErrorCategory.THROTTLE

    def test_unknown_errors(self):
        assert classify_error("SomeRandomError") == ErrorCategory.UNKNOWN
        assert classify_error(None) == ErrorCategory.UNKNOWN

    def test_error_action_throttle_retries(self):
        action = get_error_action("RequestLimitExceeded")
        assert action.should_retry is True
        assert action.max_retries > 0

    def test_error_action_capacity_no_retry(self):
        action = get_error_action("InsufficientInstanceCapacity")
        assert action.should_retry is False
        assert action.skip_region is True


# ---------------------------------------------------------------------------
# Exponential backoff tests
# ---------------------------------------------------------------------------

class TestExponentialBackoff:
    def test_first_attempt(self):
        assert exponential_backoff(0, base=1.0) == 1.0

    def test_second_attempt(self):
        assert exponential_backoff(1, base=1.0) == 2.0

    def test_cap(self):
        assert exponential_backoff(10, base=1.0, cap=30.0) == 30.0


# ---------------------------------------------------------------------------
# Idempotency tests
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_request_id_unique(self):
        ids = {generate_request_id() for _ in range(100)}
        assert len(ids) == 100

    def test_client_token_unique(self):
        tokens = {
            generate_client_token("req1", "us-east-1", 0, i)
            for i in range(100)
        }
        assert len(tokens) == 100

    def test_client_token_contains_context(self):
        t = generate_client_token("req42", "ap-south-1", 2, 5)
        assert "req42" in t
        assert "ap-south-1" in t

    def test_duplicate_request_id_rejected(self):
        regions = [_make_region("us-east-1")]
        state = OrchestratorState(
            request_id="dup-123",
            instance_type="g6.xlarge",
            total_count=2,
            remaining=2,
            regions=regions,
        )
        tools = ToolCallbacks()
        Orchestrator(state, tools, ami="ami-test")

        # Second run with same request_id should raise
        state2 = OrchestratorState(
            request_id="dup-123",
            instance_type="g6.xlarge",
            total_count=2,
            remaining=2,
            regions=regions,
            seen_request_ids=state.seen_request_ids,
        )
        try:
            Orchestrator(state2, tools, ami="ami-test")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Duplicate request_id" in str(e)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_region(name: str, subnets: list | None = None) -> RegionConfig:
    if subnets is None:
        subnets = [f"subnet-{name}-a1"]
    return RegionConfig(
        region=name,
        priority=1,
        azs=[AZConfig(az_name=f"{name}a", subnets=subnets)],
    )


def _make_launch_result(
    region: str,
    requested: int,
    launched: int,
    status: str | None = None,
    error_code: str | None = None,
) -> dict:
    remaining = requested - launched
    if status is None:
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
            private_ip=f"10.0.0.{i+1}",
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


# ---------------------------------------------------------------------------
# Orchestrator state machine tests
# ---------------------------------------------------------------------------

class TestOrchestratorFullSatisfaction:
    """All instances launched in the first region → SUCCESS."""

    def test_full_in_first_region(self):
        regions = [_make_region("us-east-1"), _make_region("us-west-2")]
        state = OrchestratorState(
            request_id="full-001",
            instance_type="g6.xlarge",
            total_count=4,
            remaining=4,
            regions=regions,
        )

        def mock_launch(**kw):
            return _make_launch_result(kw["region"], kw["target_count"], kw["target_count"])

        tools = ToolCallbacks(
            launch_instances=mock_launch,
            decide_next_action=lambda **kw: NextAction(action="done", rationale="all done"),
        )

        orch = Orchestrator(state, tools, ami="ami-test")
        result = orch.run()

        assert result.status == "SUCCESS"
        assert result.total_launched == 4
        assert result.remaining == 0
        assert len(result.all_instances) == 4


class TestOrchestratorCrossRegionFallback:
    """First region partial, second region completes the rest."""

    def test_partial_then_full(self):
        regions = [_make_region("us-east-1"), _make_region("us-west-2")]
        state = OrchestratorState(
            request_id="partial-001",
            instance_type="g6.xlarge",
            total_count=6,
            remaining=6,
            regions=regions,
        )

        call_count = {"n": 0}

        def mock_launch(**kw):
            call_count["n"] += 1
            if kw["region"] == "us-east-1":
                return _make_launch_result("us-east-1", kw["target_count"], 2)
            else:
                return _make_launch_result("us-west-2", kw["target_count"], kw["target_count"])

        tools = ToolCallbacks(
            launch_instances=mock_launch,
            decide_next_action=lambda **kw: NextAction(
                action="continue_next_region", rationale="partial"
            ),
        )

        orch = Orchestrator(state, tools, ami="ami-test")
        result = orch.run()

        assert result.status == "SUCCESS"
        assert result.total_launched == 6


class TestOrchestratorAllRegionsFail:
    """All regions return NONE → FAILED."""

    def test_all_none(self):
        regions = [_make_region("us-east-1"), _make_region("us-west-2")]
        state = OrchestratorState(
            request_id="fail-001",
            instance_type="g6.xlarge",
            total_count=4,
            remaining=4,
            regions=regions,
        )

        tools = ToolCallbacks(
            launch_instances=lambda **kw: _make_launch_result(kw["region"], kw["target_count"], 0),
            decide_next_action=lambda **kw: NextAction(
                action="continue_next_region", rationale="none"
            ),
        )

        orch = Orchestrator(state, tools, ami="ami-test")
        result = orch.run()

        assert result.status == "FAILED"
        assert result.total_launched == 0
        assert result.remaining == 4


class TestOrchestratorPreflightSkip:
    """Region not offering instance type gets skipped."""

    def test_not_offered_skipped(self):
        regions = [_make_region("us-east-1"), _make_region("us-west-2")]
        state = OrchestratorState(
            request_id="skip-001",
            instance_type="g6.xlarge",
            total_count=2,
            remaining=2,
            regions=regions,
        )

        def mock_offerings(**kw):
            if kw["region"] == "us-east-1":
                return {"supported": False, "offerings": []}
            return {"supported": True, "offerings": [kw["region"]]}

        tools = ToolCallbacks(
            check_offerings=mock_offerings,
            launch_instances=lambda **kw: _make_launch_result(kw["region"], kw["target_count"], kw["target_count"]),
        )

        orch = Orchestrator(state, tools, ami="ami-test")
        result = orch.run()

        assert result.status == "SUCCESS"
        # First region was skipped, second fulfilled
        assert any(r.error_code == "NOT_OFFERED" for r in result.region_results)
        assert result.total_launched == 2


class TestOrchestratorAbort:
    """Agent decides to abort → stops immediately."""

    def test_abort_decision(self):
        regions = [_make_region("us-east-1"), _make_region("us-west-2")]
        state = OrchestratorState(
            request_id="abort-001",
            instance_type="g6.xlarge",
            total_count=4,
            remaining=4,
            regions=regions,
        )

        tools = ToolCallbacks(
            launch_instances=lambda **kw: _make_launch_result(kw["region"], kw["target_count"], 0),
            decide_next_action=lambda **kw: NextAction(
                action="abort", rationale="giving up", abort_reason="no capacity anywhere"
            ),
        )

        orch = Orchestrator(state, tools, ami="ami-test")
        result = orch.run()

        assert result.status == "FAILED"
        assert result.decision_trace[-1].action == "abort"


class TestOrchestratorDDBWrite:
    """Verify DynamoDB write is called for launched instances."""

    def test_ddb_called_on_success(self):
        regions = [_make_region("us-east-1")]
        state = OrchestratorState(
            request_id="ddb-001",
            instance_type="g6.xlarge",
            total_count=2,
            remaining=2,
            regions=regions,
        )

        ddb_calls = []

        def mock_put(**kw):
            ddb_calls.append(kw)
            return {"written": len(kw.get("instances", [])), "errors": []}

        tools = ToolCallbacks(
            launch_instances=lambda **kw: _make_launch_result(kw["region"], kw["target_count"], kw["target_count"]),
            put_instances=mock_put,
        )

        orch = Orchestrator(state, tools, ami="ami-test")
        orch.run()

        assert len(ddb_calls) == 1
        assert ddb_calls[0]["request_id"] == "ddb-001"
        assert len(ddb_calls[0]["instances"]) == 2


class TestRemainingMonotonicDecrease:
    """remaining should only decrease or stay the same, never increase."""

    def test_remaining_never_increases(self):
        regions = [_make_region("r1"), _make_region("r2"), _make_region("r3")]
        state = OrchestratorState(
            request_id="mono-001",
            instance_type="g6.xlarge",
            total_count=10,
            remaining=10,
            regions=regions,
        )

        launch_seq = iter([3, 2, 5])

        def mock_launch(**kw):
            n = next(launch_seq, 0)
            return _make_launch_result(kw["region"], kw["target_count"], n)

        tools = ToolCallbacks(
            launch_instances=mock_launch,
            decide_next_action=lambda **kw: NextAction(
                action="continue_next_region", rationale="partial"
            ),
        )

        orch = Orchestrator(state, tools, ami="ami-test")
        result = orch.run()

        # Check remaining values from step results are monotonically non-increasing
        remaining_values = [r.remaining for r in result.region_results]
        for i in range(1, len(remaining_values)):
            assert remaining_values[i] <= remaining_values[i - 1]


# ---------------------------------------------------------------------------
# Region mode tests  (Requirements 11.1, 11.3, 11.4)
# ---------------------------------------------------------------------------

class TestSingleRegionOnlyLaunchesInSpecifiedRegion:
    """single_region mode must only attempt the specified region."""

    def test_single_region_only_one_region(self):
        regions = [_make_region("ap-northeast-1"), _make_region("us-east-1")]
        state = OrchestratorState(
            request_id="sr-001",
            instance_type="g6.xlarge",
            total_count=4,
            remaining=4,
            regions=regions,
            region_mode="single_region",
        )

        launched_regions = []

        def mock_launch(**kw):
            launched_regions.append(kw["region"])
            return _make_launch_result(kw["region"], kw["target_count"], kw["target_count"])

        tools = ToolCallbacks(launch_instances=mock_launch)
        orch = Orchestrator(state, tools, ami="ami-test")
        result = orch.run()

        assert result.status == "SUCCESS"
        assert launched_regions == ["ap-northeast-1"]
        assert result.total_launched == 4


class TestSingleRegionPartialReturnsFailed:
    """single_region mode with insufficient capacity returns PARTIAL, not fallback."""

    def test_single_region_partial(self):
        regions = [_make_region("ap-northeast-1"), _make_region("us-east-1")]
        state = OrchestratorState(
            request_id="sr-002",
            instance_type="g6.xlarge",
            total_count=6,
            remaining=6,
            regions=regions,
            region_mode="single_region",
        )

        launched_regions = []

        def mock_launch(**kw):
            launched_regions.append(kw["region"])
            # Only launch 2 out of 6
            return _make_launch_result(kw["region"], kw["target_count"], 2)

        tools = ToolCallbacks(launch_instances=mock_launch)
        orch = Orchestrator(state, tools, ami="ami-test")
        result = orch.run()

        assert result.status == "PARTIAL"
        assert result.total_launched == 2
        assert result.remaining == 4
        assert result.single_region_constrained is True
        # Must NOT have tried the second region
        assert launched_regions == ["ap-northeast-1"]


class TestSingleRegionNoneReturnsFailed:
    """single_region mode with zero capacity returns FAILED."""

    def test_single_region_none(self):
        regions = [_make_region("ap-northeast-1"), _make_region("us-east-1")]
        state = OrchestratorState(
            request_id="sr-003",
            instance_type="g6.xlarge",
            total_count=4,
            remaining=4,
            regions=regions,
            region_mode="single_region",
        )

        launched_regions = []

        def mock_launch(**kw):
            launched_regions.append(kw["region"])
            return _make_launch_result(kw["region"], kw["target_count"], 0)

        tools = ToolCallbacks(launch_instances=mock_launch)
        orch = Orchestrator(state, tools, ami="ami-test")
        result = orch.run()

        assert result.status == "FAILED"
        assert result.total_launched == 0
        assert result.single_region_constrained is True
        assert launched_regions == ["ap-northeast-1"]


class TestMultiRegionRegressionUnchanged:
    """multi_region mode (default) still falls back across regions."""

    def test_multi_region_fallback_still_works(self):
        regions = [_make_region("us-east-1"), _make_region("us-west-2")]
        state = OrchestratorState(
            request_id="mr-001",
            instance_type="g6.xlarge",
            total_count=6,
            remaining=6,
            regions=regions,
            region_mode="multi_region",
        )

        def mock_launch(**kw):
            if kw["region"] == "us-east-1":
                return _make_launch_result("us-east-1", kw["target_count"], 2)
            return _make_launch_result("us-west-2", kw["target_count"], kw["target_count"])

        tools = ToolCallbacks(
            launch_instances=mock_launch,
            decide_next_action=lambda **kw: NextAction(
                action="continue_next_region", rationale="partial"
            ),
        )

        orch = Orchestrator(state, tools, ami="ami-test")
        result = orch.run()

        assert result.status == "SUCCESS"
        assert result.total_launched == 6
        assert result.single_region_constrained is False
