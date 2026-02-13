"""Tests for src/tools/region_order.py – proximity-based ordering."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from src.tools.region_order import get_region_order, _haversine_km


@pytest.fixture()
def config_file(tmp_path: Path) -> Path:
    content = textwrap.dedent("""\
        regions:
          - region: us-east-1
            priority: 3
            azs: []
          - region: ap-south-1
            priority: 1
            azs: []
          - region: ap-southeast-1
            priority: 2
            azs: []
          - region: ap-northeast-1
            priority: 4
            azs: []
    """)
    p = tmp_path / "regions.yaml"
    p.write_text(content)
    return p


def test_primary_region_is_first(config_file: Path):
    result = get_region_order(
        primary_region="ap-southeast-1",
        config_path=str(config_file),
    )
    assert result[0] == "ap-southeast-1"


def test_sorted_by_proximity_from_singapore(config_file: Path):
    """From Singapore, Mumbai is closer than Tokyo, and Tokyo closer than Virginia."""
    result = get_region_order(
        primary_region="ap-southeast-1",
        config_path=str(config_file),
    )
    assert result[0] == "ap-southeast-1"
    # ap-south-1 (Mumbai ~4k km) < ap-northeast-1 (Tokyo ~5.3k km) < us-east-1 (~15k km)
    assert result.index("ap-south-1") < result.index("us-east-1")
    assert result.index("ap-northeast-1") < result.index("us-east-1")


def test_region_not_in_whitelist_excluded(config_file: Path):
    """eu-west-1 is not in the config, so it must not appear."""
    result = get_region_order(
        primary_region="ap-southeast-1",
        config_path=str(config_file),
    )
    assert "eu-west-1" not in result


def test_primary_not_in_whitelist(config_file: Path):
    """If primary_region isn't whitelisted, it should not appear in results."""
    result = get_region_order(
        primary_region="eu-west-1",
        config_path=str(config_file),
    )
    assert "eu-west-1" not in result
    # Should still return the whitelisted regions sorted by distance to eu-west-1
    assert len(result) == 4


def test_haversine_sanity():
    """Singapore to Mumbai should be roughly 4000 km."""
    d = _haversine_km(1.35, 103.82, 19.08, 72.88)
    assert 3500 < d < 4500
