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

from src.models.schemas import AZConfig, FallbackGroup, RegionConfig


class ConfigLoader:
    """Loads and manages candidate Region configuration.

    Supports two configuration sources:
    - Local YAML file (default for development / testing)
    - AWS SSM Parameter Store (for deployed environments)
    """

    def __init__(
        self,
        regions: Optional[List[RegionConfig]] = None,
        fallback_groups: Optional[List[FallbackGroup]] = None,
    ) -> None:
        self._regions: List[RegionConfig] = regions or []
        self._fallback_groups: List[FallbackGroup] = fallback_groups or []
        # Build lookup: consumer_region → allowed fallback regions
        self._consumer_to_fallback: dict[str, List[str]] = {}
        for group in self._fallback_groups:
            for cr in group.consumer_regions:
                self._consumer_to_fallback[cr] = group.fallback_regions

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
        fallback_groups = _parse_fallback_groups(raw.get("fallback_groups", {}))
        return cls(regions=regions, fallback_groups=fallback_groups)

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
        if isinstance(raw, list):
            region_list = raw
            fallback_raw = {}
        else:
            region_list = raw.get("regions", [])
            fallback_raw = raw.get("fallback_groups", {})
        regions = _parse_region_list(region_list)
        fallback_groups = _parse_fallback_groups(fallback_raw)
        return cls(regions=regions, fallback_groups=fallback_groups)

    @property
    def regions(self) -> List[RegionConfig]:
        """Raw (unsorted) region list."""
        return list(self._regions)

    @property
    def fallback_groups(self) -> List[FallbackGroup]:
        """All configured fallback groups."""
        return list(self._fallback_groups)

    @property
    def all_consumer_regions(self) -> set[str]:
        """Set of all consumer_regions across all fallback groups."""
        return set(self._consumer_to_fallback.keys())

    def is_consumer_region_allowed(self, consumer_region: str) -> bool:
        """Check if a consumer_region is covered by any fallback group.

        Returns True if the region is in a fallback group, False otherwise.
        When no fallback_groups are configured, all regions are allowed
        (backward compatibility).
        """
        if not self._fallback_groups:
            return True
        return consumer_region in self._consumer_to_fallback

    def get_allowed_regions(self, consumer_region: str) -> Optional[List[str]]:
        """Return the list of allowed fallback regions for a consumer_region.

        Returns None if no fallback_groups are configured (backward compat).
        Returns the fallback_regions list if the consumer_region is in a group.
        Raises ValueError if fallback_groups exist but consumer_region is not
        in any group (i.e. the request should be rejected).
        """
        if not self._fallback_groups:
            return None  # no restrictions
        if consumer_region not in self._consumer_to_fallback:
            raise ValueError(
                f"Region '{consumer_region}' is not in any configured "
                f"fallback group. Allowed consumer regions: "
                f"{sorted(self._consumer_to_fallback.keys())}. "
                f"Request rejected."
            )
        return self._consumer_to_fallback[consumer_region]

    def get_ordered_regions(
        self,
        consumer_region: str,
    ) -> List[RegionConfig]:
        """Return regions sorted by priority with *consumer_region* first.

        When fallback_groups are configured, only regions within the
        consumer_region's fallback group are returned. Regions outside
        the group are excluded.

        Sorting rules (Requirement 1.4, 8.3):
        1. ``consumer_region`` is always placed at index 0 (if in whitelist).
        2. Remaining regions are sorted by ascending ``priority`` value.
        3. Regions not present in the whitelist are silently ignored.
        """
        # Determine which regions are allowed
        allowed = self.get_allowed_regions(consumer_region)
        allowed_set = set(allowed) if allowed is not None else None

        consumer: Optional[RegionConfig] = None
        others: List[RegionConfig] = []

        for rc in self._regions:
            # Filter by fallback group if configured
            if allowed_set is not None and rc.region not in allowed_set:
                continue
            if rc.region == consumer_region:
                consumer = rc
            else:
                others.append(rc)

        others.sort(key=lambda r: r.priority)

        if consumer is not None:
            return [consumer] + others

        # consumer_region not in whitelist – return sorted list as-is
        return others

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


def _parse_fallback_groups(raw_groups: dict) -> List[FallbackGroup]:
    """Convert raw fallback_groups dict into FallbackGroup models.

    Expected structure::

        fallback_groups:
          southeast_asia:
            consumer_regions: [ap-southeast-1]
            fallback_regions: [ap-south-1, ap-northeast-1, ap-northeast-2]
          japan:
            consumer_regions: [ap-northeast-1, ap-northeast-3]
            fallback_regions: [ap-northeast-1, ap-northeast-3]
    """
    groups: List[FallbackGroup] = []
    if not raw_groups:
        return groups
    for _name, group_data in raw_groups.items():
        groups.append(
            FallbackGroup(
                consumer_regions=group_data.get("consumer_regions", []),
                fallback_regions=group_data.get("fallback_regions", []),
            )
        )
    return groups
