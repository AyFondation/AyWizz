# =============================================================================
# File: config.py
# Version: 3
# Path: ay_platform_core/src/ay_platform_core/c8_llm/config.py
# Description: Pydantic schema for `litellm-config.yaml` (R-800-020, R-800-024).
#              Two jobs: (1) strict validation of the structure LiteLLM
#              consumes at deploy time; (2) canonical in-Python
#              representation that the agent/feature validator reads.
#              Client-side settings (gateway URL, timeouts) live in the
#              ClientSettings class also defined here.
#
#              v2 (2026-05-20) : ClientSettings gains `agent_routes_*` env
#              keys that feed `LLMGatewayClient`'s client-side route
#              resolver (R-800-030 v1 note — resolver MAY live in the SDK
#              in v1 ; Q-800-011 tracks the proxy-side admission move).
#
#              v3 (2026-05-21) : LiteLLMConfig accepts the two proxy-native
#              passthrough sections `general_settings` (master key) and
#              `litellm_settings` (callbacks — wires the cost forwarder)
#              so ONE YAML serves both the off-the-shelf LiteLLM proxy and
#              the AyWizz client/validator. Root stays extra="forbid" so a
#              typo in a KNOWN section is still caught.
#
# @relation implements:R-800-020
# @relation implements:R-800-024
# @relation implements:R-800-030
# =============================================================================

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ay_platform_core.c8_llm.catalog import Feature

# ---------------------------------------------------------------------------
# LiteLLM config (YAML mounted as a ConfigMap in K8s)
# ---------------------------------------------------------------------------


class LiteLLMParams(BaseModel):
    """Fields under `model_list[*].litellm_params:` in `litellm-config.yaml`.

    Mirrors LiteLLM's native schema but we only validate the fields the
    platform cares about. `api_key:` is read from an env var reference
    (`os.environ/ANTHROPIC_API_KEY` etc.) per R-800-021.
    """

    model_config = ConfigDict(extra="allow")

    model: str  # provider/model identifier, e.g. "anthropic/claude-opus-4-7"
    api_key: str | None = None
    api_base: str | None = None


class ModelInfo(BaseModel):
    """Metadata attached to each model entry (R-800-024). Strictly typed
    because the router and cost tracker depend on these fields."""

    model_config = ConfigDict(extra="forbid")

    display_name: str
    features: list[Feature]
    context_window: int = Field(ge=1)
    cost_per_million_input: float = Field(ge=0.0)
    cost_per_million_output: float = Field(ge=0.0)
    cost_per_million_cached: float | None = Field(default=None, ge=0.0)
    rate_limit_rpm: int | None = Field(default=None, ge=1)
    rate_limit_tpm: int | None = Field(default=None, ge=1)


class ModelEntry(BaseModel):
    """One entry of `model_list:` — a named model the router can resolve to."""

    model_config = ConfigDict(extra="forbid")

    model_name: str
    litellm_params: LiteLLMParams
    model_info: ModelInfo


class BudgetConfig(BaseModel):
    """Top-level `budgets:` section (R-800-061)."""

    model_config = ConfigDict(extra="forbid")

    default_hard_cap_usd_per_month: float = Field(default=100.0, ge=0.0)
    default_soft_cap_ratio: float = Field(default=0.8, ge=0.0, le=1.0)
    window: Literal["calendar_month_utc", "rolling_30_days"] = "calendar_month_utc"


class ArchivalConfig(BaseModel):
    """Top-level `archival:` section (R-800-090/091/092)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    minio_bucket: str = "llm-archive"
    encryption: Literal["sse-kms", "sse-c", "none"] = "none"


class RateLimitsConfig(BaseModel):
    """Top-level `rate_limits:` section (R-800-060)."""

    model_config = ConfigDict(extra="forbid")

    per_tenant_rpm: int | None = Field(default=None, ge=1)
    per_user_rpm: int | None = Field(default=None, ge=1)


class LiteLLMConfig(BaseModel):
    """Full root of `litellm-config.yaml`.

    Unknown top-level keys are forbidden so that typos in operational files
    are caught at validation time rather than silently ignored.
    """

    model_config = ConfigDict(extra="forbid")

    model_list: list[ModelEntry]
    # Agent routes: name → model_name. Validated against the feature
    # catalog by `validator.validate_agent_routes`.
    agent_routes: dict[str, str] = Field(default_factory=dict)
    budgets: BudgetConfig = Field(default_factory=BudgetConfig)
    archival: ArchivalConfig = Field(default_factory=ArchivalConfig)
    rate_limits: RateLimitsConfig = Field(default_factory=RateLimitsConfig)
    # Proxy-native passthrough sections (consumed by the off-the-shelf
    # LiteLLM proxy, opaque to the AyWizz validator). `general_settings`
    # carries the proxy master key ; `litellm_settings` registers the
    # cost-forwarder callback (R-800-070). Typed as free-form dicts so a
    # typo in a KNOWN AyWizz section is still caught (root extra=forbid)
    # while the proxy keeps full latitude over its own config.
    general_settings: dict[str, Any] | None = None
    litellm_settings: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Client-side settings (consumed by LLMGatewayClient)
# ---------------------------------------------------------------------------


class ClientSettings(BaseSettings):
    """Runtime configuration for the gateway client.

    Pydantic-settings reads from the environment (prefixed `C8_`) so that
    K8s deployments can inject the proxy address and default tenant
    without code changes.
    """

    model_config = SettingsConfigDict(env_prefix="c8_", extra="ignore")

    # ClusterIP service URL per R-800-001. Default assumes docker-compose
    # local dev where the proxy is reachable at http://c8:8000/v1.
    gateway_url: str = "http://c8:8000/v1"
    # Single shared gateway credential (R-800-012). EVERY C8 client
    # (C3, C4, sub-agents) presents this as `Authorization: Bearer …` ;
    # the LiteLLM proxy enforces it as its `general_settings.master_key`.
    # ONE value, ONE variable (`C8_GATEWAY_API_KEY`) — the real provider
    # key (ANTHROPIC_API_KEY) lives ONLY in the proxy, never in a client.
    # Empty in tests/Ollama-direct (mock_llm accepts any non-empty
    # Bearer ; the client falls back to a "no-auth" placeholder).
    # `validation_alias` (un-prefixed) so it is the SAME env var the
    # orchestrator reads for sub-agent pod injection — one shared knob.
    gateway_api_key: str = Field(default="", validation_alias="C8_GATEWAY_API_KEY")
    request_timeout_seconds: float = Field(default=60.0, ge=1.0)
    connect_timeout_seconds: float = Field(default=5.0, ge=0.1)
    # Streaming heartbeat interval (Q-800-003 baseline: 15 s).
    sse_heartbeat_seconds: float = Field(default=15.0, ge=1.0)
    # Default model identifier injected into `ChatCompletionRequest`
    # when the caller leaves `model` empty. Required when the gateway
    # is a strict OpenAI-compat provider (Ollama, LiteLLM with no
    # model alias) ; the mock LLM ignores `model`. Leave empty in
    # K8s prod, set per-environment via env_file.
    default_model: str = ""
    # ------------------------------------------------------------------
    # Client-side per-agent routing (R-800-030 v1 note).
    # ------------------------------------------------------------------
    # Path to the litellm config YAML. When set AND readable, the
    # gateway client loads its `agent_routes:` section at construction
    # and resolves `agent_name → model_name` BEFORE every call. When
    # the file is absent or `agent_routes:` is empty, the client falls
    # back to `default_model` per R-800-030 step 3.
    agent_routes_yaml_path: str | None = None
    # Inline override of agent_routes — useful for dev/test scenarios
    # without a YAML on disk. Parsed as JSON ; takes precedence over
    # `agent_routes_yaml_path` when set. Example value :
    # `{"c3-rag":"llama3-3b-local","c3-docgen":"claude-haiku-fast"}`.
    agent_routes_inline: str | None = None

    @property
    def effective_bearer(self) -> str:
        """The bearer a component presents to the gateway. The configured
        `C8_GATEWAY_API_KEY`, or a `"no-auth"` placeholder so the mock_llm
        (which only checks for a non-empty Bearer) and the Ollama-direct
        dev path (no auth) still send a valid Authorization header
        (R-800-012). Real deployments set the key ; the placeholder never
        authenticates against a real provider."""
        return self.gateway_api_key or "no-auth"
