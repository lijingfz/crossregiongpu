"""Property-based tests for FilterSet.is_empty().

Property 7: 空过滤条件拒绝
For any FilterSet, is_empty() returns True if and only if region,
instance_type, subnet_id, instance_ids, private_ips, and tags are
all None or empty. The state field (which has a default) does not
count toward emptiness.

Feature: instance-query-and-deletion, Property 7
Validates: Requirements 3.4
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from src.models.schemas import FilterSet

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_region_st = st.sampled_from([
    "us-east-1", "us-west-2", "ap-northeast-1", "eu-west-1",
])

_instance_type_st = st.sampled_from([
    "g5.xlarge", "g5.2xlarge", "g6.xlarge", "g6e.2xlarge",
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
    min_size=1,
    max_size=3,
)

# Strategy that always produces an empty FilterSet (all filter fields None/empty)
_empty_filter_st = st.builds(
    FilterSet,
    region=st.none(),
    instance_type=st.none(),
    subnet_id=st.none(),
    instance_ids=st.one_of(st.none(), st.just([])),
    private_ips=st.one_of(st.none(), st.just([])),
    state=_state_st,
    tags=st.one_of(st.none(), st.just({})),
)

# Strategy that always produces a non-empty FilterSet (at least one filter field set)
_nonempty_filter_st = st.builds(
    FilterSet,
    region=st.one_of(st.none(), _region_st),
    instance_type=st.one_of(st.none(), _instance_type_st),
    subnet_id=st.one_of(
        st.none(),
        st.from_regex(r"subnet-[0-9a-f]{8}", fullmatch=True),
    ),
    instance_ids=st.one_of(st.none(), st.lists(_instance_id_st, min_size=0, max_size=3)),
    private_ips=st.one_of(st.none(), st.lists(_ip_st, min_size=0, max_size=3)),
    state=_state_st,
    tags=st.one_of(st.none(), _tag_st, st.just({})),
).filter(lambda fs: not fs.is_empty())


# ---------------------------------------------------------------------------
# Property 7: 空过滤条件拒绝
# Feature: instance-query-and-deletion, Property 7
# Validates: Requirements 3.4
# ---------------------------------------------------------------------------

class TestProperty7FilterSetIsEmpty:
    """FilterSet.is_empty() correctly distinguishes empty from non-empty filters."""

    @given(fs=_empty_filter_st)
    @settings(max_examples=100)
    def test_empty_filter_is_empty(self, fs: FilterSet):
        """**Validates: Requirements 3.4**

        When all filter fields (region, instance_type, subnet_id,
        instance_ids, private_ips, tags) are None or empty,
        is_empty() must return True regardless of state value.
        """
        assert fs.is_empty() is True

    @given(fs=_nonempty_filter_st)
    @settings(max_examples=100)
    def test_nonempty_filter_is_not_empty(self, fs: FilterSet):
        """**Validates: Requirements 3.4**

        When at least one filter field has a meaningful value,
        is_empty() must return False.
        """
        assert fs.is_empty() is False
