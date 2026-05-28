# =============================================================================
# File: test_config_override.py
# Version: 1
# Path: ay_platform_core/tests/contract/config_override/test_config_override.py
# Description: Parametrized contract test verifying that EVERY field on
#              EVERY BaseSettings subclass in `src/` is actually
#              overridable via its env-var. The companion coherence test
#              `test_env_completeness.py` proves the env files name the
#              right keys — this test proves setting those keys in the
#              environment actually changes the runtime value.
#
#              Both tests combined give the user confidence that:
#                1. The env file CAN override everything (names present).
#                2. Setting a value DOES override the default (effect
#                   actually propagates).
# =============================================================================

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings

pytestmark = pytest.mark.contract


# ---------------------------------------------------------------------------
# Discovery (kept symmetric with test_env_completeness.py)
# ---------------------------------------------------------------------------

_MONOREPO_ROOT = Path(__file__).resolve().parents[4]
_PY_SRC = _MONOREPO_ROOT / "ay_platform_core" / "src" / "ay_platform_core"
_SETTINGS_CARRIER_NAMES = ("config", "main")


def _discover_settings_classes() -> list[type[BaseSettings]]:
    for pyfile in _PY_SRC.rglob("*.py"):
        if pyfile.name not in {f"{n}.py" for n in _SETTINGS_CARRIER_NAMES}:
            continue
        rel = pyfile.relative_to(_MONOREPO_ROOT / "ay_platform_core" / "src")
        module_path = str(rel.with_suffix("")).replace("/", ".")
        importlib.import_module(module_path)

    seen: set[type[BaseSettings]] = set()

    def _recurse(cls: type[BaseSettings]) -> None:
        for sub in cls.__subclasses__():
            if sub in seen:
                continue
            seen.add(sub)
            _recurse(sub)

    _recurse(BaseSettings)
    return sorted(seen, key=lambda c: (c.__module__, c.__qualname__))


def _env_var_for_field(cls: type[BaseSettings], field_name: str, field: FieldInfo) -> str:
    alias = field.validation_alias
    if isinstance(alias, str):
        return alias.upper()
    prefix_raw = cls.model_config.get("env_prefix") or ""
    prefix: str = prefix_raw.upper() if isinstance(prefix_raw, str) else ""
    return f"{prefix}{field_name.upper()}"


# ---------------------------------------------------------------------------
# Override-value generator: produce a value that is GUARANTEED distinct
# from the field's default and VALID against its declared type.
# ---------------------------------------------------------------------------


def _override_for(field: FieldInfo) -> tuple[str, Any]:  # noqa: PLR0912 — type-dispatch + Field constraints (ge/le) require this branching
    """Return (env_string_value, expected_runtime_value) for a field.

    The env value is the string that would appear in a .env file; the
    runtime value is what Pydantic SHALL parse it to. Pydantic-settings
    does the str→type coercion for us; we just need to:
      - produce something DIFFERENT from the field's default;
      - produce something VALID against the field's type.

    Strategy:
      - Literal / Enum : pick the first allowed value that isn't the default.
      - bool           : flip the default.
      - int / float    : default ± 1.
      - str            : default + "-override", or a fresh value.
    """
    import typing  # noqa: PLC0415

    annotation = field.annotation
    default = field.default

    # Unwrap `Annotated[T, ...]` / `Optional[T]` → T
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Literal:
        # Pick any value different from the default
        for candidate in args:
            if candidate != default:
                return str(candidate), candidate
        pytest.skip(
            f"Literal field has a single allowed value — nothing to flip "
            f"({field!r})"
        )

    # Annotation is authoritative. NB: `1.0 in (True, False)` evaluates True
    # in Python (booleans are ints), so we MUST NOT use a `default in (...)`
    # short-circuit — that catches float fields whose default happens to
    # be 1.0 / 0.0 and routes them through the bool branch.
    if annotation is bool:
        new_value = not bool(default)
        return ("true" if new_value else "false", new_value)

    if annotation is int:
        new_int = int(default) + 1 if isinstance(default, int) else 42
        # Respect numeric constraints declared via Field(ge=..., le=...)
        for meta in field.metadata:
            ge = getattr(meta, "ge", None)
            le = getattr(meta, "le", None)
            if ge is not None and new_int < ge:
                new_int = ge + 1
            if le is not None and new_int > le:
                new_int = le - 1
        return str(new_int), new_int

    if annotation is float:
        new_float = float(default) + 1.0 if isinstance(default, (int, float)) else 1.25
        # Respect numeric constraints declared via Field(ge=..., le=...).
        for meta in field.metadata:
            ge = getattr(meta, "ge", None)
            le = getattr(meta, "le", None)
            if ge is not None and new_float < ge:
                new_float = float(ge) + 0.1
            if le is not None and new_float > le:
                # Pick a value inside [ge, le] that is not the default.
                lower = float(ge) if ge is not None else 0.0
                new_float = (lower + float(le)) / 2.0
                if new_float == default:
                    new_float = lower
        return str(new_float), new_float

    if annotation is str:
        # Keep a non-empty string and diverge from the default.
        new_str = f"{default}-override" if default else "override-value"
        return new_str, new_str

    # Fallback: treat as string
    new_fallback = f"{default or 'override'}-override"
    return new_fallback, new_fallback


# ---------------------------------------------------------------------------
# Parametrization — one test case per (class, field) across all Settings.
# ---------------------------------------------------------------------------


def _all_params() -> list[tuple[type[BaseSettings], str, FieldInfo]]:
    out: list[tuple[type[BaseSettings], str, FieldInfo]] = []
    for cls in _discover_settings_classes():
        for field_name, field in cls.model_fields.items():
            out.append((cls, field_name, field))
    return out


def _case_id(param: tuple[type[BaseSettings], str, FieldInfo]) -> str:
    cls, field_name, _ = param
    return f"{cls.__name__}.{field_name}"


@pytest.mark.contract
@pytest.mark.parametrize("param", _all_params(), ids=_case_id)
def test_env_var_actually_overrides_default(
    param: tuple[type[BaseSettings], str, FieldInfo],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting the field's env-var in the environment SHALL change the
    runtime value (i.e., the default is NOT silently kept).

    Ensures no Pydantic-settings class silently ignores the env layer —
    a legitimate risk with fields that use a non-standard alias config
    or were refactored away from pydantic-settings without us noticing.
    """
    cls, field_name, field = param
    env_name = _env_var_for_field(cls, field_name, field)
    env_value, expected = _override_for(field)

    # Clear any env vars the outer environment may have set for this field,
    # then assign the override.
    monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv(env_name, env_value)

    try:
        instance = cls()
    except Exception as exc:
        pytest.fail(
            f"{cls.__name__}() raised when {env_name}={env_value!r}: {exc}"
        )

    actual = getattr(instance, field_name)
    # For complex types (e.g., list[str]) add conversions here as needed.
    assert actual == expected, (
        f"{cls.__name__}.{field_name}: env {env_name}={env_value!r} did not "
        f"override default — got {actual!r}, expected {expected!r}"
    )
