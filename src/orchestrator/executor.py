"""Orchestrator: Probe-and-Fill state-machine executor.

Drives the main scheduling loop:
  Init → Preflight → Launch → Describe → DDB Write → Decide → Loop

Manages remaining/cursor state, error classification, exponential backoff,
and idempotency via request_id + client_token.

Requirements: 3.1-3.7, 4.2-4.6, 3.7 (idempotency)
"""

from __future__ import annotations

import enum
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from src.models.schemas import (
    InstanceInfo,
    NextAction,
    RegionConfig,
    StepResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error classification  (Requirement 4.5)
# ---------------------------------------------------------------------------

class ErrorCategory(str, enum.Enum):
    CAPACITY = "CAPACITY"
    QUOTA = "QUOTA"
    CONFIG = "CONFIG"
    THROTTLE = "THROTTLE"
    UNKNOWN = "UNKNOWN"


# Map AWS error codes → category
_ERROR_CODE_MAP: Dict[str, ErrorCategory] = {
    "InsufficientInstanceCapacity": ErrorCategory.CAPACITY,
    "InstanceLimitExceeded": ErrorCategory.QUOTA,
    "VcpuLimitExceeded": ErrorCategory.QUOTA,
    "MaxSpotInstanceCountExceeded": ErrorCategory.QUOTA,
    "InvalidSubnetID.NotFound": ErrorCategory.CONFIG,
    "InvalidSubnet": ErrorCategory.CONFIG,
    "InvalidAMIID.NotFound": ErrorCategory.CONFIG,
    "InvalidAMIID.Malformed": ErrorCategory.CONFIG,
    "InvalidParameterValue": ErrorCategory.CONFIG,
    "RequestLimitExceeded": ErrorCategory.THROTTLE,
    "Throttling": ErrorCategory.THROTTLE,
}


def classify_error(error_code: Optional[str]) -> ErrorCategory:
    """Classify an AWS error code into a handling category."""
    if error_code is None:
        return ErrorCategory.UNKNOWN
    return _ERROR_CODE_MAP.get(error_code, ErrorCategory.UNKNOWN)


@dataclass
class ErrorAction:
    """What to do when a specific error category is encountered."""
    should_retry: bool = False
    max_retries: int = 0
    skip_region: bool = False
    abort: bool = False
    backoff_base: float = 1.0  # seconds


# Error handling strategy per category
ERROR_STRATEGIES: Dict[ErrorCategory, ErrorAction] = {
    ErrorCategory.CAPACITY: ErrorAction(
        should_retry=False, skip_region=True,
    ),
    ErrorCategory.QUOTA: ErrorAction(
        should_retry=False, skip_region=True,
    ),
    ErrorCategory.CONFIG: ErrorAction(
        should_retry=False, skip_region=True,
    ),
    ErrorCategory.THROTTLE: ErrorAction(
        should_retry=True, max_retries=3, backoff_base=2.0,
    ),
    ErrorCategory.UNKNOWN: ErrorAction(
        should_retry=True, max_retries=2, backoff_base=1.0, skip_region=True,
    ),
}


def get_error_action(error_code: Optional[str]) -> ErrorAction:
    """Return the handling strategy for a given error code."""
    category = classify_error(error_code)
    return ERROR_STRATEGIES[category]


def exponential_backoff(attempt: int, base: float = 1.0, cap: float = 30.0) -> float:
    """Compute backoff delay: min(base * 2^attempt, cap)."""
    return min(base * (2 ** attempt), cap)


# ---------------------------------------------------------------------------
# Idempotency helpers  (Requirement 3.7)
# ---------------------------------------------------------------------------

def generate_request_id() -> str:
    """Generate a unique request ID for a scheduling run."""
    return uuid.uuid4().hex[:16]


def generate_client_token(request_id: str, region: str, step: int, seq: int) -> str:
    """Generate a deterministic-prefix, unique client token.

    Format: {request_id}-{region}-step{step}-{seq}-{hash8}
    The trailing hash ensures uniqueness even if other fields collide.
    """
    raw = f"{request_id}-{region}-{step}-{seq}-{uuid.uuid4().hex}"
    suffix = hashlib.sha256(raw.encode()).hexdigest()[:8]
    return f"{request_id}-{region}-s{step}-{seq}-{suffix}"


# ---------------------------------------------------------------------------
# Orchestrator State  (Requirements 4.2-4.6, 10.1-10.4)
# ---------------------------------------------------------------------------

@dataclass
class OrchestratorState:
    """Mutable state for a single scheduling run."""

    request_id: str
    instance_type: str
    total_count: int
    remaining: int
    cursor: int = 0
    regions: List[RegionConfig] = field(default_factory=list)
    region_mode: str = "multi_region"  # "multi_region" | "single_region"
    results: List[StepResult] = field(default_factory=list)
    all_instances: List[InstanceInfo] = field(default_factory=list)
    decision_trace: List[NextAction] = field(default_factory=list)
    seen_request_ids: set = field(default_factory=set)

    def advance_cursor(self) -> None:
        """Move to the next region (cursor strictly increases → Req 4.6)."""
        self.cursor += 1

    @property
    def current_region(self) -> Optional[RegionConfig]:
        if 0 <= self.cursor < len(self.regions):
            return self.regions[self.cursor]
        return None

    @property
    def is_done(self) -> bool:
        return self.remaining <= 0

    @property
    def regions_exhausted(self) -> bool:
        return self.cursor >= len(self.regions)


# ---------------------------------------------------------------------------
# Tool call protocol  (callbacks so orchestrator is testable without AWS)
# ---------------------------------------------------------------------------

@dataclass
class ToolCallbacks:
    """Pluggable callbacks for each tool the orchestrator invokes.

    Each callback mirrors the signature of the corresponding @tool function
    but returns plain dicts/lists so the orchestrator stays decoupled from
    boto3 / Strands runtime.
    """

    check_offerings: Callable[..., dict] = lambda **kw: {"supported": True, "offerings": []}
    launch_instances: Callable[..., dict] = lambda **kw: StepResult(
        status="NONE", requested=0, launched=0, remaining=0, region="", message=""
    ).model_dump()
    describe_instances: Callable[..., list] = lambda **kw: []
    put_instances: Callable[..., dict] = lambda **kw: {"written": 0, "errors": []}
    decide_next_action: Callable[..., NextAction] = lambda **kw: NextAction(
        action="continue_next_region", rationale="default"
    )


# ---------------------------------------------------------------------------
# Orchestrator executor  (Requirements 3.1-3.7, 4.2-4.6)
# ---------------------------------------------------------------------------

@dataclass
class ExecutionResult:
    """Final output of a complete orchestrator run."""

    status: str  # SUCCESS / PARTIAL / FAILED
    total_requested: int
    total_launched: int
    remaining: int
    region_results: List[StepResult]
    all_instances: List[InstanceInfo]
    decision_trace: List[NextAction]
    request_id: str
    single_region_constrained: bool = False


class Orchestrator:
    """Probe-and-Fill state-machine executor.

    Drives the loop: preflight → launch → describe → DDB write → decide → next.
    """

    def __init__(
        self,
        state: OrchestratorState,
        tools: ToolCallbacks,
        *,
        ami: str = "",
        security_group_ids: Optional[List[str]] = None,
        iam_profile: str = "",
        tags: Optional[Dict[str, str]] = None,
        ddb_table: str = "GpuProvisioningInstances",
        ddb_region: str = "us-east-1",
        goal_region: str = "",
        batch_max: int = 4,
        global_timeout: float = 3600.0,
    ) -> None:
        self.state = state
        self.tools = tools
        self.ami = ami
        self.security_group_ids = security_group_ids or []
        self.iam_profile = iam_profile
        self.tags = tags or {}
        self.ddb_table = ddb_table
        self.ddb_region = ddb_region
        self.goal_region = goal_region or (
            state.regions[0].region if state.regions else ""
        )
        self.batch_max = batch_max
        self.global_timeout = global_timeout

        # single_region mode: restrict regions to only the specified one
        if state.region_mode == "single_region" and state.regions:
            # Keep only the first region (the user-specified one)
            state.regions = state.regions[:1]

        # Idempotency: reject duplicate request_ids
        if state.request_id in state.seen_request_ids:
            raise ValueError(
                f"Duplicate request_id: {state.request_id}. "
                "Each scheduling run must use a unique request_id."
            )
        state.seen_request_ids.add(state.request_id)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> ExecutionResult:
        """Execute the full Probe-and-Fill scheduling loop."""
        start_time = time.monotonic()

        while not self.state.is_done and not self.state.regions_exhausted:
            # Timeout guard
            elapsed = time.monotonic() - start_time
            if elapsed >= self.global_timeout:
                logger.warning("Global timeout reached after %.1fs", elapsed)
                break

            region_cfg = self.state.current_region
            assert region_cfg is not None
            region = region_cfg.region

            # --- Step 1: Preflight offerings check ---
            offerings = self.tools.check_offerings(
                region=region,
                instance_type=self.state.instance_type,
            )
            if not offerings.get("supported", False):
                step = StepResult(
                    status="NONE",
                    requested=self.state.remaining,
                    launched=0,
                    remaining=self.state.remaining,
                    region=region,
                    error_code="NOT_OFFERED",
                    message=f"{self.state.instance_type} not offered in {region}",
                )
                self.state.results.append(step)
                self.state.advance_cursor()
                continue

            # Collect subnets from all AZs in this region config
            subnets = []
            for az in region_cfg.azs:
                subnets.extend(az.subnets)
            if not subnets:
                step = StepResult(
                    status="NONE",
                    requested=self.state.remaining,
                    launched=0,
                    remaining=self.state.remaining,
                    region=region,
                    error_code="NO_SUBNETS",
                    message=f"No subnets configured for {region}",
                )
                self.state.results.append(step)
                self.state.advance_cursor()
                continue

            # --- Step 2: Launch instances ---
            step_result = self._launch_with_retry(region, subnets, region_cfg)
            self.state.results.append(step_result)

            launched_instances = [
                InstanceInfo(**inst) if isinstance(inst, dict) else inst
                for inst in step_result.instances
            ]

            # --- Step 3: Describe instances (enrich IP/AZ) ---
            if launched_instances:
                instance_ids = [i.instance_id for i in launched_instances]
                enriched = self.tools.describe_instances(
                    region=region,
                    instance_ids=instance_ids,
                )
                # Merge enriched data back
                enriched_map = {
                    e["instance_id"]: e for e in enriched
                } if enriched else {}
                for inst in launched_instances:
                    if inst.instance_id in enriched_map:
                        e = enriched_map[inst.instance_id]
                        inst.private_ip = e.get("private_ip", inst.private_ip)
                        inst.public_ip = e.get("public_ip", inst.public_ip)
                        inst.az = e.get("az", inst.az)

            # --- Step 4: Write to DynamoDB ---
            if launched_instances:
                self.tools.put_instances(
                    table=self.ddb_table,
                    request_id=self.state.request_id,
                    goal_region=self.goal_region,
                    region=region,
                    instance_type=self.state.instance_type,
                    instances=[i.model_dump() for i in launched_instances],
                    step_id=f"step-{self.state.cursor}",
                    allocation_status=step_result.status,
                    dynamodb_region=self.ddb_region,
                )

            # Update state: remaining decreases (Req 4.2, 4.3 — monotonic)
            self.state.remaining = step_result.remaining
            self.state.all_instances.extend(launched_instances)

            # --- single_region mode: no cross-region fallback (Req 11.3, 11.4) ---
            if self.state.region_mode == "single_region" and not self.state.is_done:
                # In single_region mode, PARTIAL/NONE ends immediately
                decision = NextAction(
                    action="done",
                    rationale="single_region mode — no cross-region fallback",
                    final_summary=(
                        f"single_region constraint: launched "
                        f"{self.state.total_count - self.state.remaining}"
                        f"/{self.state.total_count} in {region}"
                    ),
                )
                self.state.decision_trace.append(decision)
                break

            # --- Step 5: Decide next action ---
            if self.state.is_done:
                decision = NextAction(
                    action="done",
                    rationale="All instances launched successfully",
                    final_summary=f"Launched {self.state.total_count} instances",
                )
            else:
                decision = self.tools.decide_next_action(
                    step_result=step_result,
                    remaining=self.state.remaining,
                )

            self.state.decision_trace.append(decision)

            if decision.action == "done":
                break
            elif decision.action == "abort":
                break
            elif decision.action == "continue_next_region":
                self.state.advance_cursor()
            elif decision.action == "retry_same_region":
                # Stay on same cursor — but the launch tool already retried
                # internally, so this is a higher-level retry (e.g. after throttle)
                pass

        return self._build_result()


    # ------------------------------------------------------------------
    # Internal: launch with error-category-based retry
    # ------------------------------------------------------------------

    def _launch_with_retry(self, region: str, subnets: List[str], region_cfg: RegionConfig) -> StepResult:
        """Launch instances with error-category-aware retry and backoff."""
        attempt = 0
        # Use per-region AMI/key if configured, fall back to orchestrator defaults
        ami = region_cfg.ami_id or self.ami
        key_name = region_cfg.key_name

        while True:
            raw = self.tools.launch_instances(
                region=region,
                instance_type=self.state.instance_type,
                target_count=self.state.remaining,
                subnets=subnets,
                ami=ami,
                security_group_ids=self.security_group_ids,
                iam_profile=self.iam_profile,
                tags=self.tags,
                batch_max=self.batch_max,
                request_id=self.state.request_id,
                key_name=key_name,
            )

            step = StepResult(**raw) if isinstance(raw, dict) else raw

            if step.status != "ERROR":
                return step

            # Classify the error and decide
            action = get_error_action(step.error_code)

            if action.should_retry and attempt < action.max_retries:
                delay = exponential_backoff(attempt, action.backoff_base)
                logger.info(
                    "Retrying %s after %.1fs (attempt %d/%d, error=%s)",
                    region, delay, attempt + 1, action.max_retries,
                    step.error_code,
                )
                time.sleep(delay)
                attempt += 1
                continue

            # No more retries — return the error step as-is
            return step

    # ------------------------------------------------------------------
    # Build final result
    # ------------------------------------------------------------------

    def _build_result(self) -> ExecutionResult:
        total_launched = len(self.state.all_instances)

        if total_launched >= self.state.total_count:
            status = "SUCCESS"
        elif total_launched > 0:
            status = "PARTIAL"
        else:
            status = "FAILED"

        # Flag if single_region constraint prevented full satisfaction
        single_region_constrained = (
            self.state.region_mode == "single_region"
            and self.state.remaining > 0
        )

        return ExecutionResult(
            status=status,
            total_requested=self.state.total_count,
            total_launched=total_launched,
            remaining=self.state.remaining,
            region_results=self.state.results,
            all_instances=self.state.all_instances,
            decision_trace=self.state.decision_trace,
            request_id=self.state.request_id,
            single_region_constrained=single_region_constrained,
        )
