"""Property-based test for confirmation prompt completeness.

Property 4: 确认提示完整性
For any non-empty list of instances to be deleted, the generated
Confirmation_Prompt string contains every instance's instance_id.

Feature: instance-query-and-deletion, Property 4
Validates: Requirements 2.2
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from src.tools.delete import _build_confirmation_prompt

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_instance_id_st = st.from_regex(r"i-[0-9a-f]{17}", fullmatch=True)

_instance_type_st = st.sampled_from([
    "g5.xlarge", "g5.2xlarge", "g6.xlarge", "g6e.2xlarge", "p4d.24xlarge",
])

_ip_st = st.tuples(
    st.integers(1, 254), st.integers(0, 255),
    st.integers(0, 255), st.integers(1, 254),
).map(lambda t: f"{t[0]}.{t[1]}.{t[2]}.{t[3]}")

_az_st = st.sampled_from([
    "us-east-1a", "us-west-2b", "ap-northeast-1c", "eu-west-1a",
])

_subnet_st = st.from_regex(r"subnet-[0-9a-f]{8}", fullmatch=True)

_instance_detail_st = st.fixed_dictionaries({
    "InstanceId": _instance_id_st,
    "InstanceType": _instance_type_st,
    "PrivateIpAddress": _ip_st,
    "SubnetId": _subnet_st,
    "Placement": st.fixed_dictionaries({"AvailabilityZone": _az_st}),
})


# ---------------------------------------------------------------------------
# Property 4: 确认提示完整性
# Feature: instance-query-and-deletion, Property 4
# Validates: Requirements 2.2
# ---------------------------------------------------------------------------

class TestProperty4ConfirmationPromptCompleteness:
    """The confirmation prompt must contain every instance_id from the input list."""

    @given(instances=st.lists(_instance_detail_st, min_size=1, max_size=20))
    @settings(max_examples=100)
    def test_prompt_contains_all_instance_ids(self, instances):
        """**Validates: Requirements 2.2**"""
        prompt = _build_confirmation_prompt(instances)
        for inst in instances:
            assert inst["InstanceId"] in prompt, (
                f"instance_id {inst['InstanceId']} not found in prompt"
            )
