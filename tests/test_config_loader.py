"""Tests for src/config/loader.py – ConfigLoader."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from src.config.loader import ConfigLoader


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture()
def yaml_config(tmp_path: Path) -> Path:
    content = textwrap.dedent("""\
        regions:
          - region: us-west-2
            priority: 3
            azs:
              - az_name: us-west-2a
                subnets: [subnet-w2a]
          - region: ap-south-1
            priority: 1
            azs:
              - az_name: ap-south-1a
                subnets: [subnet-s1a, subnet-s1b]
              - az_name: ap-south-1b
                subnets: [subnet-s1c]
          - region: eu-west-1
            priority: 2
            azs:
              - az_name: eu-west-1a
                subnets: [subnet-ew1a]
    """)
    p = tmp_path / "regions.yaml"
    p.write_text(content)
    return p


# ------------------------------------------------------------------
# YAML loading
# ------------------------------------------------------------------

class TestFromYaml:
    def test_loads_all_regions(self, yaml_config: Path):
        loader = ConfigLoader.from_yaml(yaml_config)
        assert len(loader.regions) == 3

    def test_region_names(self, yaml_config: Path):
        loader = ConfigLoader.from_yaml(yaml_config)
        names = {r.region for r in loader.regions}
        assert names == {"us-west-2", "ap-south-1", "eu-west-1"}

    def test_az_and_subnets_parsed(self, yaml_config: Path):
        loader = ConfigLoader.from_yaml(yaml_config)
        ap = next(r for r in loader.regions if r.region == "ap-south-1")
        assert len(ap.azs) == 2
        assert ap.azs[0].subnets == ["subnet-s1a", "subnet-s1b"]


# ------------------------------------------------------------------
# Priority ordering with consumer_region first
# ------------------------------------------------------------------

class TestGetOrderedRegions:
    def test_consumer_region_first(self, yaml_config: Path):
        loader = ConfigLoader.from_yaml(yaml_config)
        ordered = loader.get_ordered_regions("eu-west-1")
        assert ordered[0].region == "eu-west-1"

    def test_remaining_sorted_by_priority(self, yaml_config: Path):
        loader = ConfigLoader.from_yaml(yaml_config)
        ordered = loader.get_ordered_regions("eu-west-1")
        remaining = [r.region for r in ordered[1:]]
        # ap-south-1 (pri=1) before us-west-2 (pri=3)
        assert remaining == ["ap-south-1", "us-west-2"]

    def test_consumer_not_in_whitelist(self, yaml_config: Path):
        loader = ConfigLoader.from_yaml(yaml_config)
        ordered = loader.get_ordered_regions("sa-east-1")
        # consumer not found → just sorted by priority
        assert [r.region for r in ordered] == [
            "ap-south-1", "eu-west-1", "us-west-2"
        ]


# ------------------------------------------------------------------
# SSM loading (mocked)
# ------------------------------------------------------------------

class _FakeSSMClient:
    def __init__(self, value: str):
        self._value = value

    def get_parameter(self, Name: str, WithDecryption: bool = False):
        return {"Parameter": {"Value": self._value}}


class TestFromSSM:
    def test_loads_from_ssm_json(self):
        payload = json.dumps({
            "regions": [
                {"region": "us-east-1", "priority": 1, "azs": [
                    {"az_name": "us-east-1a", "subnets": ["subnet-e1a"]}
                ]}
            ]
        })
        client = _FakeSSMClient(payload)
        loader = ConfigLoader.from_ssm("/gpu/regions", ssm_client=client)
        assert len(loader.regions) == 1
        assert loader.regions[0].region == "us-east-1"

    def test_loads_bare_list_from_ssm(self):
        payload = json.dumps([
            {"region": "eu-central-1", "priority": 5, "azs": []}
        ])
        client = _FakeSSMClient(payload)
        loader = ConfigLoader.from_ssm("/gpu/regions", ssm_client=client)
        assert loader.regions[0].region == "eu-central-1"
