# =============================================================================
# File: config.py
# Version: 3
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/config.py
# Description: Runtime settings for the C4 Orchestrator.
#
#              v3 (2026-05-22): OpenHands engine knobs (V2 #2 / R-200-029) —
#              `C4_OPENHANDS_MODEL` (a C8 model_list name prefixed
#              `litellm_proxy/`) + `C4_OPENHANDS_MAX_ITERATIONS`. Consumed
#              only when `generate_engine == "openhands"`.
#
#              v2: env-var single-source refactor (R-100-110 v2, R-100-111
#              v2). Shared infra params (Arango, MinIO endpoint + creds,
#              platform environment) are read from UNPREFIXED env vars via
#              validation_alias. Only fields that legitimately differ
#              between components keep the `C4_` prefix (caps, timeouts,
#              dispatcher backend, MinIO bucket).
#
# @relation implements:R-100-111
# @relation implements:R-100-110
# @relation implements:R-100-112
# =============================================================================

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class OrchestratorConfig(BaseSettings):
    """C4 runtime settings."""

    model_config = SettingsConfigDict(
        env_prefix="c4_", extra="ignore", populate_by_name=True
    )

    # ---- Platform-wide (read without prefix via validation_alias) -----------
    platform_environment: Literal["development", "testing", "staging", "production"] = (
        Field(default="development", validation_alias="PLATFORM_ENVIRONMENT")
    )

    # Shared ArangoDB connection
    arango_url: str = Field(
        default="http://arangodb:8529", validation_alias="ARANGO_URL"
    )
    arango_db: str = Field(default="platform", validation_alias="ARANGO_DB")
    arango_username: str = Field(default="ay_app", validation_alias="ARANGO_USERNAME")
    arango_password: str = Field(
        default="changeme", validation_alias="ARANGO_PASSWORD"
    )

    # Shared MinIO connection
    minio_endpoint: str = Field(default="minio:9000", validation_alias="MINIO_ENDPOINT")
    minio_access_key: str = Field(default="ay_app", validation_alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(
        default="changeme", validation_alias="MINIO_SECRET_KEY"
    )
    minio_secure: bool = Field(default=False, validation_alias="MINIO_SECURE")

    # ---- C4-specific (C4_ prefix) ------------------------------------------
    # Each component owns its own bucket; the bucket name DOES legitimately
    # differ across components, so it stays prefixed.
    minio_bucket: str = "orchestrator"

    # Context enrichment cap (R-200-040)
    enrichment_round_cap: int = Field(default=3, ge=0)
    # Three-fix rule threshold (R-200-051)
    fix_attempt_cap: int = Field(default=3, ge=1)
    # Sub-agent pod hard timeout (R-200-032). In the in-process
    # dispatcher this bounds the LLM call + post-processing duration.
    sub_agent_timeout_seconds: int = Field(default=900, ge=30)

    # Whether to use the real K8s pod dispatcher (future). Baseline v1:
    # in-process dispatcher without real pods (Q-200-001 / R-200-030
    # reserve the real dispatcher for infra-ready deployments).
    dispatcher_backend: str = Field(default="in-process")

    # `generate`-phase engine (V2 #2). `in_process` (default) keeps the
    # existing dispatcher path UNCHANGED ; `openhands` routes the generate
    # phase through `pipeline.generate_engine` (OpenHands harness, gated —
    # currently a POC stub). Other phases always use the dispatcher.
    generate_engine: Literal["in_process", "openhands"] = Field(default="in_process")

    # OpenHands engine knobs (V2 #2 / R-200-029). Consumed ONLY when
    # `generate_engine == "openhands"`. `openhands_model` is a C8
    # `model_list` name prefixed `litellm_proxy/` so the OpenHands LiteLLM
    # client routes THROUGH the C8 proxy (never a provider directly) ;
    # default = Claude Opus 4.7 per the Q13 POC. `max_iterations` bounds one
    # agent run (the SDK default of 500 is too high for a POC task).
    openhands_model: str = Field(
        default="litellm_proxy/claude-opus-flagship",
        validation_alias="C4_OPENHANDS_MODEL",
    )
    openhands_max_iterations: int = Field(
        default=50, ge=1, validation_alias="C4_OPENHANDS_MAX_ITERATIONS"
    )

    # Active domain plug-in selection (R-200-061 v2 / P4.a). Per-
    # deployment binding — one OrchestratorService instance hosts ONE
    # domain plug-in. Per-run cross-domain dispatch deferred to v2
    # (Q-200-012). Valid values : `code`, `documentation`.
    domain: str = Field(
        default="code",
        validation_alias="C4_DOMAIN",
        description="Production domain plug-in : `code` or `documentation`.",
    )

    # ---- Gitea backend (R-200-146..147) ----------------------------------
    # C4 talks to the bundled Gitea instance with the root admin
    # credentials so it can push artifacts to ANY project's repo
    # without a cross-component read of `c2_project_secrets`. The
    # per-project service account stays usable for operator-side
    # `git clone` from outside the cluster. Q-100-020 tracks the
    # prod migration to per-deployment vault tokens.
    gitea_base_url: str = Field(
        default="http://gitea:3000",
        validation_alias="C4_GITEA_BASE_URL",
        description="Base URL of the bundled Gitea instance. Empty "
        "disables artifact pushes (artifacts stay in MinIO only).",
    )
    gitea_admin_username: str = Field(
        default="aywizz",
        validation_alias="GITEA_ROOT_USERNAME",
        description="Root admin username for the bundled Gitea.",
    )
    gitea_admin_password: str = Field(
        default="change-me-gitea-root-password",
        validation_alias="GITEA_ROOT_PASSWORD",
        description="Root admin password for the bundled Gitea.",
    )

    # ---- NATS event publisher (R-200-070 / R-200-071) ---------------------
    # Empty string keeps the orchestrator on `NullPublisher` (events are
    # captured in the trace ledger only) — backward-compatible with v1
    # deployments that haven't deployed NATS yet. Non-empty enables the
    # JetStream publisher ; failures during connect raise at startup so
    # the lifespan surfaces the misconfig immediately.
    nats_url: str = Field(
        default="",
        validation_alias="C4_NATS_URL",
        description="NATS server URL(s) — comma-separated. Empty = "
        "disable NATS publishing, fall back to NullPublisher.",
    )
    nats_connect_timeout_seconds: float = Field(
        default=5.0,
        ge=0.1,
        validation_alias="C4_NATS_CONNECT_TIMEOUT_SECONDS",
    )

    # ---- K8s sub-agent dispatcher (R-200-030..033) -----------------------
    # All `C4_K8S_*`. Consumed ONLY when `dispatcher_backend == "k8s"`.
    # The `pod_view_*` settings describe how the POD sees MinIO / C8 —
    # on Docker Desktop K8s the pod uses `host.docker.internal` to
    # reach docker-compose services, in prod K8s it's the cluster
    # Service DNS.
    k8s_namespace: str = Field(default="c4-workers")
    k8s_image: str = Field(default="ay-api:local")
    k8s_image_pull_policy: str = Field(default="IfNotPresent")
    k8s_service_account_name: str = Field(default="c4-sub-agent")
    k8s_pod_view_minio_endpoint: str = Field(
        default="host.docker.internal:9000",
        description="MinIO endpoint as the sub-agent pod sees it.",
    )
    k8s_pod_view_c8_gateway_url: str = Field(
        default="http://host.docker.internal:4000/v1",
        description="C8 LLM gateway URL as the sub-agent pod sees it.",
    )
    k8s_pod_view_c8_default_model: str = Field(default="")
    # The single shared gateway credential, propagated to sub-agent pods
    # as `C8_GATEWAY_API_KEY` (same value the orchestrator itself uses).
    # Read from the un-prefixed `C8_GATEWAY_API_KEY` (validation_alias)
    # so there is ONE variable across C3/C4/sub-agents and the proxy.
    c8_gateway_api_key: str = Field(
        default="",
        validation_alias="C8_GATEWAY_API_KEY",
        description="Shared C8 gateway key injected into sub-agent pods.",
    )
    # Path to kubeconfig. Empty = in-cluster config first, fall back
    # to default kubeconfig discovery (~/.kube/config).
    k8s_kubeconfig_path: str = Field(default="")
