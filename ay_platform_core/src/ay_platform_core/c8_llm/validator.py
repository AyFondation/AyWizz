# =============================================================================
# File: validator.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/validator.py
# Description: Config-time validator (R-800-050). Verifies the LiteLLM YAML
#              agrees with the feature catalog defined in `catalog.py` —
#              refuses to apply a configuration that maps an agent to a
#              model missing a required feature. Exposed as a standalone
#              function for CLI/entrypoint use and as a library call for
#              tests.
#
# @relation implements:R-800-050
# @relation implements:R-800-051
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass

from ay_platform_core.c8_llm.catalog import AGENT_CATALOG, Feature
from ay_platform_core.c8_llm.config import LiteLLMConfig


@dataclass(frozen=True, slots=True)
class ConfigIssue:
    """One validation violation."""

    rule: str
    agent: str | None
    message: str


def validate_agent_routes(config: LiteLLMConfig) -> list[ConfigIssue]:
    """Enforce R-800-050 / R-800-051.

    - Every `agent_routes` target SHALL exist in `model_list`.
    - For every agent in both the catalog and the routes, the target model
      SHALL support every feature required by the catalog.
    - Agents in the routes but absent from the catalog are NOT errors —
      per R-800-051 they fall back to the default model at runtime and
      emit a warning, but config-time acceptance is intentional to allow
      forward declaration.
    """
    issues: list[ConfigIssue] = []
    model_by_name = {m.model_name: m for m in config.model_list}

    # agent_routes["default"] is the catch-all (R-800-030 step 3).
    for agent_name, model_name in config.agent_routes.items():
        if model_name not in model_by_name:
            issues.append(
                ConfigIssue(
                    rule="R-800-050",
                    agent=agent_name,
                    message=(
                        f"agent_routes[{agent_name!r}] = {model_name!r} but "
                        f"{model_name!r} is not declared in model_list"
                    ),
                )
            )
            continue
        if agent_name in AGENT_CATALOG:
            required, _ = AGENT_CATALOG[agent_name]
            declared: set[Feature] = set(model_by_name[model_name].model_info.features)
            missing = required - declared
            if missing:
                issues.append(
                    ConfigIssue(
                        rule="R-800-050",
                        agent=agent_name,
                        message=(
                            f"agent {agent_name!r} routed to {model_name!r} is "
                            "missing required feature(s): "
                            f"{sorted(m.value for m in missing)}"
                        ),
                    )
                )
    # R-800-030 step 3: a default route MAY exist but is not required at
    # validation time — missing default is a runtime warning, not a config
    # error.
    return issues


def validate_configuration(config: LiteLLMConfig) -> list[ConfigIssue]:
    """Top-level validator — composes all R-800-0xx checks.

    Current checks: agent-routes consistency. Additional checks (budget
    sanity, provider reachability, API key presence) are intentionally not
    performed here — they live in the admission controller or the proxy
    readiness probe (R-800-003).
    """
    return validate_agent_routes(config)
