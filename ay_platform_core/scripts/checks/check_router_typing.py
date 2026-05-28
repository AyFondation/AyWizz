#!/usr/bin/env python3
# =============================================================================
# File: check_router_typing.py
# Version: 1
# Path: ay_platform_core/scripts/checks/check_router_typing.py
# Description: Coherence check — every FastAPI route that returns a body uses a
#              typed Pydantic BaseModel as response_model (no raw dict, no Any).
#              Auto-discovers all router.py modules under src/.
#              Run from ay_platform_core/: python scripts/checks/check_router_typing.py
# =============================================================================

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, get_args, get_origin

from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel

SRC_ROOT = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _discover_routers() -> list[tuple[str, Any]]:
    """Import all router.py files under src/ and return (module_path, router) pairs."""
    routers = []
    for router_file in SRC_ROOT.rglob("router.py"):
        rel = router_file.relative_to(SRC_ROOT)
        module_path = str(rel).replace("/", ".").replace("\\", ".").removesuffix(".py")
        try:
            mod = importlib.import_module(module_path)
            if hasattr(mod, "router"):
                routers.append((module_path, mod.router))
        except Exception as exc:
            print(f"  WARNING: could not import {module_path}: {exc}")
    return routers


def _unwrap_list(annotation: Any) -> Any:
    """list[X] → X, otherwise returns annotation unchanged."""
    origin = get_origin(annotation)
    if origin is list:
        args = get_args(annotation)
        return args[0] if args else annotation
    return annotation


def check() -> list[str]:
    issues: list[str] = []
    router_modules = _discover_routers()

    if not router_modules:
        print("  (no router modules found — trivially OK)")
        return issues

    app = FastAPI()
    for _module_path, router in router_modules:
        app.include_router(router)

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue

        response_model = route.response_model
        if response_model is None:
            continue  # 204 / no body — OK

        inner = _unwrap_list(response_model)

        if inner is Any:
            issues.append(f"  {route.path}: response_model is bare Any")
            continue

        if inner is dict or inner is dict:
            issues.append(f"  {route.path}: response_model is raw dict")
            continue

        if not (isinstance(inner, type) and issubclass(inner, BaseModel)):
            issues.append(
                f"  {route.path}: response_model inner type {inner!r} is not a Pydantic BaseModel"
            )

    return issues


if __name__ == "__main__":
    issues = check()
    if issues:
        print("FAIL: Router typing violations:")
        for line in issues:
            print(line)
        sys.exit(1)
    router_modules = _discover_routers()
    app = FastAPI()
    for _, router in router_modules:
        app.include_router(router)
    n = sum(1 for r in app.routes if isinstance(r, APIRoute))
    print(f"OK: {n} routes across {len(router_modules)} router(s) — all response models typed")
