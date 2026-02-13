"""Property-based tests for instance-query-and-deletion data models.

Property 6: 数据模型序列化往返一致性
For any valid FilterSet, InstanceSummary, or DeleteResult object,
serializing via model_dump() and deserializing back produces an
equivalent object.

Validates: Requirements 3.1, 3.2, 3.3
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from src.models.schemas import (
    DeleteResult,
    FilterSet,
    InstanceSummary,
    TerminatedInstance,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_region_st = st.sampled_from([
    "us-east-1", "us-west-2", "ap-northeast-1", "eu-west-1", "ap-southeast-1",
])

_instance_type_st = st.sampled_from([
    "g5.xlarge", "g5.2xlarge", "g6.xlarge", "g6e.2xlarge", "p4d.24xlarge",
])

_instance_id_st = st.from_regex(r"i-[0-9a-f]{17}", fullmatch=True)

_ip_st = st.tuples(
    st.integers(1, 254), st.integers(0, 255),
    st.integers(0, 255), st.integers(1, 254),
).map(lambda t: f"{t[0]}.{t[1]}.{t[2]}.{t[3]}")

_state_st = st.sampled_from(["running", "stopped", "terminated", "pending"])

_tag_st = st.dictionaries(
    keys=st.from_regex(r"[A-Za-z][A-Za-z0-9]{0,9}", fullmatch=True),
    values=st.text(min_size=1, max_size=20),
    min_size=0,
    max_size=3,
)

filter_set_st = st.builds(
    FilterSet,
    region=st.one_of(st.none(), _region_st),
    instance_type=st.one_of(st.none(), _instance_type_st),
    subnet_id=st.one_of(st.none(), st.from_regex(r"subnet-[0-9a-f]{8}", fullmatch=True)),
    instance_ids=st.one_of(st.none(), st.lists(_instance_id_st, min_size=0, max_size=5)),
    private_ips=st.one_of(st.none(), st.lists(_ip_st, min_size=0, max_size=5)),
    state=_state_st,
    tags=st.one_of(st.none(), _tag_st),
)

instance_summary_st = st.builds(
    InstanceSummary,
    instance_id=_instance_id_st,
    instance_type=_instance_type_st,
    private_ip=_ip_st,
    public_ip=st.one_of(st.none(), _ip_st),
    subnet_id=st.from_regex(r"subnet-[0-9a-f]{8}", fullmatch=True),
    az=_region_st.flatmap(lambda r: st.just(f"{r}a")),
    state=_state_st,
    launch_time=st.from_regex(r"2025-0[1-9]-[012][0-9]T[01][0-9]:00:00Z", fullmatch=True),
)

terminated_instance_st = st.builds(
    TerminatedInstance,
    instance_id=_instance_id_st,
    current_state=st.sampled_from(["shutting-down", "terminated"]),
)

delete_result_st = st.builds(
    DeleteResult,
    deleted_count=st.integers(min_value=0, max_value=20),
    terminated_instances=st.lists(terminated_instance_st, min_size=0, max_size=5),
    errors=st.lists(st.text(min_size=1, max_size=50), min_size=0, max_size=3),
)


# ---------------------------------------------------------------------------
# Property 6: 数据模型序列化往返一致性
# Feature: instance-query-and-deletion, Property 6
# Validates: Requirements 3.1, 3.2, 3.3
# ---------------------------------------------------------------------------

class TestProperty6SerializationRoundTrip:
    """model_dump() → Model(**data) produces an equivalent object."""

    @given(fs=filter_set_st)
    @settings(max_examples=100)
    def test_filter_set_round_trip(self, fs: FilterSet):
        """**Validates: Requirements 3.1**"""
        rebuilt = FilterSet(**fs.model_dump())
        assert rebuilt == fs

    @given(summary=instance_summary_st)
    @settings(max_examples=100)
    def test_instance_summary_round_trip(self, summary: InstanceSummary):
        """**Validates: Requirements 3.2**"""
        rebuilt = InstanceSummary(**summary.model_dump())
        assert rebuilt == summary

    @given(dr=delete_result_st)
    @settings(max_examples=100)
    def test_delete_result_round_trip(self, dr: DeleteResult):
        """**Validates: Requirements 3.3**"""
        rebuilt = DeleteResult(**dr.model_dump())
        assert rebuilt == dr
