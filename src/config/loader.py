"""Configuration loader for the GPU Cross-Region Dynamic Scheduler.

Loads Region/AZ/Subnet whitelists from YAML files or AWS SSM Parameter Store,
and provides Region priority ordering with consumer_region placed first.

Requirements: 8.1, 8.2, 8.3, 1.4
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

import yaml

from src.models.schemas import AZConfig, RegionConfig


class ConfigLoader:
    """Loads and manages candidate Region configuration.

    Supports two configuration sources:
    - Local YAML file (default for development / testing)
    - AWS SSM Parameter Store (for deployed environments)
    """

    def __init__(self, regions: Optional[List[RegionConfig]] = None) -> None:
        self._regions: List[RegionConfig] = regions or []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ConfigLoader":
        """Load region configuration from a YAML file.

        Expected YAML structure::

            regions:
              - region: ap-south-1
                priority: 1
                azs:
                  - az_name: ap-south-1a
                    subnets: [subnet-aaa, subnet-bbb]
                  - az_name: ap-south-1b
                    subnets: [subnet-ccc]
              - region: ap-northeast-1
                priority: 2
                azs:
                  - az_name: ap-northeast-1a
                    subnets: [subnet-ddd]
        """
        path = Path(path)
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)

        regions = _parse_region_list(raw.get("regions", []))
        return cls(regions=regions)

    @classmethod
    def from_ssm(
        cls,
        parameter_name: str,
        ssm_client=None,
    ) -> "ConfigLoader":
        """Load region configuration from AWS SSM Parameter Store.

        The parameter value must be a JSON string with the same schema
        as the YAML ``regions`` list.

        Requirements: 8.1, 8.4 (dynamic update without redeployment)
        """
        import boto3

        client = ssm_client or boto3.client("ssm")
        response = client.get_parameter(Name=parameter_name, WithDecryption=False)
        raw = json.loads(response["Parameter"]["Value"])

        # Accept both {"regions": [...]} and bare [...]
        region_list = raw if isinstance(raw, list) else raw.get("regions", [])
        regions = _parse_region_list(region_list)
        return cls(regions=regions)

    def get_ordered_regions(
        self,
        consumer_region: str,
    ) -> List[RegionConfig]:
        """Return regions sorted by priority with *consumer_region* first.

        Sorting rules (Requirement 1.4, 8.3):
        1. ``consumer_region`` is always placed at index 0.
        2. Remaining regions are sorted by ascending ``priority`` value.
        3. Regions not present in the whitelist are silently ignored.
        """
        consumer: Optional[RegionConfig] = None
        others: List[RegionConfig] = []

        for rc in self._regions:
            if rc.region == consumer_region:
                consumer = rc
            else:
                others.append(rc)

        others.sort(key=lambda r: r.priority)

        if consumer is not None:
            return [consumer] + others

        # consumer_region not in whitelist – return sorted list as-is
        return others

    @property
    def regions(self) -> List[RegionConfig]:
        """Raw (unsorted) region list."""
        return list(self._regions)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _parse_region_list(raw_list: list) -> List[RegionConfig]:
    """Convert raw dicts into validated RegionConfig models."""
    regions: List[RegionConfig] = []
    for item in raw_list:
        azs = [
            AZConfig(az_name=az["az_name"], subnets=az.get("subnets", []))
            for az in item.get("azs", [])
        ]
        regions.append(
            RegionConfig(
                region=item["region"],
                priority=item.get("priority", 0),
                azs=azs,
                key_name=item.get("key_name", ""),
                ami_id=item.get("ami_id", ""),
            )
        )
    return regions
