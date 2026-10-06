# =============================================================================
# File: forward_auth.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c2_auth/forward_auth.py
# Description: Wire format of the `X-Project-Scopes` forward-auth header
#              (inc3b) — serialiser AND parser, deliberately in one module.
#
#              WHY THE HEADER EXISTS. `X-User-Roles` carries the caller's
#              global roles plus their role on ONE project: the one C2 can
#              read out of `X-Forwarded-Uri`. That works for every endpoint
#              whose URI contains `…/projects/{pid}/…` and for nothing else.
#              Three surfaces learn their project id somewhere C2 cannot
#              see:
#                - C9 (MCP): the project is a TOOL ARGUMENT. The forwarded
#                  URI is `/api/v1/mcp/...`, so no project role was ever
#                  derived and every MCP tool writing project content
#                  returned 403 to everyone, whoever they were.
#                - C3 conversations / C4 run-by-id: the project is a
#                  property of the RECORD, known only after a DB read.
#              The `/auth/verify` docstring has been tracking this as
#              "inc3b". This header closes it by reporting the caller's
#              WHOLE project→roles map, so a component that resolves its own
#              project id can look up the role for THAT project without C2
#              having to understand its payload shape.
#
#              WHY SERIALISER AND PARSER SHARE A MODULE. They are two halves
#              of one contract across a process boundary. Split apart they
#              drift silently — the producer starts emitting `;`-separated
#              and the consumer still splits on `,` — and the symptom is a
#              403 in production, not a failing test. Here a single
#              round-trip test covers both (§8.4: no parallel definitions).
#
#              SECURITY — READ BEFORE USING. The header is attacker-supplied
#              UNLESS Traefik overwrote it. That overwrite happens only
#              because `X-Project-Scopes` is listed in the
#              `forward-auth-c2` middleware's `authResponseHeaders`
#              (infra/c1_gateway/dynamic/middlewares.yml). Drop it from that
#              list and this becomes a self-service role grant. A route that
#              reads it MUST sit behind `forward-auth-c2`;
#              `tests/coherence/test_gateway_route_coverage.py` enforces
#              that every content route does, and
#              `tests/system/test_header_forgery.py` proves the overwrite
#              empirically against the real gateway.
#
#              IT GRANTS NOTHING BY ITSELF. It REPORTS scopes. A consumer
#              SHALL resolve the project it is about to act on and consult
#              that project's roles — never "the caller holds editor
#              somewhere, therefore allow". That mistake is the confused
#              deputy, and it is why `roles_for_project` demands an explicit
#              project id instead of offering a "has any role" helper.
#
# @relation implements:E-100-002
# =============================================================================

from __future__ import annotations

from ay_platform_core.c2_auth.models import RBACProjectRole

#: Separator between `project=roles` groups.
_GROUP_SEP = ";"
#: Separator between roles inside one group.
_ROLE_SEP = ","
#: Separator between a project id and its roles.
_KV_SEP = "="

HEADER_NAME = "X-Project-Scopes"
"""Canonical header name. Imported by consumers so a rename is one edit."""


def serialize_project_scopes(
    scopes: dict[str, list[RBACProjectRole]],
) -> str:
    """Render a project→roles map as the header value.

    Deterministic (both levels sorted) so the value is stable across
    requests and directly comparable in tests. Projects with an empty role
    list are omitted — they carry no information and would produce a
    dangling `pid=`.

    Returns `""` for an empty map, which is a legitimate value: a
    `tenant_manager` or a freshly created user holds no project scope.
    """
    return _GROUP_SEP.join(
        f"{pid}{_KV_SEP}{_ROLE_SEP.join(sorted(r.value for r in roles))}"
        for pid, roles in sorted(scopes.items())
        if roles
    )


def parse_project_scopes(header: str | None) -> dict[str, set[str]]:
    """Parse the header into `{project_id: {role, …}}`.

    Tolerant of a missing, empty or malformed header, and returns role names
    as plain strings rather than enum members ON PURPOSE: a consumer
    comparing against its own `required` tuple must not crash because a
    future C2 version introduced a role it does not know yet. An unknown
    role simply fails to match, which fails CLOSED.

    Malformed groups (no `=`, empty project id, no roles) are skipped rather
    than raising: this parses a value from another process on a request
    path, and the safe failure is "no scopes for that project" — a 403 —
    not a 500 that an attacker could trigger at will by sending junk on a
    route whose middleware is misconfigured.
    """
    if not header:
        return {}
    parsed: dict[str, set[str]] = {}
    for group in header.split(_GROUP_SEP):
        chunk = group.strip()
        if not chunk or _KV_SEP not in chunk:
            continue
        pid, _, raw_roles = chunk.partition(_KV_SEP)
        pid = pid.strip()
        if not pid:
            continue
        roles = {r.strip() for r in raw_roles.split(_ROLE_SEP) if r.strip()}
        if not roles:
            continue
        parsed.setdefault(pid, set()).update(roles)
    return parsed


def roles_for_project(header: str | None, project_id: str) -> set[str]:
    """Return the caller's roles ON `project_id`, as plain strings.

    The only accessor a gate should use. There is intentionally no
    "does the caller hold this role anywhere" variant: holding `project_
    editor` on one project says nothing about another, and a helper that
    blurred the two would invite exactly the escalation this header is
    meant to avoid.

    An empty set means "no proven role on that project" → refuse.
    """
    if not project_id:
        return set()
    return parse_project_scopes(header).get(project_id, set())
