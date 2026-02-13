"""Property-based tests for ec2_query_instances region scoping.

Property 2: Region 作用域正确性
For any query, when region is specified, all returned instances have az
belonging to that region; when region is not specified, results may include
instances from all candidate regions in the configuration.

Feature: instance-query-and-deletion, Property 2
Validates: Requirements 1.3, 1.4
"""

from __future__ import annotations

import os
import tempfile

import boto3
import yaml
from hypothesis import given, settings, assume
from hypothesis import strategies as st
from moto import mock_aws

from src.tools.query import ec2_query_instances

# ---------------------------------------------------------------------------
# Constants & strategies
# ---------------------------------------------------------------------------

# Regions that moto supports well for EC2
CANDIDATE_REGIONS = ["us-east-1", "us-west-2", "eu-west-1"]

_instance_type_pool = ["g5.xlarge", "g5.2xlarge", "t2.micro"]
_instance_type_st = st.sampled_from(_instance_type_pool)


def _write_regions_yaml(regions: list[str]) -> str:
    """Write a temp regions.yaml with the given region names."""
    data = {
        "regions": [
            {"region": r, "priority": i + 1, "azs": []}
            for i, r in enumerate(regions)
        ]
    }
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    yaml.dump(data, tmp)
    tmp.close()
    return tmp.name


def _create_instances_in_region(region: str, instance_type: str, count: int):
    """Create instances in a region via moto, return instance IDs."""
    ec2 = boto3.client("ec2", region_name=region)
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
    subnet = ec2.create_subnet(
        VpcId=vpc["Vpc"]["VpcId"],
        CidrBlock="10.0.1.0/24",
        AvailabilityZone=f"{region}a",
    )
    resp = ec2.run_instances(
        ImageId="ami-12345678",
        InstanceType=instance_type,
        MinCount=count,
        MaxCount=count,
        SubnetId=subnet["Subnet"]["SubnetId"],
    )
    return [i["InstanceId"] for i in resp["Instances"]]


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

@st.composite
def region_scope_scenario(draw):
    """Generate a scenario with instances across multiple regions.

    Returns dict with:
      - regions_with_instances: list of (region, instance_type, count)
      - specify_region: bool — whether to pass an explicit region filter
      - target_region: the region to filter on (if specify_region is True)
      - query_instance_type: instance type to filter on
    """
    # Pick 1-3 regions to populate with instances
    n_regions = draw(st.integers(min_value=1, max_value=len(CANDIDATE_REGIONS)))
    chosen_regions = draw(
        st.permutations(CANDIDATE_REGIONS).map(lambda p: list(p[:n_regions]))
    )

    itype = draw(_instance_type_st)

    regions_with_instances = []
    for r in chosen_regions:
        count = draw(st.integers(min_value=1, max_value=3))
        regions_with_instances.append((r, itype, count))

    specify_region = draw(st.booleans())
    target_region = draw(st.sampled_from(chosen_regions)) if specify_region else None

    return {
        "regions_with_instances": regions_with_instances,
        "specify_region": specify_region,
        "target_region": target_region,
        "query_instance_type": itype,
    }


# ---------------------------------------------------------------------------
# Property 2: Region 作用域正确性
# Feature: instance-query-and-deletion, Property 2
# Validates: Requirements 1.3, 1.4
# ---------------------------------------------------------------------------

class TestProperty2RegionScope:
    """All returned instances respect the region scope of the query."""

    @settings(max_examples=100, deadline=None)
    @given(scenario=region_scope_scenario())
    @mock_aws
    def test_region_scoping(self, scenario):
        """**Validates: Requirements 1.3, 1.4**

        When region is specified, every returned instance's az starts with
        that region. When region is omitted, instances from any candidate
        region in the config may appear.
        """
        # Setup: create instances across regions
        all_regions = []
        for region, itype, count in scenario["regions_with_instances"]:
            _create_instances_in_region(region, itype, count)
            all_regions.append(region)

        itype = scenario["query_instance_type"]
        specify_region = scenario["specify_region"]
        target_region = scenario["target_region"]

        if specify_region:
            # --- Case 1: region specified ---
            result = ec2_query_instances(
                region=target_region,
                instance_type=itype,
                state="running",
            )
            for inst in result["instances"]:
                assert inst["az"].startswith(target_region), (
                    f"Instance {inst['instance_id']} az={inst['az']} "
                    f"does not belong to region {target_region}"
                )
        else:
            # --- Case 2: no region specified, multi-region query ---
            config_path = _write_regions_yaml(all_regions)
            try:
                result = ec2_query_instances(
                    instance_type=itype,
                    state="running",
                    config_path=config_path,
                )
                # Every returned az must belong to one of the candidate regions
                for inst in result["instances"]:
                    matched = any(
                        inst["az"].startswith(r) for r in all_regions
                    )
                    assert matched, (
                        f"Instance {inst['instance_id']} az={inst['az']} "
                        f"does not belong to any candidate region {all_regions}"
                    )

                # Total count should cover instances from all regions
                expected_total = sum(
                    c for _, _, c in scenario["regions_with_instances"]
                )
                assert result["total_count"] == expected_total, (
                    f"Expected {expected_total} instances across all regions, "
                    f"got {result['total_count']}"
                )
            finally:
                os.unlink(config_path)
