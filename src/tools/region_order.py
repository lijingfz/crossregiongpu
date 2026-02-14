"""Tool: get_region_order – Return candidate Regions sorted by geographic proximity.

Reads the candidate Region whitelist from config (YAML or SSM), then sorts
them by great-circle distance to the primary_region. Regions not in the
whitelist are excluded.

Requirements: 8.1, 8.3
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, Optional, Tuple

from strands import tool

from src.config.loader import ConfigLoader

_DEFAULT_CONFIG_PATH = Path("config/regions.yaml")

# Approximate lat/lon for AWS Region endpoints.
# Based on the city each Region is named after per AWS official docs:
# https://docs.aws.amazon.com/global-infrastructure/latest/regions/aws-regions.html
REGION_COORDINATES: Dict[str, Tuple[float, float]] = {
    # Asia Pacific
    "ap-southeast-1": (1.35, 103.82),      # Singapore
    "ap-southeast-2": (-33.87, 151.21),     # Sydney, Australia
    "ap-southeast-3": (-6.21, 106.85),      # Jakarta, Indonesia
    "ap-southeast-4": (-37.81, 144.96),     # Melbourne, Australia
    "ap-southeast-5": (3.14, 101.69),       # Malaysia (Kuala Lumpur)
    "ap-southeast-6": (-36.85, 174.76),     # New Zealand (Auckland)
    "ap-southeast-7": (13.76, 100.50),      # Thailand (Bangkok)
    "ap-south-1": (19.08, 72.88),           # Mumbai, India
    "ap-south-2": (17.39, 78.49),           # Hyderabad, India
    "ap-northeast-1": (35.68, 139.69),      # Tokyo, Japan
    "ap-northeast-2": (37.57, 126.98),      # Seoul, South Korea
    "ap-northeast-3": (34.69, 135.50),      # Osaka, Japan
    "ap-east-1": (22.32, 114.17),           # Hong Kong
    "ap-east-2": (25.03, 121.57),           # Taipei, Taiwan
    # US
    "us-east-1": (39.05, -77.47),           # N. Virginia
    "us-east-2": (39.96, -83.00),           # Ohio
    "us-west-1": (37.35, -121.96),          # N. California
    "us-west-2": (45.59, -122.60),          # Oregon
    # Europe
    "eu-west-1": (53.35, -6.26),            # Ireland (Dublin)
    "eu-west-2": (51.51, -0.13),            # London, UK
    "eu-west-3": (48.86, 2.35),             # Paris, France
    "eu-central-1": (50.11, 8.68),          # Frankfurt, Germany
    "eu-central-2": (47.37, 8.54),          # Zurich, Switzerland
    "eu-north-1": (59.33, 18.07),           # Stockholm, Sweden
    "eu-south-1": (45.46, 9.19),            # Milan, Italy
    "eu-south-2": (40.42, -3.70),           # Spain (Madrid)
    # Middle East
    "me-south-1": (26.07, 50.56),           # Bahrain
    "me-central-1": (24.45, 54.65),         # UAE (Abu Dhabi)
    # Africa
    "af-south-1": (-33.93, 18.42),          # Cape Town, South Africa
    # South America
    "sa-east-1": (-23.55, -46.63),          # São Paulo, Brazil
    # Canada
    "ca-central-1": (45.50, -73.57),        # Montreal, Canada
    "ca-west-1": (51.04, -114.07),          # Calgary, Canada
    # Israel
    "il-central-1": (32.07, 34.78),         # Tel Aviv, Israel
    # Mexico
    "mx-central-1": (19.43, -99.13),        # Mexico City
}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in kilometres."""
    R = 6371.0  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


@tool
def get_region_order(
    primary_region: str,
    config_path: Optional[str] = None,
    ssm_parameter: Optional[str] = None,
) -> list:
    """Return candidate regions sorted by geographic distance to *primary_region*.

    Enforces geographic compliance via fallback_groups:
    1. If primary_region is not in any fallback group, returns an error dict
       (the request should be rejected).
    2. Only regions within the same fallback group are included.
    3. Within the allowed set, sorts by great-circle distance.
    4. primary_region is placed first (distance=0).
    """
    if ssm_parameter:
        loader = ConfigLoader.from_ssm(ssm_parameter)
    else:
        path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
        loader = ConfigLoader.from_yaml(path)

    # Enforce fallback group boundary
    try:
        allowed_regions = loader.get_allowed_regions(primary_region)
    except ValueError as exc:
        return {"error": str(exc), "allowed_consumer_regions": sorted(loader.all_consumer_regions)}

    allowed_set = set(allowed_regions) if allowed_regions is not None else None

    whitelist = {rc.region for rc in loader.regions}
    # Intersect with fallback group if configured
    if allowed_set is not None:
        whitelist = whitelist & allowed_set

    origin = REGION_COORDINATES.get(primary_region)

    if origin is None:
        # Unknown primary_region coords — fall back to config priority order
        ordered = loader.get_ordered_regions(primary_region)
        return [rc.region for rc in ordered]

    # Build (distance, region) pairs for every whitelisted region
    scored: list[tuple[float, str]] = []
    for region_name in whitelist:
        if region_name == primary_region:
            continue  # will be prepended
        coords = REGION_COORDINATES.get(region_name)
        if coords is None:
            scored.append((float("inf"), region_name))
        else:
            dist = _haversine_km(origin[0], origin[1], coords[0], coords[1])
            scored.append((dist, region_name))

    scored.sort(key=lambda x: x[0])

    result = []
    if primary_region in whitelist:
        result.append(primary_region)
    result.extend(r for _, r in scored)
    return result
