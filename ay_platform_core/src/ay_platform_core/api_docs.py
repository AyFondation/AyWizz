# =============================================================================
# File: api_docs.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/api_docs.py
# Description: Enriches the OpenAPI document FastAPI generates, for every
#              component, from one place.
#
#              WHY A POST-PROCESSOR AND NOT 200 EDITS. The three gaps below
#              are uniform across ~272 operations in 29 routers. Fixing them
#              at each call site would mean ~200 `Header(...,
#              include_in_schema=False)` edits and ~268 `responses={...}`
#              edits, every one of which a new route can forget. FastAPI
#              lets `app.openapi` be replaced by any callable, so the rules
#              are applied once, to the generated document, and a route
#              added tomorrow inherits them.
#
#              GAP 1 — THE DOCUMENT INVITED HEADER FORGERY. `X-User-Id`,
#              `X-Tenant-Id`, `X-User-Roles` and `X-Project-Scopes` are
#              declared as `Header(...)` parameters, directly or through
#              `_require_actor` / `_require_tenant`, so they appeared in the
#              document as parameters THE CALLER SUPPLIES — 555 of them.
#              They are not: C1's `forward-auth-c2` middleware strips
#              whatever a client sent and injects what C2 verified, and
#              `tests/system/test_header_forgery.py` exists because a
#              caller setting them is an attack. A reader following the
#              document was being told to do the one thing the gateway is
#              there to refuse. They are now hidden.
#
#              GAP 2 — NO ERROR RESPONSES. 268 of 272 operations declared no
#              4xx at all, so the document could not tell a consumer that an
#              endpoint refuses them. Two rules are DERIVABLE and therefore
#              applied automatically:
#                · 401 on every non-public operation — true without
#                  exception: no verified identity, no service;
#                · 404 on every operation carrying a path parameter — an id
#                  that names nothing is not found.
#              403 is NOT derivable here. `_require_role(...)` is called in
#              the handler BODY, not as a `Depends`, so it is invisible to
#              route introspection, and annotating 403 everywhere would
#              claim a refusal many routes cannot produce. It is declared
#              per router instead (`APIRouter(responses=...)`), and
#              `scripts/checks/audit_openapi_docs.py` checks each one
#              against the auth-matrix catalogue, which already knows which
#              routes are `ROLE_GATED`. Derive what is provable; verify the
#              rest; invent nothing.
#
#              GAP 3 — NO APP-LEVEL PROSE. Every app was
#              `FastAPI(title="C5 Requirements Service")` with no
#              `description` and the default `version="0.1.0"`, so `/docs`
#              opened on a bare endpoint list with no statement of what the
#              component is or how to authenticate against it.
# =============================================================================

from __future__ import annotations

import os
import re
from typing import Any, TypedDict

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field

#: Headers C1 injects after `forward-auth-c2` has verified the bearer. A
#: client cannot set them: the middleware's `authResponseHeaders` list
#: replaces whatever arrived. Hidden from the document so it never reads
#: as "supply these".
GATEWAY_INJECTED_HEADERS: frozenset[str] = frozenset(
    {"x-user-id", "x-tenant-id", "x-user-roles", "x-project-scopes"}
)

#: Matches a `{param}` segment, including FastAPI's `{name:path}` converter.
_PATH_PARAM_RE = re.compile(r"\{[^}]+\}")

#: A PROJECT-CONTENT path: `/projects/{id}/` followed by something. This is
#: the predicate C2's `/auth/verify` uses to refuse a caller holding no role
#: on the named project (`_role_gated_project_id`, E-100-002 v8), so a 403
#: on these paths is not an inference — it is the gateway's own rule,
#: restated in the document.
#:
#: It deliberately does NOT match `/projects` or `/projects/{id}`: a listing
#: and a project's own metadata are governance, not content, and both are
#: reachable by any tenant member.
_PROJECT_CONTENT_RE = re.compile(r"/projects/\{[^}]+\}/.")


class ErrorBody(BaseModel):
    """The body every refused request carries.

    FastAPI serialises `HTTPException` as `{"detail": ...}`, and before
    this model the document described none of the 4xx bodies at all — a
    consumer had to discover the shape by provoking an error.
    """

    detail: str = Field(
        description="Human-readable reason the request was refused.",
        examples=["requires a project role on this project"],
    )


def _error_response(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorBody"}}},
    }


#: The 403 prose, declared once.
#:
#: `describe_app` attaches it automatically to every PROJECT-CONTENT path,
#: which is derivable and true (see `_PROJECT_CONTENT_RE`). It is exported
#: so a route gated on something ELSE — a platform role on a tenant, a
#: session, a user — can declare its own 403 with the same words:
#: `@router.post(..., responses=ROLE_GATED_RESPONSES)`.
#:
#: ATTACHING IT TO A WHOLE ROUTER IS WRONG, and was tried: most routers are
#: MIXED (un-gated reads beside gated writes), so a router-level 403
#: claimed a refusal 43 operations cannot produce — a false statement,
#: which is worse than a missing one because a reader cannot tell. The
#: referee is `scripts/checks/audit_openapi_docs.py`, whose
#: `403_on_ungated_route` finding is what surfaced those 43.
ROLE_GATED_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: _error_response(
        "The caller's roles do not satisfy this route's gate. For project "
        "content this means holding no role on the project named in the "
        "path — a global `admin` or `tenant_admin` role does not substitute "
        "(`E-100-002`)."
    )
}

#: The authorization story, stated once. `forward-auth-c2` is the only way
#: identity reaches a component, so these two sentences are true of every
#: authenticated route on the platform.
_AUTH_PREAMBLE = """
## Authentication

Every route below is reached through the platform gateway (C1, Traefik),
which runs `forward-auth-c2` against C2 before the request arrives. Send a
bearer token:

    Authorization: Bearer <access_token>

obtained from `POST /auth/login`. The gateway resolves the token into the
caller's identity, tenant and per-project roles and injects them as internal
headers; those headers are **not** part of this contract and a client that
sets them has them stripped and replaced.

A request with no valid token is refused with **401**. A caller whose roles
do not satisfy a route's gate is refused with **403** — for project content
that means holding no role on the project named in the path, regardless of
any global role (`admin` and `tenant_admin` are content-blind by design,
per `E-100-002`).
"""


class DocsUrls(TypedDict):
    """The three `FastAPI(...)` keywords that place a component's docs.

    A `TypedDict` rather than a plain `dict[str, str]` because the value is
    splatted into the constructor: `**dict[str, str]` tells mypy only that
    *some* strings arrive under *some* names, so it tries the unpack
    against every parameter of `FastAPI` in turn and reports a mismatch
    for each — 135 errors from one call shape. Naming the keys makes the
    unpack checkable.
    """

    docs_url: str
    redoc_url: str
    openapi_url: str


def docs_urls(service: str) -> DocsUrls:
    """The `FastAPI(**docs_urls("c5"))` kwargs for a reachable docs surface.

    WHY THE PATHS ARE PREFIXED. All nine components serve FastAPI's default
    `/docs`, `/redoc` and `/openapi.json`, and a single Traefik rule on
    `PathPrefix("/docs")` can only point at ONE service — which is why the
    generated documentation was unreachable through the gateway: no router
    claimed it, so it fell to the UI catch-all. Prefixing each component's
    docs with its own SERVICE NAME (`/docs/c5`, `/docs/c8-admin`) makes one
    router per component trivial and needs no path rewriting.

    It also fixes the half that a `stripPrefix` middleware would have
    broken: Swagger UI's HTML references its spec by absolute URL, so
    serving the page at `/docs/c5` while the spec stayed at
    `/openapi.json` would load a page whose spec fetch 404s. Both move
    together.

    `service` is the Traefik service name, deliberately — the string in
    `routers.yml` and the string in the URL are then the same string.

    NOTE ON REACHABILITY. These paths sit behind `forward-auth-c2`, which
    reads a bearer token (`HTTPBearer` on C2's `/auth/verify`). A client
    that sets `Authorization: Bearer …` — curl, Postman, a codegen
    pipeline — reaches them. A plain BROWSER navigation does not, because
    the platform keeps its token in `localStorage` rather than a cookie and
    a navigation carries no header. Browser access needs the UI to proxy
    the spec with the token attached; that is not built.
    """
    return DocsUrls(
        docs_url=f"/docs/{service}",
        redoc_url=f"/docs/{service}/redoc",
        openapi_url=f"/docs/{service}/openapi.json",
    )


def describe_app(
    app: FastAPI,
    *,
    summary: str,
    description: str,
    public_paths: frozenset[str] = frozenset(),
) -> None:
    """Give `app` a documented OpenAPI surface. Mutates `app` in place.

    Args:
        app: The component's FastAPI instance, already carrying its routers.
        summary: One line: what this component is.
        description: Markdown prose. The shared authentication section is
            appended, so each component says only what is specific to it.
        public_paths: Paths reachable WITHOUT a verified identity, as
            exact strings (`"/auth/login"`). They get no 401, because
            claiming one would be false. Everything else gets a 401.
    """
    app.summary = summary
    app.description = description.strip() + "\n" + _AUTH_PREAMBLE
    # Matches the convention C2's `/ux/config` already uses for
    # `build_version`, so the document and the UI footer name the same
    # build instead of disagreeing.
    app.version = os.environ.get("BUILD_VERSION", "dev")

    def _openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            summary=app.summary,
            description=app.description,
            routes=app.routes,
        )
        _hide_injected_headers(schema)
        _declare_error_responses(schema, public_paths)
        _tag_untagged(schema)
        schema.setdefault("components", {}).setdefault("schemas", {})[
            "ErrorBody"
        ] = ErrorBody.model_json_schema()
        app.openapi_schema = schema
        return schema

    app.openapi = _openapi  # type: ignore[method-assign]


#: Tags derived from the path for operations that declare none. Every
#: router sets its own tag; what was left over was the nine `/health`
#: probes and C8's service-to-service call-target route — operational
#: surfaces rather than forgotten ones, so they are grouped as such
#: instead of being given invented business tags.
_DERIVED_TAGS: tuple[tuple[str, str], ...] = (
    ("/health", "ops"),
    ("/metrics", "ops"),
    ("/internal/", "internal"),
)

#: Descriptions for the derived tags, so Swagger's grouping says what the
#: group IS rather than showing a bare word.
DERIVED_TAG_METADATA: list[dict[str, str]] = [
    {
        "name": "ops",
        "description": (
            "Liveness and metrics surfaces. Reachable without a token "
            "(the kubelet has none) and carrying no tenant or project "
            "data."
        ),
    },
    {
        "name": "internal",
        "description": (
            "Service-to-service routes. Not part of the consumer contract "
            "and not reachable through the public gateway; documented so "
            "an operator can reason about a call chain."
        ),
    },
]


def _tag_untagged(schema: dict[str, Any]) -> None:
    """Group operations that declare no tag, by what their path says."""
    used: set[str] = set()
    for path, operations in (schema.get("paths") or {}).items():
        for operation in operations.values():
            if not isinstance(operation, dict) or operation.get("tags"):
                continue
            for prefix, tag in _DERIVED_TAGS:
                if path.startswith(prefix):
                    operation["tags"] = [tag]
                    used.add(tag)
                    break
    if used:
        existing = {t.get("name") for t in schema.get("tags") or []}
        schema.setdefault("tags", []).extend(
            t for t in DERIVED_TAG_METADATA if t["name"] in used - existing
        )


def _hide_injected_headers(schema: dict[str, Any]) -> None:
    """Drop gateway-injected headers from every operation's parameters."""
    for operations in (schema.get("paths") or {}).values():
        for operation in operations.values():
            if not isinstance(operation, dict):
                continue
            params = operation.get("parameters")
            if not isinstance(params, list):
                continue
            kept = [
                p
                for p in params
                if not (
                    isinstance(p, dict)
                    and p.get("in") == "header"
                    and str(p.get("name", "")).lower() in GATEWAY_INJECTED_HEADERS
                )
            ]
            if kept:
                operation["parameters"] = kept
            else:
                # An empty `parameters` array is legal but noisy; drop it so
                # a route whose ONLY parameters were injected headers reads
                # as taking none, which is what a client sees.
                operation.pop("parameters", None)


def _declare_error_responses(
    schema: dict[str, Any], public_paths: frozenset[str]
) -> None:
    """Add the 4xx responses that follow from the route itself.

    Only the two derivable ones. See this module's header for why 403 is
    not among them.
    """
    for path, operations in (schema.get("paths") or {}).items():
        has_path_param = bool(_PATH_PARAM_RE.search(path))
        is_project_content = bool(_PROJECT_CONTENT_RE.search(path))
        is_public = path in public_paths
        for operation in operations.values():
            if not isinstance(operation, dict):
                continue
            responses = operation.setdefault("responses", {})
            if not is_public:
                responses.setdefault(
                    "401",
                    _error_response(
                        "No verified identity. The bearer token is absent, "
                        "expired, or was rejected by C2."
                    ),
                )
            if is_project_content:
                responses.setdefault("403", ROLE_GATED_RESPONSES[403])
            if has_path_param:
                responses.setdefault(
                    "404",
                    _error_response(
                        "No such resource, or none visible to this caller "
                        "in this tenant and project."
                    ),
                )


__all__ = [
    "GATEWAY_INJECTED_HEADERS",
    "ROLE_GATED_RESPONSES",
    "ErrorBody",
    "describe_app",
]
