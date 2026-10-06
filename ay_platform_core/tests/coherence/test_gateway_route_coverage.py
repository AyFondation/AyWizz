# =============================================================================
# File: test_gateway_route_coverage.py
# Version: 1
# Path: ay_platform_core/tests/coherence/test_gateway_route_coverage.py
# Description: Refuses an HTTP route that the gateway does not deliver to its
#              own component behind the authorization boundary.
#
#              WHY THIS EXISTS. An audit of the route catalogue against
#              `infra/c1_gateway/dynamic/routers.yml` found 83 endpoints —
#              essentially the entire 310-SPEC traceability capability plus
#              C4's source browser — that no router claimed. Ten C5 project
#              prefixes (`containers`, `coverage`, `intake`, `plans`,
#              `process`, `changes`, `baselines`, `absorb`,
#              `baseline-readiness`, `impact`) matched the generic
#              `c2-projects` rule and were delivered to C2, which does not
#              implement them: the gateway answered 404 for the whole
#              feature. `/api/v1/process` had no rule at all and fell to the
#              UI catch-all. Confirmed live: `/requirements/documents`
#              returned 200 with real data while every one of those prefixes
#              returned 404 or 502.
#
#              The capability was built, and its unit, contract and
#              integration tiers were green throughout, because all of them
#              mount the routers in-process. NOTHING checked that a route
#              is reachable through the deployed gateway. That is the same
#              blind spot that let `.dockerignore` drop a Python package and
#              let C6's run-trigger return 403 to every caller: the
#              composition root and the deployed artifact were exercised by
#              nothing.
#
#              Three questions are asked of every catalogued route:
#                1. does a router match it at all?
#                2. does the WINNING router (Traefik precedence: highest
#                   priority) send it to the component that implements it?
#                   A route delivered to the wrong backend is as dead as an
#                   unrouted one — it just 404s from somewhere else.
#                3. is it behind `forward-auth-c2`, i.e. the authorization
#                   boundary?
#
#              WHAT THIS DOES NOT DO. It does not re-implement Traefik. It
#              models the three predicates this config uses (`Path`,
#              `PathPrefix`, `PathRegexp`), `Method`, and priority ordering.
#              That is the subset the platform relies on, and the matcher
#              itself is tested below so a false negative cannot hide behind
#              a green assertion.
#
#              NO `@relation validates:` MARKER, deliberately — same reason
#              as `test_dockerignore_spares_source.py`: this guards a DEFECT
#              CLASS, not a requirement, and claiming a requirement it does
#              not reach would make `060-IMPLEMENTATION-STATUS.md` report a
#              requirement `tested` on the strength of a comment.
# =============================================================================

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.routing import APIRoute
from fastapi.security.base import SecurityBase

from ay_platform_core.c2_auth.router import router as c2_auth_router
from tests.e2e.auth_matrix._catalog import ENDPOINTS

pytestmark = pytest.mark.coherence

_REPO = Path(__file__).resolve().parents[3]
_ROUTERS_YML = _REPO / "infra" / "c1_gateway" / "dynamic" / "routers.yml"

#: The authorization boundary. Every route carrying tenant or project content
#: SHALL sit behind it, because it is what resolves the caller's identity and
#: their role ON THE TARGET PROJECT into the forward-auth headers the
#: backends gate on.
_GATE = "forward-auth-c2"

#: Which Traefik service must serve each catalogue component.
_EXPECTED_SERVICE: dict[str, str] = {
    "c2_auth": "c2",
    "c3_conversation": "c3",
    "c4_orchestrator": "c4",
    "c5_requirements": "c5",
    "c6_validation": "c6",
    "c7_memory": "c7",
    "c8_admin": "c8-admin",
    "c9_mcp": "c9",
    "c16_backup": "c16-backup",
}

#: Prefixes legitimately served WITHOUT `forward-auth-c2`, with the reason.
#: Forward-auth would be circular on C2's own token surface: C2 is the only
#: component that can verify a JWT, so it authenticates itself. The exemption
#: is EARNED, not asserted — `test_every_ungated_auth_route_verifies_a_bearer`
#: below proves each of these routes validates a bearer token in-app, and
#: `test_no_auth_route_trusts_a_forwardable_header` proves none of them
#: trusts a header an unauthenticated caller could simply set.
_SELF_AUTHENTICATING_PREFIXES = ("/auth",)

#: Routes under `_SELF_AUTHENTICATING_PREFIXES` that are genuinely public.
_PUBLIC_AUTH_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/auth/login"),
        ("POST", "/auth/token"),
        ("GET", "/auth/config"),
    }
)


# ---------------------------------------------------------------------------
# Traefik rule matching
# ---------------------------------------------------------------------------


def _load_routers() -> dict[str, dict[str, Any]]:
    cfg = yaml.safe_load(_ROUTERS_YML.read_text())
    routers: dict[str, dict[str, Any]] = cfg["http"]["routers"]
    return routers


def _predicates(rule: str) -> tuple[list[tuple[str, str]], set[str]]:
    methods = set(re.findall(r"Method\(`([^`]+)`\)", rule))
    preds = [
        (kind, val)
        for kind, val in re.findall(r"(Path|PathPrefix|PathRegexp)\(`([^`]+)`\)", rule)
    ]
    return preds, methods


def _rule_matches(rule: str, path: str, method: str) -> bool:
    preds, methods = _predicates(rule)
    if methods and method.upper() not in methods:
        return False
    for kind, val in preds:
        if kind == "Path" and path == val:
            return True
        if kind == "PathPrefix" and (path == val or path.startswith(val)):
            return True
        if kind == "PathRegexp" and re.search(val, path):
            return True
    return False


def _concretise(path: str) -> str:
    """Turn `/api/v1/projects/{project_id}/plans` into a concrete path.

    Traefik matches request paths, not templates, so a `{param}` has to
    become a real segment before a prefix or regex comparison means
    anything. `{path:path}` converters are included — they also occupy at
    least one segment.
    """
    return re.sub(r"\{[^}]+\}", "SEG", path)


def _resolve(
    routers: dict[str, dict[str, Any]], path: str, method: str
) -> tuple[str, int, list[str], str] | None:
    """Return the WINNING router for `path`, per Traefik precedence.

    Traefik picks the matching router with the highest `priority`; that is
    what makes `c5-project-surfaces` (100) beat `c2-projects` (50) on a
    shared `/api/v1/projects/...` prefix, and it is precisely the mechanism
    that was missing for the ten C5 prefixes.
    """
    best: tuple[str, int, list[str], str] | None = None
    for name, router in routers.items():
        if not _rule_matches(router["rule"], path, method):
            continue
        priority = int(router.get("priority", 0))
        if best is None or priority > best[1]:
            best = (
                name,
                priority,
                list(router.get("middlewares") or []),
                str(router.get("service")),
            )
    return best


# ---------------------------------------------------------------------------
# The three questions, asked of every catalogued route
# ---------------------------------------------------------------------------


def test_every_catalogued_route_is_routed_to_its_own_component() -> None:
    """A catalogued route SHALL reach the component that implements it.

    Covers questions 1 and 2 together: "no router matched" and "a router
    matched but sends it elsewhere" are the same defect to a user — the
    feature does not work — and they were both present.
    """
    routers = _load_routers()
    findings: list[str] = []

    for endpoint in ENDPOINTS:
        want = _EXPECTED_SERVICE.get(endpoint.component)
        if want is None:
            findings.append(
                f"{endpoint.component} has no entry in _EXPECTED_SERVICE; add "
                "one so its routes are actually checked"
            )
            continue
        hit = _resolve(routers, _concretise(endpoint.path), endpoint.method)
        if hit is None:
            findings.append(
                f"UNROUTED  {endpoint.method:6} {endpoint.path} "
                f"({endpoint.component}): no router rule matches it"
            )
            continue
        name, priority, _middlewares, service = hit
        if service != want:
            findings.append(
                f"MISROUTED {endpoint.method:6} {endpoint.path} "
                f"({endpoint.component}): winning router {name!r} "
                f"(priority {priority}) sends it to {service!r}, "
                f"expected {want!r}"
            )

    assert not findings, (
        f"{len(findings)} catalogued route(s) are not delivered to the "
        "component that implements them. The backend test tiers cannot see "
        "this — they mount the routers in-process — so the feature is green "
        "in CI and 404 in the deployed product.\n\n"
        "Add a router in `infra/c1_gateway/dynamic/routers.yml` (then "
        "regenerate the K8s configmap with "
        "`python3 infra/c1_gateway/scripts/gen_k8s_c1_configmap.py`). A "
        "project-scoped prefix needs priority > 50 to beat the generic "
        "`c2-projects` rule.\n\n  " + "\n  ".join(findings)
    )


def test_every_content_route_sits_behind_the_authorization_boundary() -> None:
    """A route requiring identity SHALL be behind `forward-auth-c2`.

    The exception is C2's own token surface, where forward-auth would be
    circular; that exemption is proven safe by the two tests below rather
    than taken on trust.
    """
    routers = _load_routers()
    findings: list[str] = []

    for endpoint in ENDPOINTS:
        if endpoint.auth.value == "open":
            # Being gated anyway is never a defect, so `open` routes are not
            # constrained in either direction here.
            continue
        if endpoint.path.startswith(_SELF_AUTHENTICATING_PREFIXES):
            continue
        hit = _resolve(routers, _concretise(endpoint.path), endpoint.method)
        if hit is None:
            continue  # already reported by the routing test
        name, priority, middlewares, service = hit
        if _GATE not in middlewares:
            findings.append(
                f"{endpoint.method:6} {endpoint.path} (auth="
                f"{endpoint.auth.value}) lands on {name!r} (priority "
                f"{priority}, service {service!r}) whose middlewares are "
                f"{middlewares or 'NONE'} — no {_GATE}"
            )

    assert not findings, (
        f"{len(findings)} route(s) requiring identity are reachable without "
        f"passing {_GATE}. Such a route receives whatever `X-User-Id` / "
        "`X-User-Roles` the CALLER chose to send, because nothing "
        "overwrites them — a trivial privilege escalation.\n\n  "
        + "\n  ".join(findings)
    )


# ---------------------------------------------------------------------------
# The exemption, earned rather than asserted
# ---------------------------------------------------------------------------


def _security_schemes(route: APIRoute) -> list[str]:
    """Names of the security schemes in a route's dependency tree."""
    found: list[str] = []

    def walk(dependant: Any) -> None:
        call = getattr(dependant, "call", None)
        if isinstance(call, SecurityBase):
            found.append(type(call).__name__)
        for sub in dependant.dependencies:
            walk(sub)

    walk(route.dependant)
    return found


def _auth_routes() -> list[tuple[str, str, APIRoute]]:
    """Every route of C2's `/auth` surface, with its full mounted path.

    `main.py` mounts this router at `prefix="/auth"`; the prefix is applied
    here rather than read from the app because the app mounts its routers
    inside the lifespan, so `create_app()` alone exposes only `/health`.
    """
    out: list[tuple[str, str, APIRoute]] = []
    for route in c2_auth_router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods or set()):
            if method in {"HEAD", "OPTIONS"}:
                continue
            out.append((method, f"/auth{route.path}", route))
    return out


def test_every_ungated_auth_route_verifies_a_bearer() -> None:
    """C2's `/auth` surface has no forward-auth, so it SHALL self-verify.

    This is what makes `_SELF_AUTHENTICATING_PREFIXES` legitimate instead
    of a hole: `/auth/users`, `/auth/sessions` and friends are reachable
    with no middleware in front of them, and they are safe only because
    each one decodes and verifies the bearer token itself.
    """
    findings: list[str] = []
    for method, path, route in _auth_routes():
        if (method, path) in _PUBLIC_AUTH_ROUTES:
            continue
        if not _security_schemes(route):
            findings.append(
                f"{method:6} {path} declares no security scheme, and no "
                "gateway middleware protects this prefix"
            )

    assert not findings, (
        f"{len(findings)} route(s) on the un-gated `/auth` prefix neither "
        "sit behind forward-auth nor verify a bearer token themselves, so "
        "they are reachable by an anonymous caller.\n\n"
        "Either add the bearer dependency, or — if the route is genuinely "
        "public — add it to `_PUBLIC_AUTH_ROUTES` with a reason.\n\n  "
        + "\n  ".join(findings)
    )


def test_no_auth_route_trusts_a_forwardable_header() -> None:
    """An un-gated route SHALL NOT read `X-User-*` / `X-Tenant-Id`.

    On a prefix with no forward-auth middleware, those headers are set by
    the caller and nothing overwrites them. A route that gated on
    `X-User-Roles` here would hand `admin` to anyone who typed it. The
    module is read as text on purpose: this must hold for code that has not
    been imported into a route tree yet.
    """
    # Named explicitly rather than derived from `c2_auth_router.__module__`:
    # that attribute belongs to `APIRouter` (fastapi.routing), not to the
    # module that instantiates it.
    module_file = (
        _REPO
        / "ay_platform_core"
        / "src"
        / "ay_platform_core"
        / "c2_auth"
        / "router.py"
    )
    assert module_file.is_file(), module_file

    offenders: list[str] = []
    for lineno, line in enumerate(module_file.read_text().splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#") or '"""' in stripped:
            continue
        # A parameter binding, not a docstring mention or the /verify
        # emission (`response.headers[...] = ...`).
        if re.search(r"\bx_(user_id|user_roles|tenant_id)\b\s*[:=]", stripped):
            offenders.append(f"{module_file.name}:{lineno}: {stripped}")

    assert not offenders, (
        "C2's `/auth` surface has no forward-auth middleware, so these "
        "headers are attacker-controlled there. Gate on the verified JWT "
        "claims instead.\n\n  " + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# The matcher itself, so a false negative cannot hide behind a green test
# ---------------------------------------------------------------------------


def test_priority_decides_between_two_matching_routers() -> None:
    """The precedence rule the ten C5 prefixes needed."""
    routers = {
        "generic": {
            "rule": "PathPrefix(`/api/v1/projects`)",
            "service": "c2",
            "priority": 50,
        },
        "specific": {
            "rule": "PathRegexp(`^/api/v1/projects/[^/]+/plans(/.*)?$`)",
            "service": "c5",
            "priority": 100,
        },
    }
    hit = _resolve(routers, "/api/v1/projects/demo/plans", "GET")
    assert hit is not None
    assert hit[0] == "specific"
    assert hit[3] == "c5"


def test_a_lower_priority_specific_rule_loses() -> None:
    """Priority, not specificity, is what Traefik actually orders on.

    Guards the inverse of the test above: if `_resolve` preferred the
    narrower rule, it would report the platform as correct while Traefik
    delivered the request to C2.
    """
    routers = {
        "generic": {
            "rule": "PathPrefix(`/api/v1/projects`)",
            "service": "c2",
            "priority": 100,
        },
        "specific": {
            "rule": "PathRegexp(`^/api/v1/projects/[^/]+/plans(/.*)?$`)",
            "service": "c5",
            "priority": 50,
        },
    }
    hit = _resolve(routers, "/api/v1/projects/demo/plans", "GET")
    assert hit is not None
    assert hit[3] == "c2"


def test_the_matcher_reproduces_the_original_defect() -> None:
    """Without a `/plans` rule, the route went to C2 — a silent 404."""
    routers = {
        "generic": {
            "rule": "PathPrefix(`/api/v1/projects`)",
            "service": "c2",
            "priority": 50,
        },
    }
    hit = _resolve(routers, "/api/v1/projects/demo/plans", "GET")
    assert hit is not None
    assert hit[3] == "c2", "this is the bug the audit found, modelled"


def test_an_unmatched_path_resolves_to_nothing() -> None:
    routers = {
        "c6": {
            "rule": "PathPrefix(`/api/v1/validation`)",
            "service": "c6",
            "priority": 50,
        },
    }
    assert _resolve(routers, "/api/v1/process/cycles", "GET") is None


def test_method_predicates_are_honoured() -> None:
    routers = {
        "login": {
            "rule": "Path(`/auth/login`) && Method(`POST`)",
            "service": "c2",
            "priority": 100,
        },
    }
    assert _resolve(routers, "/auth/login", "POST") is not None
    assert _resolve(routers, "/auth/login", "GET") is None


def test_params_are_concretised_before_matching() -> None:
    # A raw `{project_id}` would not match `[^/]+` in a useful way for the
    # `{path:path}` form, which contains a colon.
    assert _concretise("/api/v1/projects/{project_id}/plans") == (
        "/api/v1/projects/SEG/plans"
    )
    assert _concretise("/a/{path:path}/meta") == "/a/SEG/meta"


def test_there_is_a_catalogue_to_check() -> None:
    """Guards against the suite passing because it inspected nothing."""
    assert len(ENDPOINTS) > 200
    assert any(e.path.startswith("/api/v1/projects/") for e in ENDPOINTS)
    assert len(_load_routers()) > 20
    assert len(_auth_routes()) > 5
