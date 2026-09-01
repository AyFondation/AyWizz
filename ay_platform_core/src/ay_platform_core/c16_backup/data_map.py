# =============================================================================
# File: data_map.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c16_backup/data_map.py
# Description: The authoritative DataMap (E-900-001 / R-900-001): every
#              ArangoDB collection + MinIO bucket that holds tenant/project
#              data, its scope, its secret fields, and — separately — every
#              store DELIBERATELY excluded, with a reason. A coherence test
#              (R-900-001) asserts every real collection in the codebase is
#              either mapped here or explicitly excluded, so a new component's
#              store cannot silently escape backup.
#
# @relation implements:R-900-001
# =============================================================================

from __future__ import annotations

from ay_platform_core.c16_backup.models import (
    BackupScope,
    DataMapEntry,
    ExcludedStore,
    KeyStrategy,
    Store,
)

MANIFEST_VERSION = 1

# ---------------------------------------------------------------------------
# INCLUDED — tenant/project business data that a snapshot captures.
# ---------------------------------------------------------------------------

_A = Store.ARANGO
_M = Store.MINIO
_T = BackupScope.TENANT
_P = BackupScope.PROJECT

DATA_MAP: tuple[DataMapEntry, ...] = (
    # ---- C2 Auth (identity) — TENANT scope only -------------------------
    DataMapEntry(store=_A, name="c2_tenants", component="c2_auth", scope=_T,
                 tenant_scoped=True, project_scoped=False,
                 key_strategy=KeyStrategy.TENANT_ID),  # _key == tenant_id
    DataMapEntry(store=_A, name="c2_users", component="c2_auth", scope=_T,
                 tenant_scoped=True, project_scoped=False,
                 secret_fields=("argon2id_hash",),
                 note="password hash blanked on export (R-900-010)"),
    DataMapEntry(store=_A, name="c2_role_assignments", component="c2_auth",
                 scope=_T, tenant_scoped=True, project_scoped=True,
                 note="grants recreated on tenant restore; project restore "
                      "makes the restorer owner instead"),
    DataMapEntry(store=_A, name="c2_projects", component="c2_auth", scope=_P,
                 tenant_scoped=True, project_scoped=True,
                 key_strategy=KeyStrategy.PROJECT_ID),
    DataMapEntry(store=_A, name="c2_user_preferences", component="c2_auth",
                 scope=_T, tenant_scoped=True, project_scoped=False,
                 note="user-keyed (by user_id); filtered via the tenant's "
                      "users at snapshot time"),
    # ---- C3 Conversation -------------------------------------------------
    DataMapEntry(store=_A, name="c3_conversations", component="c3_conversation",
                 scope=_P, tenant_scoped=True, project_scoped=True),
    DataMapEntry(store=_A, name="c3_messages", component="c3_conversation",
                 scope=_P, tenant_scoped=True, project_scoped=True),
    # ---- C4 Orchestrator -------------------------------------------------
    DataMapEntry(store=_A, name="c4_runs", component="c4_orchestrator",
                 scope=_P, tenant_scoped=True, project_scoped=True),
    DataMapEntry(store=_A, name="c4_artifact_runs", component="c4_orchestrator",
                 scope=_P, tenant_scoped=True, project_scoped=True),
    # ---- C5 Requirements -------------------------------------------------
    DataMapEntry(store=_A, name="req_documents", component="c5_requirements",
                 scope=_P, tenant_scoped=True, project_scoped=True),
    DataMapEntry(store=_A, name="req_entities", component="c5_requirements",
                 scope=_P, tenant_scoped=True, project_scoped=True),
    DataMapEntry(store=_A, name="req_relations", component="c5_requirements",
                 scope=_P, tenant_scoped=True, project_scoped=True),
    DataMapEntry(store=_A, name="req_history", component="c5_requirements",
                 scope=_P, tenant_scoped=True, project_scoped=True),
    # ---- C6 Validation ---------------------------------------------------
    DataMapEntry(store=_A, name="c6_runs", component="c6_validation",
                 scope=_P, tenant_scoped=True, project_scoped=True),
    DataMapEntry(store=_A, name="c6_findings", component="c6_validation",
                 scope=_P, tenant_scoped=True, project_scoped=True),
    # ---- C7 Memory -------------------------------------------------------
    DataMapEntry(store=_A, name="memory_chunks", component="c7_memory",
                 scope=_P, tenant_scoped=True, project_scoped=True,
                 key_strategy=KeyStrategy.COMPOSITE),  # _key = {tenant}:{project}:{chunk}
    DataMapEntry(store=_A, name="memory_sources", component="c7_memory",
                 scope=_P, tenant_scoped=True, project_scoped=True),
    DataMapEntry(store=_A, name="memory_links", component="c7_memory",
                 scope=_P, tenant_scoped=True, project_scoped=True),
    DataMapEntry(store=_A, name="memory_project_config", component="c7_memory",
                 scope=_P, tenant_scoped=True, project_scoped=True),
    DataMapEntry(store=_A, name="memory_kg_entities", component="c7_memory",
                 scope=_P, tenant_scoped=True, project_scoped=True),
    DataMapEntry(store=_A, name="memory_kg_relations", component="c7_memory",
                 scope=_P, tenant_scoped=True, project_scoped=True),
    # ---- C8 LLM (TENANT catalogue + project selection only; the GLOBAL
    #      platform registries are excluded, see below) -------------------
    DataMapEntry(store=_A, name="tenant_llm_catalog", component="c8_llm",
                 scope=_T, tenant_scoped=True, project_scoped=False),
    DataMapEntry(store=_A, name="tenant_llm_catalog_meta", component="c8_llm",
                 scope=_T, tenant_scoped=True, project_scoped=False),
    DataMapEntry(store=_A, name="project_llm_models", component="c8_llm",
                 scope=_P, tenant_scoped=True, project_scoped=True),
    DataMapEntry(store=_A, name="embedding_catalog", component="c8_llm",
                 scope=_T, tenant_scoped=True, project_scoped=False),
    DataMapEntry(store=_A, name="embedding_project_selection", component="c8_llm",
                 scope=_P, tenant_scoped=True, project_scoped=True,
                 key_strategy=KeyStrategy.COMPOSITE),  # _key = {tenant}:{project}
    # ---- MinIO object stores (all project-scoped by key prefix) ----------
    DataMapEntry(store=_M, name="memory", component="c7_memory", scope=_P,
                 tenant_scoped=True, project_scoped=True,
                 key_note="sources/{tenant}/{project}/... and "
                          "{tenant}/{project}/.../runs/..."),
    DataMapEntry(store=_M, name="requirements", component="c5_requirements",
                 scope=_P, tenant_scoped=True, project_scoped=True,
                 key_note="projects/{project}/requirements/... and "
                          "sources/{tenant}/{project}/..."),
    DataMapEntry(store=_M, name="orchestrator", component="c4_orchestrator",
                 scope=_P, tenant_scoped=True, project_scoped=True,
                 key_note="artifact-run keys, project/run scoped"),
    DataMapEntry(store=_M, name="validation", component="c6_validation",
                 scope=_P, tenant_scoped=False, project_scoped=True,
                 key_note="validation-reports/{project}/..."),
    DataMapEntry(store=_M, name="c13-extractor-artifacts",
                 component="c13_extractor", scope=_P,
                 tenant_scoped=True, project_scoped=True,
                 key_note="extraction artifacts per tenant/project"),
)

# ---------------------------------------------------------------------------
# EXCLUDED — real stores deliberately NOT backed up, each with a reason.
# ---------------------------------------------------------------------------

EXCLUDED: tuple[ExcludedStore, ...] = (
    # Transient / operational (a fresh restore starts clean).
    ExcludedStore(store=_A, name="c2_sessions",
                  reason="transient JWT sessions (tokens); not business data"),
    ExcludedStore(store=_A, name="req_idempotency",
                  reason="operational idempotency keys; transient"),
    ExcludedStore(store=_A, name="req_reindex_jobs",
                  reason="operational reindex job tracking; transient"),
    ExcludedStore(store=_A, name="llm_calls",
                  reason="usage/metering history; a restored tenant starts clean"),
    ExcludedStore(store=_A, name="llm_quota_policy",
                  reason="operational quota state; restored tenant starts clean"),
    ExcludedStore(store=_A, name="llm_quota_anchors",
                  reason="operational quota anchors; restored tenant starts clean"),
    ExcludedStore(store=_A, name="storage_snapshots",
                  reason="operational storage-metering history"),
    ExcludedStore(store=_A, name="backup_records",
                  reason="C16's own backup registry; not itself backed up"),
    ExcludedStore(store=_A, name="c2_audit",
                  reason="append-only governance audit trail; operational "
                         "history, not restored into a fresh tenant"),
    # Secret store — never travels (R-900-010).
    ExcludedStore(store=_A, name="c2_project_secrets",
                  reason="project service-account credentials (R-900-010); "
                         "excluded entirely, re-provisioned after restore"),
    # GLOBAL platform registries (D-011) — app-level, not tenant data; several
    # also hold secrets, so they never travel.
    ExcludedStore(store=_A, name="llm_registry",
                  reason="GLOBAL platform LLM registry (D-011); not tenant data"),
    ExcludedStore(store=_A, name="llm_providers",
                  reason="GLOBAL platform registry + secret provider keys (D-011)"),
    ExcludedStore(store=_A, name="embedding_providers",
                  reason="GLOBAL platform embedding registry + secret keys (D-011)"),
    ExcludedStore(store=_A, name="embedding_models",
                  reason="GLOBAL platform embedding registry (D-011)"),
    ExcludedStore(store=_A, name="c8_model_pricing",
                  reason="GLOBAL platform pricing catalogue; not tenant data"),
    # MinIO operational bucket.
    ExcludedStore(store=_M, name="llm-archive",
                  reason="operational LLM call archive/metering; not business data"),
)


def entries_for_scope(scope: BackupScope) -> tuple[DataMapEntry, ...]:
    """DataMap entries captured by a snapshot of `scope`. A TENANT snapshot
    captures everything (tenant- + project-scoped); a PROJECT snapshot captures
    only project-scoped entries."""
    if scope is BackupScope.TENANT:
        return DATA_MAP
    return tuple(e for e in DATA_MAP if e.scope is BackupScope.PROJECT)


def mapped_names(store: Store) -> frozenset[str]:
    """All names (included + excluded) classified for a store — the set the
    completeness test checks the codebase against."""
    return frozenset(
        [e.name for e in DATA_MAP if e.store is store]
        + [e.name for e in EXCLUDED if e.store is store]
    )
