"""Tool: finalize – Summarise an orchestrator run into a structured result.

Aggregates per-Region status, builds the gpu_list, collects errors,
and computes the gap between requested and launched instances.

Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6
"""

from __future__ import annotations

from typing import Any, Dict, List

from strands import tool

from src.models.schemas import InstanceInfo, StepResult


# ------------------------------------------------------------------
# Public tool
# ------------------------------------------------------------------

@tool
def finalize(
    total_requested: int,
    total_launched: int,
    region_results: List[Dict[str, Any]],
    all_instances: List[Dict[str, Any]],
    ddb_written: int = 0,
) -> dict:
    """Produce a structured execution summary.

    Parameters
    ----------
    total_requested : int
        Original target instance count.
    total_launched : int
        Total instances successfully launched across all regions.
    region_results : list[dict]
        List of per-step StepResult dicts (status, region, launched, …).
    all_instances : list[dict]
        Flat list of InstanceInfo dicts for every launched instance.
    ddb_written : int
        Number of records written to DynamoDB.

    Returns
    -------
    dict with keys:
        status, region_summary, total_requested, total_launched,
        gap, ddb_written, errors, gpu_list
    """
    # --- 7.1  overall status ---
    if total_launched >= total_requested:
        status = "SUCCESS"
    elif total_launched > 0:
        status = "PARTIAL"
    else:
        status = "FAILED"

    # --- 7.2  per-region summary ---
    region_summary = _build_region_summary(region_results)

    # --- 7.3  gap ---
    gap = max(total_requested - total_launched, 0)

    # --- 7.5  error list ---
    errors = _collect_errors(region_results)

    # --- 7.6  gpu_list ---
    gpu_list = _build_gpu_list(all_instances)

    return {
        "status": status,                   # 7.1
        "region_summary": region_summary,   # 7.2
        "total_requested": total_requested, # 7.3
        "total_launched": total_launched,    # 7.3
        "gap": gap,                         # 7.3
        "ddb_written": ddb_written,         # 7.4
        "errors": errors,                   # 7.5
        "gpu_list": gpu_list,               # 7.6
    }


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _build_region_summary(region_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Aggregate per-region FULL/PARTIAL/NONE status."""
    summary: List[Dict[str, Any]] = []
    for step in region_results:
        summary.append({
            "region": step.get("region", ""),
            "status": step.get("status", "NONE"),
            "requested": step.get("requested", 0),
            "launched": step.get("launched", 0),
            "error_code": step.get("error_code"),
            "message": step.get("message", ""),
        })
    return summary


def _collect_errors(region_results: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Extract error entries from step results."""
    errors: List[Dict[str, str]] = []
    for step in region_results:
        if step.get("status") in ("NONE", "ERROR") and step.get("error_code"):
            errors.append({
                "region": step.get("region", ""),
                "error_code": step.get("error_code", ""),
                "message": step.get("message", ""),
            })
    return errors


def _build_gpu_list(all_instances: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build the gpu_list array per Requirement 7.6."""
    gpu_list: List[Dict[str, Any]] = []
    for inst in all_instances:
        gpu_list.append({
            "instance_id": inst.get("instance_id", ""),
            "instance_type": inst.get("instance_type", ""),
            "region": inst.get("region", ""),
            "az": inst.get("az", ""),
            "private_ip": inst.get("private_ip", ""),
            "public_ip": inst.get("public_ip"),
        })
    return gpu_list
