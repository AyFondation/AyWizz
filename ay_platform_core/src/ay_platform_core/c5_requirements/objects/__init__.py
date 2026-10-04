# =============================================================================
# File: __init__.py
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/objects/__init__.py
# Version: 1
# Description: Object-grain document model for 310-SPEC-DOC-TRACEABILITY.
#              Facade re-exporting the public contracts (§4.1 of 310-SPEC).
#              Lives inside C5 rather than as a new component: the
#              collections are C5-owned per R-100-012, and splitting one
#              corpus across two services would split its ownership.
#
# @relation implements:R-310-001
# =============================================================================

from .models import (
    DocObject,
    DocObjectPublic,
    HolderKind,
    ObjectLock,
    ObjectReviewRequest,
    ObjectType,
    ProducedBy,
    ReviewDecision,
    ReviewRecord,
    ReviewState,
    WorkingDraft,
    WorkingDraftWrite,
    is_valid_object_id,
)

__all__ = [
    "DocObject",
    "DocObjectPublic",
    "HolderKind",
    "ObjectLock",
    "ObjectReviewRequest",
    "ObjectType",
    "ProducedBy",
    "ReviewDecision",
    "ReviewRecord",
    "ReviewState",
    "WorkingDraft",
    "WorkingDraftWrite",
    "is_valid_object_id",
]
