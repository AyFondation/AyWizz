# =============================================================================
# File: test_compose_dev_profile.py
# Version: 2
# Path: ay_platform_core/tests/coherence/test_compose_dev_profile.py
# Description: Coherence checks on the dev compose stack
#              (`docker-compose.yml` + `docker-compose.dev.override.yml`).
#              Pins two invariants exposed by the 2026-05-13 incident :
#
#                1. mock_llm is gated by the `test` profile and SHALL NOT
#                   appear in the dev compose runtime. If it ever
#                   resurfaces in dev, the C4 orchestrator silently
#                   routes its C8 calls to canned BLOCKED responses
#                   (the bug we just chased).
#
#                2. Every API service that calls C8 (c2, c3, c4) SHALL
#                   pull `.env.dev` via the dev override so its
#                   `C8_GATEWAY_URL` points at real Ollama. A service
#                   that only reads `.env.test` falls back to
#                   `mock_llm:8000` — which is now profile-gated and
#                   absent from the dev stack — and crashes mid-call.
#
#              These tests run on file content only (no docker), so they
#              are deterministic and fast (<50 ms).
# =============================================================================

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
import yaml

_TESTS_DIR = Path(__file__).resolve().parents[1]
_BASE = _TESTS_DIR / "docker-compose.yml"
_DEV_OVERRIDE = _TESTS_DIR / "docker-compose.dev.override.yml"

# Services that issue C8 LLM gateway calls and therefore SHALL be wired
# to a real backend (Ollama) in the dev compose overlay. c4 was added
# 2026-05-13 after the demo-blocking incident where its absence here
# meant pipeline runs silently fell back to mock_llm. Update this list
# any time a new component starts hitting C8.
_C8_CALLING_SERVICES: frozenset[str] = frozenset(
    {"c2", "c3", "c4", "c4_openhands"}
)


def _load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fp:
        loaded = yaml.safe_load(fp) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"expected a YAML mapping at top of {path}")
    return loaded


@pytest.mark.coherence
def test_mock_llm_is_in_test_profile_only() -> None:
    """mock_llm SHALL declare `profiles: [test]` so it is NOT started
    by `docker compose up` in the dev stack (which does not pass
    `--profile test`). This is the structural fix for the 2026-05-13
    incident where c4 silently routed to mock_llm in dev."""
    base = _load(_BASE)
    svc = base.get("services", {}).get("mock_llm")
    assert svc is not None, "mock_llm service missing from base compose"
    profiles = svc.get("profiles") or []
    assert isinstance(profiles, list), "mock_llm.profiles must be a list"
    assert "test" in profiles, (
        f"mock_llm.profiles SHALL contain 'test' (found {profiles!r}) — "
        "leaving it unguarded re-introduces the dev-stack regression."
    )


@pytest.mark.coherence
def test_no_dev_service_depends_on_mock_llm() -> None:
    """Once mock_llm is profile-gated, no service started in the dev
    stack SHALL list it under `depends_on`. compose ignores depends_on
    entries pointing to unstarted profiled services in v2+, but a
    leftover `depends_on: mock_llm` is a strong signal the author
    didn't realize the service is now gated — and is one rename away
    from breaking pytest e2e too."""
    base = _load(_BASE)
    offenders: list[str] = []
    for name, svc in (base.get("services") or {}).items():
        if name == "mock_llm":
            continue
        deps = svc.get("depends_on") or {}
        keys: Iterable[str]
        if isinstance(deps, dict):
            keys = list(deps.keys())
        elif isinstance(deps, list):
            keys = deps
        else:
            keys = []
        if "mock_llm" in keys:
            offenders.append(name)
    assert not offenders, (
        f"services depend on mock_llm but mock_llm is profile-gated: "
        f"{offenders}. Remove the dependency or move it to a "
        "test-only override file."
    )


@pytest.mark.coherence
def test_dev_override_routes_c8_calling_services_to_env_dev() -> None:
    """Every service in `_C8_CALLING_SERVICES` SHALL be re-declared in
    the dev override with `.env.dev` appended to its `env_file:`
    list. Missing means the service inherits only `.env.test` —
    whose `C8_GATEWAY_URL` points at mock_llm — and crashes on its
    first LLM call (the 2026-05-13 c4 regression)."""
    override = _load(_DEV_OVERRIDE)
    services = override.get("services") or {}
    offenders: list[str] = []
    for svc_name in sorted(_C8_CALLING_SERVICES):
        svc = services.get(svc_name)
        if svc is None:
            offenders.append(f"{svc_name} (service missing from override)")
            continue
        env_files = svc.get("env_file") or []
        if isinstance(env_files, str):
            env_files = [env_files]
        # Compose env_file supports BOTH short (string) and long
        # ({"path": ..., "required": ...}) entry syntax. The Tier-2
        # `.env.secret` is declared long-form with `required: false`,
        # so normalise every entry to its path string before the
        # `.endswith` check (a bare `f.endswith` would AttributeError
        # on the dict entry).
        env_paths = [
            (f["path"] if isinstance(f, dict) else f) for f in env_files
        ]
        if not any(p.endswith(".env.dev") for p in env_paths):
            offenders.append(
                f"{svc_name} (env_file={env_files!r} — missing .env.dev)"
            )
    assert not offenders, (
        "C8-calling services SHALL be in dev override with .env.dev:\n  "
        + "\n  ".join(offenders)
    )
