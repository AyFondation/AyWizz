# =============================================================================
# File: routes.py
# Version: 1
# Path: ay_platform_core/tests/fixtures/routes.py
# Description: Test helper for FastAPI route introspection. FastAPI 0.116+
#              includes sub-routers LAZILY: `app.include_router(r)` no longer
#              flattens `r`'s routes into `app.routes` — it appends an
#              `_IncludedRouter` wrapper whose child routes live under
#              `.original_router.routes`. Roster / app-factory tests that
#              iterate `app.routes` filtering `APIRoute` therefore miss every
#              mounted route. This helper walks that wrapper recursively and
#              returns the flat list of `APIRoute`s, matching the pre-0.116
#              `app.routes` semantics those tests were written against.
# =============================================================================

from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute


def iter_api_routes(app: FastAPI) -> list[APIRoute]:
    """Return every `APIRoute` mounted on `app`, descending into fastapi's
    lazy `_IncludedRouter` wrappers (`.original_router.routes`).

    A prefix passed at include time (`app.include_router(r, prefix="/auth")`)
    is stored on the wrapper's `include_context.prefix`, NOT on the child
    routes — their `.path` is the router-local path. This helper threads the
    accumulated include prefix down the tree and returns each route with its
    fully-resolved `.path`, matching pre-0.116 `app.routes` semantics. Routers
    that bake their prefix into `APIRouter(prefix=...)` (the majority) carry an
    empty include prefix, so those routes are returned unchanged (identity
    preserved). When an include-time prefix applies, a shallow COPY with the
    corrected path is returned — lazy inclusion reuses the original router's
    `APIRoute` objects by reference (they are module-level singletons shared
    with other tests, e.g. the route-catalog coherence test), so mutating them
    in place would corrupt that shared state."""
    out: list[APIRoute] = []

    def _walk(routes: Iterable[Any], prefix: str) -> None:
        for r in routes:
            if isinstance(r, APIRoute):
                full = f"{prefix}{r.path}"
                if full == r.path:
                    out.append(r)
                else:
                    resolved = copy.copy(r)
                    resolved.path = full
                    out.append(resolved)
                continue
            original = getattr(r, "original_router", None)
            if original is not None:
                ctx = getattr(r, "include_context", None)
                child_prefix = getattr(ctx, "prefix", "") or ""
                _walk(original.routes, prefix + child_prefix)

    _walk(app.routes, "")
    return out
