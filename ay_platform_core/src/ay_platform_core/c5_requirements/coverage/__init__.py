# =============================================================================
# File: __init__.py
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/coverage/__init__.py
# Version: 1
# Description: The traceability graph — allocations (requirement → container)
#              and coverage links (object → requirement or upstream object).
#              310-SPEC-DOC-TRACEABILITY §4.4 / §4.5 / §4.7.
#
# @relation implements:R-310-064
# =============================================================================

from .models import (
    Allocation,
    AllocationRejection,
    AllocationVerdict,
    ContainerCoverage,
    CoverageLink,
    CoverageStrength,
    RequirementCoverage,
    ReturnReason,
    is_valid_requirement_id,
)

__all__ = [
    "Allocation",
    "AllocationRejection",
    "AllocationVerdict",
    "ContainerCoverage",
    "CoverageLink",
    "CoverageStrength",
    "RequirementCoverage",
    "ReturnReason",
    "is_valid_requirement_id",
]
