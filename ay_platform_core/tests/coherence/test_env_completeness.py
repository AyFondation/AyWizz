# =============================================================================
# File: test_env_completeness.py
# Version: 2
# Path: ay_platform_core/tests/coherence/test_env_completeness.py
# Description: Coherence checks that tie the application's Pydantic-settings
#              classes to the canonical env files (`.env.example` at the
#              monorepo root, and every `.env*` file under this tests/
#              directory). Two invariants are enforced:
#
#                1. COMPLETENESS — every field of every BaseSettings
#                   subclass discovered in `src/` has a matching entry in
#                   each env file. Missing entries mean operators cannot
#                   override a platform knob via env.
#
#                2. NO ORPHANS — every variable declared in an env file
#                   either (a) corresponds to a live Settings field or
#                   (b) belongs to the `_INFRA_BOOTSTRAP_VARS` whitelist
#                   (variables consumed by Docker images / init
#                   containers, not by Python code — e.g. ArangoDB and
#                   MinIO root credentials per R-100-118 v2). Orphan
#                   lines outside both categories signal stale config.
#
#              The env-var name for a given field is derived as follows:
#                - If the field has a `validation_alias` (string), that is
#                  the env-var name (no prefix applied).
#                - Otherwise, the name is `f"{env_prefix}{field_name}".upper()`.
#
#              Discovery walks the `src/` tree and imports every `config.py`
#              and `main.py` — these are the canonical homes of Settings
#              subclasses in the repo (§4.1). Extending to other module
#              paths is a follow-up if a new convention emerges.
#
#              v2: added the infra-bootstrap whitelist for variables that
#              live in the env file by design (R-100-110 v2 — single
#              source of truth) but are NOT mapped to Pydantic Settings
#              because they are consumed only by Docker images / init
#              containers (e.g. `ARANGO_ROOT_PASSWORD`, `MINIO_ROOT_USER`).
#              See R-100-118 v2.
#
# @relation implements:R-100-113
# =============================================================================

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Any

import pytest
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings

pytestmark = pytest.mark.coherence


# ---------------------------------------------------------------------------
# Discovery of Settings classes
# ---------------------------------------------------------------------------

# Repository layout: this test lives at
#   ay_platform_core/tests/coherence/test_env_completeness.py
# so we walk five directories up to reach the monorepo root.
_MONOREPO_ROOT = Path(__file__).resolve().parents[3]
_PY_SRC = _MONOREPO_ROOT / "ay_platform_core" / "src" / "ay_platform_core"
_ENV_EXAMPLE = _MONOREPO_ROOT / ".env.example"
_TESTS_DIR = _MONOREPO_ROOT / "ay_platform_core" / "tests"

# Modules commonly holding BaseSettings subclasses. We import them all,
# then collect subclasses via __subclasses__(). The trade-off: any module
# that imports `BaseSettings` but never subclasses it is a no-op; any
# module that subclasses but isn't imported here is missed. The current
# repo keeps every Settings class in `config.py` or `main.py` — extend
# this list if that convention shifts.
_SETTINGS_CARRIER_NAMES = ("config", "main")


# Variables that legitimately appear in every env file but are NOT mapped
# to a Pydantic Settings field — they are consumed by Docker images and
# init containers, not by Python code. Adding a name here is a deliberate
# act: it widens the surface of "values that can sit in the env file with
# no code-side reader". Keep this list short and document each entry
# (R-100-118 v2).
_INFRA_BOOTSTRAP_VARS: frozenset[str] = frozenset(
    {
        # Backend bootstrap admin credentials — used by:
        #   - the `arangodb` and `minio` Docker services (init password)
        #   - the `arangodb_init` and `minio_init` one-shot containers
        #     when they create the runtime app users (R-100-118 v2).
        # Never read by any Python component at runtime.
        "ARANGO_ROOT_PASSWORD",
        "ARANGO_ROOT_USERNAME",
        "MINIO_ROOT_PASSWORD",
        "MINIO_ROOT_USER",
        # Host-published port mapping (R-100-122). Consumed by Compose
        # via ${VAR} substitution + by the n8n WEBHOOK_URL composition.
        # No Pydantic Settings field reads them — Python services bind
        # 0.0.0.0:8000 inside their container regardless of host mapping.
        "PORT_C1_DASHBOARD",
        "PORT_C1_PUBLIC",
        "PORT_MOCK_LLM",
        "PORT_OBSERVABILITY",
    }
)


def _discover_settings_classes() -> list[type[BaseSettings]]:
    """Import every `config.py` / `main.py` under `src/ay_platform_core/`
    and return the BaseSettings subclasses found in the process.

    Uses `BaseSettings.__subclasses__()` post-import; the return order is
    sorted by (module, qualname) for deterministic test output.
    """
    seen: set[type[BaseSettings]] = set()
    for pyfile in _PY_SRC.rglob("*.py"):
        if pyfile.name not in {f"{name}.py" for name in _SETTINGS_CARRIER_NAMES}:
            continue
        rel = pyfile.relative_to(_MONOREPO_ROOT / "ay_platform_core" / "src")
        module_path = str(rel.with_suffix("")).replace("/", ".")
        try:
            importlib.import_module(module_path)
        except Exception as exc:  # pragma: no cover — surfaces import-time faults
            raise RuntimeError(
                f"failed to import {module_path} during env coherence discovery: {exc}"
            ) from exc

    # Recursively collect all descendants of BaseSettings. Settings classes
    # may themselves have subclasses (pydantic defines none by default).
    def _recurse(cls: type[BaseSettings]) -> None:
        for sub in cls.__subclasses__():
            if sub in seen:
                continue
            seen.add(sub)
            _recurse(sub)

    _recurse(BaseSettings)
    return sorted(seen, key=lambda c: (c.__module__, c.__qualname__))


def _env_var_for_field(cls: type[BaseSettings], field_name: str, field: FieldInfo) -> str:
    """Return the env-var name a given field actually reads from.

    Rule (mirrors pydantic-settings behaviour):
      - `validation_alias` wins and is applied verbatim (uppercased).
      - Otherwise `env_prefix` from the class model_config is concatenated
        with the uppercased field name.
    """
    alias = field.validation_alias
    if isinstance(alias, str):
        return alias.upper()
    prefix_raw = cls.model_config.get("env_prefix") or ""
    prefix: str = prefix_raw.upper() if isinstance(prefix_raw, str) else ""
    return f"{prefix}{field_name.upper()}"


def _expected_env_vars() -> dict[str, tuple[type[BaseSettings], str]]:
    """Map env-var name → (owning Settings class, field name)."""
    out: dict[str, tuple[type[BaseSettings], str]] = {}
    for cls in _discover_settings_classes():
        for field_name, field in cls.model_fields.items():
            env = _env_var_for_field(cls, field_name, field)
            # Sharing a validation_alias across classes is legitimate for
            # platform-wide knobs like PLATFORM_ENVIRONMENT. Prefix-based
            # collisions, however, would indicate a bug in env_prefix
            # assignments — fail loudly.
            prior = out.get(env)
            if prior is not None and prior != (cls, field_name):
                prior_cls, prior_field = prior
                # Accept the collision ONLY when both share the SAME
                # validation_alias (intentional platform-wide knob).
                is_aliased_here = isinstance(field.validation_alias, str)
                prior_alias = prior_cls.model_fields[prior_field].validation_alias
                is_aliased_prior = isinstance(prior_alias, str)
                if not (is_aliased_here and is_aliased_prior):
                    raise AssertionError(
                        f"env-var {env} collision: "
                        f"{prior_cls.__name__}.{prior_field} vs "
                        f"{cls.__name__}.{field_name}"
                    )
                # Keep the first owner; both are valid readers.
                continue
            out[env] = (cls, field_name)
    return out


# ---------------------------------------------------------------------------
# Env-file parsing (minimal .env grammar: KEY=value, `#` comments)
# ---------------------------------------------------------------------------


def _parse_env_file(path: Path) -> dict[str, str]:
    """Return a dict of env-var name → raw value.

    Unquotes surrounding single/double quotes; preserves inline values
    verbatim otherwise. Blank lines and `#`-prefixed lines are ignored.
    Duplicate keys fail the test — the env file SHALL declare each key
    exactly once.
    """
    out: dict[str, str] = {}
    if not path.is_file():
        raise AssertionError(f"env file missing: {path}")
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise AssertionError(f"{path}:{lineno} malformed line: {raw!r}")
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        if key in out:
            raise AssertionError(f"{path}:{lineno} duplicate key: {key}")
        out[key] = value
    return out


def _discover_env_files() -> list[Path]:
    """Env files to audit: `.env.example` (root) + every `.env*` under tests/."""
    files: list[Path] = [_ENV_EXAMPLE]
    for path in _TESTS_DIR.glob(".env*"):
        if path.is_file():
            files.append(path)
    return sorted(files)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.coherence
class TestSettingsDiscovery:
    def test_discovery_finds_every_known_component(self) -> None:
        """Sanity: the discovery walk SHALL find at least one Settings per
        component. Guards against a Settings class drifting into a file
        name the discovery ignores (e.g., `settings.py` instead of
        `config.py`)."""
        classes = _discover_settings_classes()
        module_prefixes = {c.__module__.rsplit(".", 1)[0] for c in classes}
        # Expect at least one Settings class from each component family
        # that is known to own one.
        for required in (
            "ay_platform_core.c2_auth",
            "ay_platform_core.c3_conversation",
            "ay_platform_core.c4_orchestrator",
            "ay_platform_core.c5_requirements",
            "ay_platform_core.c6_validation",
            "ay_platform_core.c7_memory",
            "ay_platform_core.c8_llm",
            "ay_platform_core.c9_mcp",
        ):
            assert any(mod.startswith(required) for mod in module_prefixes), (
                f"no Settings class discovered under {required}. "
                f"Discovered prefixes: {sorted(module_prefixes)}"
            )


@pytest.mark.coherence
@pytest.mark.parametrize(
    "env_path",
    _discover_env_files(),
    ids=lambda p: p.name if isinstance(p, Path) else str(p),
)
class TestEnvFileCompleteness:
    """Each audited env file SHALL be in lockstep with the discovered
    Settings fields — no missing vars, no orphan vars."""

    def test_every_settings_field_has_env_entry(self, env_path: Path) -> None:
        expected = _expected_env_vars()
        parsed = _parse_env_file(env_path)
        missing = sorted(set(expected.keys()) - set(parsed.keys()))
        assert not missing, (
            f"{env_path.name} missing {len(missing)} env var(s): {missing}. "
            f"Each corresponds to a field on a live BaseSettings subclass."
        )

    def test_every_env_entry_corresponds_to_a_settings_field(
        self, env_path: Path
    ) -> None:
        expected = _expected_env_vars()
        parsed = _parse_env_file(env_path)
        # Allowed = Settings-field names UNION infra-bootstrap whitelist.
        allowed = set(expected.keys()) | _INFRA_BOOTSTRAP_VARS
        orphans = sorted(set(parsed.keys()) - allowed)
        assert not orphans, (
            f"{env_path.name} carries {len(orphans)} orphan var(s): {orphans}. "
            f"Either restore the matching Settings field, drop the line, or "
            f"add the variable to _INFRA_BOOTSTRAP_VARS if it is consumed "
            f"only by Docker images / init containers (R-100-118 v2)."
        )


@pytest.mark.coherence
class TestEnvFileSharedShape:
    """`.env.example` and every `.env.test*` under tests/ SHALL expose the
    same key set. Values may differ (that's the whole point of env
    files), but drift in the KEYS themselves indicates the files were
    updated independently."""

    def test_env_test_key_set_matches_example(self) -> None:
        if not _ENV_EXAMPLE.is_file():
            pytest.skip(".env.example missing — separately flagged by completeness")
        example_keys = set(_parse_env_file(_ENV_EXAMPLE).keys())
        for path in _TESTS_DIR.glob(".env*"):
            if not path.is_file():
                continue
            test_keys = set(_parse_env_file(path).keys())
            only_example = example_keys - test_keys
            only_test = test_keys - example_keys
            assert not only_example, (
                f"{path.name} is missing keys present in .env.example: "
                f"{sorted(only_example)}"
            )
            assert not only_test, (
                f"{path.name} declares keys absent from .env.example: "
                f"{sorted(only_test)}"
            )


def _consume_any(_: Any) -> None:
    """Keep inspect import alive for mypy — used indirectly via pydantic."""
    inspect.getmodule(_)
