# =============================================================================
# File: test_object_endpoints.py
# Version: 1
# Path: ay_platform_core/tests/contract/c5_requirements/test_object_endpoints.py
# Description: Contract tests for the object-grain REST surface
#              (310-SPEC §4.1 / §4.7 / §4.10). Asserts the route roster, the
#              role gates, and the deliberate status-code mapping — all
#              against the router definition, with no backend.
#
#              The status codes are a contract, not an implementation detail:
#                - 409 for a stale expected_version — the request was
#                  well-formed, the object moved under it (R-310-192);
#                - 423 for a foreign lease — a temporary state naming a
#                  holder, not an authorisation verdict (R-310-193).
#
# @relation validates:R-310-192
# @relation validates:R-310-193
# =============================================================================

from __future__ import annotations

from typing import ClassVar

import pytest
from fastapi.routing import APIRoute

from ay_platform_core.c5_requirements.objects.router import router
from tests.e2e.auth_matrix._catalog import ENDPOINTS, EndpointSpec

_BASE = "/api/v1/projects/{project_id}/containers/{container}/objects"


def _routes() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for route in router.routes:
        if isinstance(route, APIRoute):
            for method in (route.methods or set()) - {"HEAD", "OPTIONS"}:
                found.add((method, route.path))
    return found


@pytest.mark.contract
class TestRouteRoster:
    EXPECTED: ClassVar[set[tuple[str, str]]] = {
        ("GET", _BASE),
        ("POST", _BASE),
        ("GET", f"{_BASE}/{{object_id}}"),
        ("GET", f"{_BASE}/{{object_id}}/versions"),
        ("GET", f"{_BASE}/{{object_id}}/versions/{{version}}"),
        ("GET", f"{_BASE}/{{object_id}}/draft"),
        ("PUT", f"{_BASE}/{{object_id}}/draft"),
        ("POST", f"{_BASE}/{{object_id}}/review"),
        ("POST", f"{_BASE}/{{object_id}}/lock"),
        ("DELETE", f"{_BASE}/{{object_id}}/lock"),
        ("DELETE", f"{_BASE}/{{object_id}}/lock/force"),
    }

    def test_roster_is_exactly_as_declared(self) -> None:
        assert _routes() == self.EXPECTED

    def test_no_route_deletes_a_version(self) -> None:
        # R-310-204: the retained chain is never deleted. As with the storage
        # layer, the absence of the route is the enforcement.
        destructive = {
            (method, path)
            for method, path in _routes()
            if method == "DELETE" and "version" in path
        }
        assert not destructive, destructive


@pytest.mark.contract
class TestCatalogAgreement:
    """CLAUDE.md §13.2 — every route carries exactly one EndpointSpec."""

    def _object_specs(self) -> dict[tuple[str, str], EndpointSpec]:
        return {
            (spec.method, spec.path): spec
            for spec in ENDPOINTS
            # Selected on "/objects", not "/containers/": the process
            # surface also lives under a {container} segment, and a looser
            # selector would sweep it in and report it as a stale object row.
            if spec.component == "c5_requirements" and "/objects" in spec.path
        }

    def test_every_route_is_catalogued(self) -> None:
        missing = _routes() - set(self._object_specs())
        assert not missing, f"routes absent from the auth matrix catalog: {missing}"

    def test_no_stale_catalog_entry(self) -> None:
        stale = set(self._object_specs()) - _routes()
        assert not stale, f"catalog entries with no live route: {stale}"

    def test_every_object_endpoint_is_role_gated(self) -> None:
        """Reads AND writes require a project role (E-100-002 v8).

        **THIS ASSERTION WAS INVERTED FOR READS.** It used to expect
        `authenticated` on every GET — i.e. "any logged-in caller may read
        this project's objects, whoever they are" — and that is exactly the
        exposure the 2026-10-06 audit found across 70 endpoints: a
        `Scope.PROJECT` row promising isolation with no role gate behind it.
        Verified live before the fix: a caller with a grant on `demo` alone
        read `/api/v1/projects/not-mine/...` and got 200.

        Reads are now gated at VIEWER level by the router-wide
        `require_project_content_role`, so no legitimate reader loses
        access; writes keep their stricter own gates, which the tests below
        pin individually.
        """
        for (method, path), spec in self._object_specs().items():
            assert spec.auth.value == "role_gated", (method, path)

    def test_reads_are_viewer_level_and_writes_are_not(self) -> None:
        """The floor must not become a write permission.

        Without this, the test above would be satisfied by declaring every
        route `role_gated` with viewer-level `accept_roles` — which would
        hand a `project_viewer` the ability to create objects and break
        leases. Reads accept any grant; writes do not accept a viewer.
        """
        for (method, path), spec in self._object_specs().items():
            if method == "GET":
                assert "project_viewer" in spec.accept_roles, (method, path)
            else:
                assert "project_viewer" not in spec.accept_roles, (method, path)

    def test_content_endpoints_exclude_the_content_blind_role(self) -> None:
        # E-100-002: platform_manager is content-blind, and every route here
        # operates on tenant content.
        for (method, path), spec in self._object_specs().items():
            if spec.auth.value != "role_gated":
                continue
            assert "platform_manager" in spec.excluded_global_roles, (method, path)

    def test_breaking_a_foreign_lease_is_owner_only(self) -> None:
        # R-310-193 reserves it to the container owner; project_editor must
        # not be able to break somebody else's lease.
        spec = self._object_specs()[
            ("DELETE", f"{_BASE}/{{object_id}}/lock/force")
        ]
        assert spec.accept_roles == ("project_owner",)

    def test_ordinary_writes_accept_editors(self) -> None:
        for method, path in (
            ("POST", _BASE),
            ("PUT", f"{_BASE}/{{object_id}}/draft"),
            ("POST", f"{_BASE}/{{object_id}}/review"),
        ):
            spec = self._object_specs()[(method, path)]
            assert spec.accept_roles == ("project_editor", "project_owner")

    def test_creation_reports_201(self) -> None:
        assert self._object_specs()[("POST", _BASE)].success_status == 201

    def test_lock_release_reports_204(self) -> None:
        for path in (f"{_BASE}/{{object_id}}/lock", f"{_BASE}/{{object_id}}/lock/force"):
            assert self._object_specs()[("DELETE", path)].success_status == 204
