# =============================================================================
# File: test_project_role_gate_ratchet.py
# Version: 2
# Path: ay_platform_core/tests/coherence/test_project_role_gate_ratchet.py
# Description: Freezes the set of project-scoped endpoints that have NO
#              in-app role gate and rely solely on the gateway boundary, so
#              the list can shrink but never grow unnoticed.
#
#              WHAT THE AUDIT FOUND (2026-10-06). 70 endpoints across C4, C5
#              and C7 were catalogued `Auth.AUTHENTICATED` + `Scope.PROJECT`.
#              Those two together were a contradiction: `Scope.PROJECT`'s
#              contract is that a cross-project call returns 403/404, and
#              `AUTHENTICATED` means no role is checked — so nothing enforced
#              it. Verified against the running stack: a caller holding
#              `project_owner` on `demo` and nothing else received **200**
#              from `/api/v1/projects/not-mine/requirements/entities`. The
#              body was empty only because that project had no data; the
#              authorization decision was ALLOW.
#
#              THE EXPOSURE IS NOW CLOSED, at one point rather than 69.
#              `/auth/verify` refuses a project-content request from a caller
#              holding no grant on that project (E-100-002 v8,
#              `_role_gated_project_id`). Re-verified live: the same call now
#              returns 403, while the caller's own project still returns 200.
#              The gateway was the right place — it is the only layer that
#              knows both the target project and the caller's full scope map,
#              so it also covers endpoints nobody has written yet, and it
#              cannot break the service-to-service callers (C3's tools,
#              C4→C7 live-docs, C12→C7 ingestion, C9's MCP adapters) because
#              none of them traverse C1.
#
#              SO WHAT IS THIS LIST FOR NOW? Defence in depth. These routes
#              have exactly ONE thing standing between a caller and another
#              project's data, and these tests keep that fact countable:
#                * a component reached directly — a port-forward, a future
#                  mesh topology, a mistaken `expose:` — has no gate at all;
#                * an internal caller that self-asserts roles (C4's live-docs
#                  client sends `X-User-Roles: project_editor`) is trusted
#                  unconditionally by these routes;
#                * removing the boundary check re-opens all 69 silently.
#              An in-app `_require_role` on each would remove that
#              single-point dependence. Until then the number is the risk.
#
#              WHY THE ISOLATION MATRIX DID NOT CATCH THE ORIGINAL HOLE.
#              `test_isolation.py` interpolates a fabricated resource id, so
#              an un-gated endpoint answers 404 — absence of the resource,
#              not refusal of the caller — and the test accepts that as proof
#              of isolation. Only `…/entities/{entity_id}/history` was
#              caught, because it returns `200 {"history": []}` for a
#              non-existent resource instead of 404. It has since been gated
#              in-app at viewer level, which is why the count here is 69 and
#              not 70.
#
#              The entries carry no individual waiver on purpose: the list IS
#              the waiver, and its length is the number a reader should react
#              to.
# =============================================================================

from __future__ import annotations

import pytest

from tests.e2e.auth_matrix._catalog import ENDPOINTS, Auth, Scope

pytestmark = pytest.mark.coherence


#: Project-scoped endpoints with NO role gate, as of 2026-10-06. Frozen.
#: Remove an entry when its route gains a `_require_role(...)` AND its
#: catalogue row becomes `Auth.ROLE_GATED`. Never add one.
_KNOWN_UNGATED: frozenset[tuple[str, str]] = frozenset({
    ("DELETE", "/api/v1/projects/{project_id}/documents/{path:path}"),
    ("GET", "/api/v1/memory/projects/{project_id}/enrichment-config"),
    ("GET", "/api/v1/memory/projects/{project_id}/kg/summary"),
    ("GET", "/api/v1/memory/projects/{project_id}/live-docs/kg-indexed"),
    ("GET", "/api/v1/memory/projects/{project_id}/quota"),
    ("GET", "/api/v1/memory/projects/{project_id}/sources"),
    ("GET", "/api/v1/memory/projects/{project_id}/sources/{source_id}"),
    ("GET", "/api/v1/memory/projects/{project_id}/sources/{source_id}/blob"),
    ("GET", "/api/v1/memory/projects/{project_id}/sources/{source_id}/chunks.zip"),
    ("GET", "/api/v1/memory/projects/{project_id}/sources/{source_id}/chunks/{chunk_id}"),
    ("GET", "/api/v1/memory/projects/{project_id}/sources/{source_id}/diagnostics"),
    ("GET", "/api/v1/memory/projects/{project_id}/sources/{source_id}/runs"),
    ("GET", "/api/v1/memory/projects/{project_id}/sources/{source_id}/runs/{run_id}/artifacts"),
    ("GET", "/api/v1/memory/projects/{project_id}/sources/{source_id}/runs/{run_id}/artifacts.zip"),
    (
        "GET",
        "/api/v1/memory/projects/{project_id}/sources/{source_id}/runs/{run_id}/artifacts/{artifact_path:path}",
    ),
    ("GET", "/api/v1/projects/{project_id}/artifacts/runs"),
    ("GET", "/api/v1/projects/{project_id}/artifacts/runs/{run_id}/blob"),
    ("GET", "/api/v1/projects/{project_id}/artifacts/runs/{run_id}/tree"),
    ("GET", "/api/v1/projects/{project_id}/baseline-readiness"),
    ("GET", "/api/v1/projects/{project_id}/baselines"),
    ("GET", "/api/v1/projects/{project_id}/baselines/{tag}"),
    ("GET", "/api/v1/projects/{project_id}/baselines/{tag}/render/{fmt}"),
    ("GET", "/api/v1/projects/{project_id}/changes"),
    ("GET", "/api/v1/projects/{project_id}/changes/{drop_id}/{requirement_id}"),
    ("GET", "/api/v1/projects/{project_id}/changes/{drop_id}/{requirement_id}/closure"),
    ("GET", "/api/v1/projects/{project_id}/containers/{container}/objects"),
    ("GET", "/api/v1/projects/{project_id}/containers/{container}/objects/{object_id}"),
    ("GET", "/api/v1/projects/{project_id}/containers/{container}/objects/{object_id}/draft"),
    ("GET", "/api/v1/projects/{project_id}/containers/{container}/objects/{object_id}/versions"),
    (
        "GET",
        "/api/v1/projects/{project_id}/containers/{container}/objects/{object_id}/versions/{version}",
    ),
    ("GET", "/api/v1/projects/{project_id}/coverage/containers/{container}"),
    ("GET", "/api/v1/projects/{project_id}/coverage/requirements/{requirement_id}"),
    ("GET", "/api/v1/projects/{project_id}/coverage/speculative"),
    ("GET", "/api/v1/projects/{project_id}/coverage/suspect"),
    ("GET", "/api/v1/projects/{project_id}/documents"),
    ("GET", "/api/v1/projects/{project_id}/documents/{path:path}"),
    ("GET", "/api/v1/projects/{project_id}/git/commits"),
    ("GET", "/api/v1/projects/{project_id}/impact/{requirement_id}"),
    ("GET", "/api/v1/projects/{project_id}/intake/drops/{drop_id}/requirements"),
    ("GET", "/api/v1/projects/{project_id}/intake/drops/{drop_id}/requirements/{requirement_id}"),
    (
        "GET",
        "/api/v1/projects/{project_id}/intake/drops/{drop_id}/requirements/{requirement_id}/findings",
    ),
    (
        "GET",
        "/api/v1/projects/{project_id}/intake/drops/{drop_id}/requirements/{requirement_id}/fragments",
    ),
    ("GET", "/api/v1/projects/{project_id}/intake/drops/{drop_id}/rework"),
    ("GET", "/api/v1/projects/{project_id}/plans"),
    ("GET", "/api/v1/projects/{project_id}/plans/{plan_id}"),
    ("GET", "/api/v1/projects/{project_id}/plans/{plan_id}/report"),
    ("GET", "/api/v1/projects/{project_id}/plans/{plan_id}/versions/{version}"),
    (
        "GET",
        "/api/v1/projects/{project_id}/process/cycles/{cycle_id}/containers/{container}/activity",
    ),
    ("GET", "/api/v1/projects/{project_id}/process/cycles/{cycle_id}/resolved"),
    ("GET", "/api/v1/projects/{project_id}/process/cycles/{cycle_id}/versions/{version}"),
    ("GET", "/api/v1/projects/{project_id}/process/workflows/{workflow_id}/resolved"),
    ("GET", "/api/v1/projects/{project_id}/process/workflows/{workflow_id}/versions/{version}"),
    ("GET", "/api/v1/projects/{project_id}/requirements/documents"),
    ("GET", "/api/v1/projects/{project_id}/requirements/documents/{slug}"),
    ("GET", "/api/v1/projects/{project_id}/requirements/entities"),
    ("GET", "/api/v1/projects/{project_id}/requirements/entities/{entity_id}"),
    ("GET", "/api/v1/projects/{project_id}/requirements/entities/{entity_id}/versions/{version}"),
    ("GET", "/api/v1/projects/{project_id}/requirements/export"),
    ("GET", "/api/v1/projects/{project_id}/requirements/reindex/{job_id}"),
    ("GET", "/api/v1/projects/{project_id}/requirements/relations"),
    ("GET", "/api/v1/projects/{project_id}/requirements/tailorings"),
    ("GET", "/api/v1/projects/{project_id}/source/file/{path:path}/meta"),
    ("GET", "/api/v1/projects/{project_id}/source/tree"),
    ("POST", "/api/v1/projects/{project_id}/coverage/audit/unallocated"),
    ("POST", "/api/v1/projects/{project_id}/documents"),
    ("POST", "/api/v1/projects/{project_id}/documents/mkdir"),
    ("POST", "/api/v1/projects/{project_id}/documents/move"),
    ("POST", "/api/v1/projects/{project_id}/documents/rename"),
    ("PUT", "/api/v1/projects/{project_id}/documents/{path:path}"),
})


def _ungated_project_endpoints() -> set[tuple[str, str]]:
    """Endpoints claiming a project scope with no role gate."""
    return {
        (e.method, e.path)
        for e in ENDPOINTS
        if e.auth == Auth.AUTHENTICATED and e.scope == Scope.PROJECT
    }


def test_no_new_project_endpoint_relies_only_on_the_gateway() -> None:
    """A NEW project-scoped endpoint SHALL carry its own role gate.

    The gateway boundary (E-100-002 v8) already refuses a caller with no
    grant on the project in the URI, so a new un-gated endpoint is not an
    open door the way the original 70 were. It is a new line on the
    single-point-of-failure list, and growing that list is a decision, not a
    detail — which is why it fails here instead of being counted silently.

    Note the isolation matrix will NOT object: it accepts a 404 on a
    fabricated resource id as proof of isolation, and an un-gated endpoint
    produces that for free.
    """
    new = _ungated_project_endpoints() - _KNOWN_UNGATED
    assert not new, (
        f"{len(new)} project-scoped endpoint(s) were added with no in-app "
        "role gate, so they depend entirely on the gateway boundary "
        "refusing the caller. That boundary is real, but it is one check: a "
        "component reached directly, or an internal caller that self-asserts "
        "roles, meets no gate at all on these routes.\n\n"
        "Add `_require_role(x_user_roles, required=…)` to the route (reads "
        "are viewer-level: `project_viewer`/`project_editor`/"
        "`project_owner`) and set the catalogue row to `Auth.ROLE_GATED` "
        "with matching `accept_roles`. Do NOT add it to "
        "`_KNOWN_UNGATED`.\n\n  "
        + "\n  ".join(f"{m} {p}" for m, p in sorted(new))
    )


def test_the_waiver_list_has_no_stale_entries() -> None:
    """A gated endpoint SHALL be removed from the waiver.

    Keeps the count honest: a list that still names endpoints somebody has
    since fixed over-reports the exposure, and an over-reported number gets
    ignored just as fast as an under-reported one.
    """
    stale = _KNOWN_UNGATED - _ungated_project_endpoints()
    assert not stale, (
        f"{len(stale)} waived endpoint(s) are now gated (or no longer "
        "exist). Delete them from `_KNOWN_UNGATED` — that deletion is the "
        "record of the fix.\n\n  "
        + "\n  ".join(f"{m} {p}" for m, p in sorted(stale))
    )


def test_the_waiver_is_not_silently_empty_or_unbounded() -> None:
    """Guards the ratchet itself.

    If `_ungated_project_endpoints()` ever returned nothing — a renamed
    enum member, a changed field — both tests above would pass while
    checking nothing at all. This pins that the catalogue is still being
    read and that the exposure has not quietly grown by an order of
    magnitude.
    """
    assert len(_KNOWN_UNGATED) == 69, (
        "the waiver list changed size without the tests above failing, "
        "which means the comparison is no longer doing its job"
    )
    project_scoped = [e for e in ENDPOINTS if e.scope == Scope.PROJECT]
    assert len(project_scoped) > 100, (
        "the catalogue no longer reports project-scoped endpoints — the "
        "field or enum was renamed and this whole file is now vacuous"
    )
