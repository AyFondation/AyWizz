# =============================================================================
# File: __init__.py
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/intake/__init__.py
# Version: 1
# Description: Intake of externally-supplied requirements and their splitting
#              into interval-anchored fragments.
#              310-SPEC-DOC-TRACEABILITY §4.4 / §4.5.
#
# @relation implements:R-310-092
# =============================================================================

from .intervals import (
    Gap,
    IntervalError,
    Overlap,
    PartitionReport,
    TextInterval,
    covered_text,
    validate_partition,
)

__all__ = [
    "Gap",
    "IntervalError",
    "Overlap",
    "PartitionReport",
    "TextInterval",
    "covered_text",
    "validate_partition",
]
