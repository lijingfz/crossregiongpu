"""Unit tests for deployment configuration consistency.

Validates:
- requirements.txt stays in sync with pyproject.toml dependencies
- agent_entrypoint.py exists at project root

Requirements: 4.1, 4.2, 4.3
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _parse_requirements_txt() -> dict[str, str]:
    """Parse requirements.txt into {package_name: version_spec} mapping."""
    reqs: dict[str, str] = {}
    req_path = PROJECT_ROOT / "requirements.txt"
    for line in req_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([a-zA-Z0-9_-]+)(.*)", line)
        if match:
            reqs[match.group(1).lower()] = match.group(2).strip()
    return reqs


def _parse_pyproject_deps() -> dict[str, str]:
    """Parse pyproject.toml [project].dependencies into {package_name: version_spec}."""
    import tomllib

    pyproject_path = PROJECT_ROOT / "pyproject.toml"
    data = tomllib.loads(pyproject_path.read_text())
    deps: dict[str, str] = {}
    for dep in data.get("project", {}).get("dependencies", []):
        match = re.match(r"^([a-zA-Z0-9_-]+)(.*)", dep)
        if match:
            deps[match.group(1).lower()] = match.group(2).strip()
    return deps


class TestRequirementsTxt:
    """Verify requirements.txt consistency with pyproject.toml."""

    def test_requirements_txt_exists(self):
        assert (PROJECT_ROOT / "requirements.txt").is_file()

    def test_contains_all_pyproject_deps(self):
        """Every dependency in pyproject.toml must appear in requirements.txt."""
        pyproject_deps = _parse_pyproject_deps()
        req_deps = _parse_requirements_txt()
        for pkg, version_spec in pyproject_deps.items():
            assert pkg in req_deps, f"Missing from requirements.txt: {pkg}"
            assert req_deps[pkg] == version_spec, (
                f"Version mismatch for {pkg}: "
                f"pyproject.toml has '{version_spec}', "
                f"requirements.txt has '{req_deps[pkg]}'"
            )

    def test_contains_bedrock_agentcore(self):
        """requirements.txt must include bedrock-agentcore."""
        req_deps = _parse_requirements_txt()
        assert "bedrock-agentcore" in req_deps


class TestEntrypointLocation:
    """Verify agent_entrypoint.py is at project root."""

    def test_entrypoint_exists_at_root(self):
        assert (PROJECT_ROOT / "agent_entrypoint.py").is_file()
