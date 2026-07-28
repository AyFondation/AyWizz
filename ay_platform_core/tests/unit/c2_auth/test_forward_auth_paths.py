# =============================================================================
# File: test_forward_auth_paths.py
# Version: 1
# Path: ay_platform_core/tests/unit/c2_auth/test_forward_auth_paths.py
# Description: Unit tests for the C2 forward-auth content/governance path
#              discrimination (`_content_project_id`, E-100-002 v4). Project
#              lifecycle-status enforcement applies to project CONTENT URIs
#              only; governance URIs (admin, project metadata, ACL) MUST be
#              exempt so an operator can reactivate a frozen project and an
#              owner can still see its status.
#
# @relation validates:E-100-002
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c2_auth.router import _content_project_id

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "uri",
    [
        "/api/v1/memory/projects/p1/sources",
        "/api/v1/memory/projects/p1/sources/s1/upload",
        "/api/v1/projects/p1/requirements/documents",
        "/api/v1/projects/p1/requirements/entities/e1/history",
        "/api/v1/projects/p1/artifacts/runs",
        "/api/v1/projects/p1/documents/readme.md",
        "/api/v1/projects/p1/source/tree",
        "/api/v1/llm/projects/p1/models",
        "/api/v1/memory/projects/p1/sources?refresh=1",  # query stripped
    ],
)
def test_content_uris_yield_project_id(uri: str) -> None:
    assert _content_project_id(uri) == "p1"


@pytest.mark.parametrize(
    "uri",
    [
        "/api/v1/projects/p1",  # bare project metadata (governance)
        "/api/v1/projects/p1/",  # trailing slash, still bare
        "/api/v1/projects/p1/members/u1",  # ACL (governance)
        "/admin/projects/p1/deactivate",  # platform-operator surface
        "/admin/projects/p1/members",  # operator ACL read
        "/api/v1/admin/projects/p1/artifacts/seed",  # admin seed
        "/api/v1/projects",  # list, no project id
        "/api/v1/conversations",  # not project-scoped
        "/health",
    ],
)
def test_governance_and_non_project_uris_yield_none(uri: str) -> None:
    assert _content_project_id(uri) is None
