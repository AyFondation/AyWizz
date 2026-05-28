---
document: 060-IMPLEMENTATION-STATUS
version: 1
path: requirements/060-IMPLEMENTATION-STATUS.md
language: en
status: draft
audience: any-fresh-session, contributor-onboarding
generated-by: ay_platform_core/scripts/checks/audit_implementation_status.py
---

# Implementation Status — cross-reference of R-* requirements vs. code

> **Generated** — re-run the audit script to refresh this file. The
> mapping is mechanical: it counts `@relation implements:R-…`
> markers in `ay_platform_core/src/` and `@relation validates:R-…`
> markers in `ay_platform_core/tests/`. Status legend:
>
> - `tested`: at least one implementer + at least one validating test.
> - `implemented`: at least one implementer, no `@relation validates:` marker.
>   (May still be tested via positional / functional tests — the marker
>   is a stronger guarantee than coverage of the code path.)
> - `test-only`: tests reference the requirement but no source file does.
>   Three legitimate sub-cases (do NOT need fixing):
>    - **Architectural meta-rules** (e.g. R-100-001 SRP, R-100-002 footprint) —
>      no single file implements them; the project structure as a whole does.
>    - **Test-as-implementation** (e.g. R-100-113 env coherence) — the test IS the
>      mechanism that enforces the requirement; the marker on the test is the implem.
>    - **WIP stubs** (e.g. R-300-080 import endpoint) — `status: draft` ; the impl is
>      a 501 stub validated by tests. Will move to `tested` once the v2 work lands.
>   The fourth case — stale marker after impl deletion — is what an audit catches.
> - `divergent`: requirement is `status: approved` in the spec, but **no**
>   marker exists in the codebase. Either the impl forgot the marker or
>   the requirement is unimplemented despite being approved.
> - `not-yet`: requirement is `status: draft`, no marker. Expected for v2 work.

## Summary

| Spec | Total | tested | implemented | test-only | divergent | not-yet |
|---|---|---|---|---|---|---|
| [100-SPEC-ARCHITECTURE](./100-SPEC-ARCHITECTURE.md) | 82 | 8 | 25 | 4 | 0 | 45 |
| [200-SPEC-PIPELINE-AGENT](./200-SPEC-PIPELINE-AGENT.md) | 70 | 23 | 28 | 0 | 0 | 19 |
| [300-SPEC-REQUIREMENTS-MGMT](./300-SPEC-REQUIREMENTS-MGMT.md) | 52 | 0 | 29 | 5 | 0 | 18 |
| [400-SPEC-MEMORY-RAG](./400-SPEC-MEMORY-RAG.md) | 46 | 9 | 11 | 1 | 0 | 25 |
| [500-SPEC-UI-UX](./500-SPEC-UI-UX.md) | 14 | 0 | 0 | 0 | 8 | 6 |
| [700-SPEC-VERTICAL-COHERENCE](./700-SPEC-VERTICAL-COHERENCE.md) | 24 | 2 | 22 | 0 | 0 | 0 |
| [800-SPEC-LLM-ABSTRACTION](./800-SPEC-LLM-ABSTRACTION.md) | 52 | 2 | 12 | 0 | 0 | 38 |
| **Total** | **340** | **44** | **127** | **10** | **8** | **151** |

## R-100-* — [100-SPEC-ARCHITECTURE](./100-SPEC-ARCHITECTURE.md)

| ID | v | status | overall | implementing | validating |
|---|---|---|---|---|---|
| `R-100-001` | v1 | draft | **test-only** | — | `ay_platform_core/tests/integration/c6_validation/test_regression_flow.py`, `ay_platform_core/tests/integration/c6_validation/test_run_flow.py`, `ay_platform_core/tests/unit/c6_validation/test_checks.py` (+2 more) |
| `R-100-002` | v1 | draft | **test-only** | — | `ay_platform_core/tests/integration/c6_validation/test_run_flow.py`, `ay_platform_core/tests/unit/c6_validation/test_parsers.py` |
| `R-100-003` | v1 | draft | **test-only** | — | `ay_platform_core/tests/integration/c3_conversation/test_rag_chat_flow.py` |
| `R-100-004` | v1 | draft | **not-yet** | — | — |
| `R-100-005` | v1 | draft | **not-yet** | — | — |
| `R-100-006` | v1 | draft | **not-yet** | — | — |
| `R-100-007` | v1 | draft | **not-yet** | — | — |
| `R-100-008` | v2 | draft | **not-yet** | — | — |
| `R-100-010` | v1 | draft | **not-yet** | — | — |
| `R-100-011` | v1 | draft | **not-yet** | — | — |
| `R-100-012` | v3 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c2_auth/db/repository.py` | — |
| `R-100-013` | v2 | draft | **not-yet** | — | — |
| `R-100-014` | v1 | draft | **not-yet** | — | — |
| `R-100-015` | v2 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c9_mcp/__init__.py`, `ay_platform_core/src/ay_platform_core/c9_mcp/main.py`, `ay_platform_core/src/ay_platform_core/c9_mcp/models.py` (+7 more) | — |
| `R-100-016` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/__init__.py` | — |
| `R-100-020` | v1 | draft | **not-yet** | — | — |
| `R-100-021` | v1 | draft | **not-yet** | — | — |
| `R-100-022` | v1 | draft | **not-yet** | — | — |
| `R-100-030` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c2_auth/__init__.py`, `ay_platform_core/src/ay_platform_core/c2_auth/main.py`, `ay_platform_core/src/ay_platform_core/c2_auth/modes/base.py` (+1 more) | — |
| `R-100-031` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c2_auth/modes/none_mode.py` | — |
| `R-100-032` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c2_auth/modes/none_mode.py` | — |
| `R-100-033` | v1 | draft | **not-yet** | — | — |
| `R-100-034` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c2_auth/modes/local_mode.py` | — |
| `R-100-035` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c2_auth/modes/local_mode.py` | — |
| `R-100-036` | v1 | draft | **not-yet** | — | — |
| `R-100-037` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c2_auth/modes/sso_mode.py` | — |
| `R-100-038` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c2_auth/models.py`, `ay_platform_core/src/ay_platform_core/c2_auth/service.py` | — |
| `R-100-039` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c2_auth/modes/local_mode.py`, `ay_platform_core/src/ay_platform_core/c2_auth/router.py`, `ay_platform_core/src/ay_platform_core/observability/auth_guard.py` (+1 more) | `ay_platform_core/tests/unit/observability/test_auth_guard.py` |
| `R-100-040` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c2_auth/router.py` | — |
| `R-100-041` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c2_auth/router.py` | — |
| `R-100-042` | v1 | draft | **not-yet** | — | — |
| `R-100-050` | v1 | draft | **not-yet** | — | — |
| `R-100-051` | v1 | draft | **not-yet** | — | — |
| `R-100-052` | v1 | draft | **not-yet** | — | — |
| `R-100-053` | v1 | draft | **not-yet** | — | — |
| `R-100-054` | v1 | draft | **not-yet** | — | — |
| `R-100-055` | v1 | draft | **not-yet** | — | — |
| `R-100-056` | v1 | draft | **not-yet** | — | — |
| `R-100-060` | v1 | draft | **not-yet** | — | — |
| `R-100-061` | v1 | draft | **not-yet** | — | — |
| `R-100-062` | v1 | draft | **not-yet** | — | — |
| `R-100-063` | v1 | draft | **not-yet** | — | — |
| `R-100-070` | v1 | draft | **not-yet** | — | — |
| `R-100-071` | v1 | draft | **not-yet** | — | — |
| `R-100-072` | v1 | draft | **not-yet** | — | — |
| `R-100-073` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c2_auth/service.py` | — |
| `R-100-074` | v1 | draft | **not-yet** | — | — |
| `R-100-075` | v2 | draft | **not-yet** | — | — |
| `R-100-080` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c7_memory/router.py`, `infra/c12_workflow/workflows/ingest_text_source.json`, `infra/k8s/base/c12_workflow/c12-workflow-configmap.yaml` | `ay_platform_core/tests/system/test_uploads_to_retrieval.py` |
| `R-100-081` | v2 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c7_memory/router.py`, `infra/c12_workflow/workflows/ingest_text_source.json`, `infra/k8s/base/c12_workflow/c12-workflow-configmap.yaml` | `ay_platform_core/tests/system/test_uploads_to_retrieval.py` |
| `R-100-082` | v1 | draft | **not-yet** | — | — |
| `R-100-083` | v1 | draft | **not-yet** | — | — |
| `R-100-084` | v1 | draft | **not-yet** | — | — |
| `R-100-085` | v1 | draft | **not-yet** | — | — |
| `R-100-086` | v1 | draft | **not-yet** | — | — |
| `R-100-087` | v1 | draft | **not-yet** | — | — |
| `R-100-088` | v1 | draft | **not-yet** | — | — |
| `R-100-100` | v1 | draft | **implemented** | `ay_platform_core/tests/docker-compose.yml` | — |
| `R-100-101` | v1 | draft | **not-yet** | — | — |
| `R-100-102` | v1 | draft | **not-yet** | — | — |
| `R-100-103` | v1 | draft | **not-yet** | — | — |
| `R-100-104` | v2 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/_observability/synthesis.py`, `ay_platform_core/src/ay_platform_core/observability/__init__.py`, `ay_platform_core/src/ay_platform_core/observability/config.py` (+3 more) | — |
| `R-100-105` | v2 | approved | **tested** | `ay_platform_core/src/ay_platform_core/_observability/synthesis.py`, `ay_platform_core/src/ay_platform_core/observability/__init__.py`, `ay_platform_core/src/ay_platform_core/observability/config.py` (+3 more) | `ay_platform_core/tests/integration/observability/test_trace_propagation.py` |
| `R-100-106` | v2 | draft | **not-yet** | — | — |
| `R-100-107` | v1 | draft | **not-yet** | — | — |
| `R-100-108` | v1 | draft | **not-yet** | — | — |
| `R-100-110` | v2 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/_sub_agent/config.py`, `ay_platform_core/src/ay_platform_core/c2_auth/config.py`, `ay_platform_core/src/ay_platform_core/c3_conversation/main.py` (+5 more) | — |
| `R-100-111` | v2 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/c2_auth/config.py`, `ay_platform_core/src/ay_platform_core/c3_conversation/main.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/config.py` (+5 more) | — |
| `R-100-112` | v1 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/c2_auth/config.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/config.py`, `ay_platform_core/src/ay_platform_core/c5_requirements/config.py` (+2 more) | — |
| `R-100-113` | v1 | approved | **test-only** | — | `ay_platform_core/tests/coherence/test_env_completeness.py` |
| `R-100-114` | v2 | approved | **tested** | `ay_platform_core/src/ay_platform_core/c2_auth/ux_router.py`, `ay_platform_core/src/ay_platform_core/c3_conversation/main.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/main.py` (+8 more) | `ay_platform_core/tests/integration/c2_auth/test_ux_config.py`, `ay_platform_core/tests/integration/c7_memory/test_remote_service.py`, `ay_platform_core/tests/system/k8s/test_basic_smoke.py` (+1 more) |
| `R-100-115` | v2 | approved | **implemented** | `ay_platform_core/tests/docker-compose.yml` | — |
| `R-100-116` | v1 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/_mock_llm/main.py`, `infra/k8s/base/_mock_llm/deployment.yaml` | — |
| `R-100-117` | v1 | approved | **tested** | `ay_platform_core/src/ay_platform_core/c3_conversation/main.py`, `ay_platform_core/src/ay_platform_core/c7_memory/remote.py`, `ay_platform_core/tests/docker-compose.yml` | `ay_platform_core/tests/system/k8s/test_basic_smoke.py` |
| `R-100-118` | v2 | approved | **tested** | `ay_platform_core/src/ay_platform_core/_sub_agent/config.py`, `ay_platform_core/src/ay_platform_core/c2_auth/main.py`, `ay_platform_core/src/ay_platform_core/observability/auth_guard.py` (+1 more) | `ay_platform_core/tests/integration/_credentials/test_arango_ay_app.py`, `ay_platform_core/tests/integration/_credentials/test_minio_ay_app.py`, `ay_platform_core/tests/integration/c2_auth/test_demo_seed.py` (+3 more) |
| `R-100-119` | v1 | approved | **implemented** | `ay_platform_core/tests/docker-compose.yml` | — |
| `R-100-120` | v1 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/_observability/buffer.py`, `ay_platform_core/src/ay_platform_core/_observability/collector.py`, `ay_platform_core/src/ay_platform_core/_observability/main.py` (+2 more) | — |
| `R-100-121` | v1 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/_observability/main.py`, `ay_platform_core/tests/docker-compose.yml` | — |
| `R-100-122` | v1 | approved | **implemented** | `ay_platform_core/tests/docker-compose.yml` | — |
| `R-100-123` | v1 | approved | **implemented** | `.github/workflows/ci-tests.yml` | — |
| `R-100-124` | v1 | approved | **tested** | `ay_platform_core/src/ay_platform_core/_observability/main.py`, `ay_platform_core/src/ay_platform_core/_observability/synthesis.py`, `ay_platform_core/src/ay_platform_core/observability/workflow/__init__.py` (+3 more) | `ay_platform_core/tests/integration/observability/workflow/test_elasticsearch_integration.py`, `ay_platform_core/tests/integration/observability/workflow/test_loki_integration.py`, `ay_platform_core/tests/unit/observability/workflow/test_router.py` |
| `R-100-125` | v2 | draft | **not-yet** | — | — |

## R-200-* — [200-SPEC-PIPELINE-AGENT](./200-SPEC-PIPELINE-AGENT.md)

| ID | v | status | overall | implementing | validating |
|---|---|---|---|---|---|
| `R-200-001` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/models.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/service.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/state.py` | — |
| `R-200-002` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/models.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/router.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/service.py` | — |
| `R-200-003` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/service.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/state.py` | — |
| `R-200-010` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/service.py` | — |
| `R-200-011` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/domains/code/plugin.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/domains/documentation/plugin.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/service.py` | `ay_platform_core/tests/unit/c4_orchestrator/test_documentation_domain_plugin.py` |
| `R-200-012` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/domains/code/plugin.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/domains/documentation/plugin.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/service.py` | `ay_platform_core/tests/unit/c4_orchestrator/test_documentation_domain_plugin.py` |
| `R-200-013` | v1 | draft | **not-yet** | — | — |
| `R-200-020` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/models.py` | — |
| `R-200-021` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/dispatcher/in_process.py` | — |
| `R-200-022` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/models.py` | — |
| `R-200-030` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/_sub_agent/__init__.py`, `ay_platform_core/src/ay_platform_core/_sub_agent/__main__.py`, `ay_platform_core/src/ay_platform_core/_sub_agent/runtime.py` (+6 more) | `ay_platform_core/tests/integration/c4_orchestrator/test_k8s_dispatcher_e2e.py`, `ay_platform_core/tests/unit/_sub_agent/test_runtime.py`, `ay_platform_core/tests/unit/c4_orchestrator/test_k8s_dispatcher.py` |
| `R-200-031` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/_sub_agent/runtime.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/dispatcher/k8s.py`, `infra/k8s/base/c4_orchestrator/serviceaccount.yaml` (+1 more) | `ay_platform_core/tests/unit/c4_orchestrator/test_k8s_dispatcher.py` |
| `R-200-032` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/_sub_agent/runtime.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/dispatcher/k8s.py`, `infra/k8s/base/c4_workers/rbac.yaml` | `ay_platform_core/tests/integration/c4_orchestrator/test_k8s_dispatcher_e2e.py`, `ay_platform_core/tests/unit/c4_orchestrator/test_k8s_dispatcher.py` |
| `R-200-033` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/_sub_agent/__init__.py`, `ay_platform_core/src/ay_platform_core/_sub_agent/models.py`, `ay_platform_core/src/ay_platform_core/_sub_agent/runtime.py` (+3 more) | `ay_platform_core/tests/integration/c4_orchestrator/test_k8s_dispatcher_e2e.py`, `ay_platform_core/tests/unit/_sub_agent/test_runtime.py`, `ay_platform_core/tests/unit/c4_orchestrator/test_dispatch_storage.py` (+1 more) |
| `R-200-040` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/service.py` | — |
| `R-200-041` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/service.py` | — |
| `R-200-050` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/service.py` | — |
| `R-200-051` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/service.py` | — |
| `R-200-052` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/service.py` | — |
| `R-200-060` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/domains/base.py` | — |
| `R-200-061` | v2 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/domains/base.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/domains/code/plugin.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/domains/documentation/plugin.py` | `ay_platform_core/tests/unit/c4_orchestrator/test_documentation_domain_plugin.py` |
| `R-200-062` | v1 | draft | **not-yet** | — | — |
| `R-200-070` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/events/base.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/events/nats_publisher.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/service.py` | `ay_platform_core/tests/integration/c4_orchestrator/test_nats_publisher_e2e.py`, `ay_platform_core/tests/unit/c4_orchestrator/test_nats_publisher.py` |
| `R-200-071` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/events/nats_publisher.py` | `ay_platform_core/tests/integration/c4_orchestrator/test_nats_publisher_e2e.py`, `ay_platform_core/tests/unit/c4_orchestrator/test_nats_publisher.py` |
| `R-200-080` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/db/repository.py` | — |
| `R-200-081` | v1 | draft | **not-yet** | — | — |
| `R-200-100` | v1 | draft | **not-yet** | — | — |
| `R-200-110` | v1 | draft | **not-yet** | — | — |
| `R-200-120` | v1 | draft | **not-yet** | — | — |
| `R-200-130` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_models.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_storage.py` | — |
| `R-200-131` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_models.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_router.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_service.py` (+2 more) | — |
| `R-200-132` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_models.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_router.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_service.py` (+1 more) | — |
| `R-200-133` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_router.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_service.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_storage.py` | — |
| `R-200-140` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c2_auth/gitea_client.py` | — |
| `R-200-141` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c2_auth/gitea_client.py` | — |
| `R-200-142` | v1 | draft | **not-yet** | — | — |
| `R-200-143` | v1 | draft | **not-yet** | — | — |
| `R-200-144` | v1 | draft | **not-yet** | — | — |
| `R-200-145` | v1 | draft | **not-yet** | — | — |
| `R-200-146` | v1 | draft | **not-yet** | — | — |
| `R-200-147` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c2_auth/gitea_client.py` | `ay_platform_core/tests/unit/c2_auth/test_gitea_client_contents.py`, `ay_platform_core/tests/unit/c4_orchestrator/test_doc_versioning.py` |
| `R-200-150` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/dispatcher/in_process.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/service.py` | — |
| `R-200-151` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/main.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/service.py` | — |
| `R-200-152` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/service.py` | — |
| `R-200-153` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c3_conversation/document_tools.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_service.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/documents_router.py` | — |
| `R-200-154` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_service.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/documents_router.py` | — |
| `R-200-155` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_service.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/documents_router.py` | — |
| `R-200-156` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c3_conversation/document_tools.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/documents_router.py` | — |
| `R-200-160` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_service.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/documents_router.py` | `ay_platform_core/tests/integration/c4_orchestrator/test_documents_structural_ops.py` |
| `R-200-161` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_service.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/documents_router.py` | `ay_platform_core/tests/integration/c4_orchestrator/test_documents_structural_ops.py` |
| `R-200-162` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_service.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/documents_router.py` | `ay_platform_core/tests/integration/c4_orchestrator/test_documents_structural_ops.py` |
| `R-200-163` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_service.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/documents_router.py` | `ay_platform_core/tests/integration/c4_orchestrator/test_documents_structural_ops.py` |
| `R-200-164` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_service.py` | — |
| `R-200-170` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/source_router.py` | `ay_platform_core/tests/integration/c4_orchestrator/test_source_api.py` |
| `R-200-171` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/source_router.py` | `ay_platform_core/tests/integration/c4_orchestrator/test_source_api.py` |
| `R-200-172` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/source_router.py` | `ay_platform_core/tests/integration/c4_orchestrator/test_source_api.py` |
| `R-200-173` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/source_router.py` | `ay_platform_core/tests/integration/c4_orchestrator/test_source_api.py` |
| `R-200-174` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/source_router.py` | `ay_platform_core/tests/integration/c4_orchestrator/test_source_api.py` |
| `R-200-175` | v1 | draft | **not-yet** | — | — |
| `R-200-180` | v1 | draft | **not-yet** | — | — |
| `R-200-181` | v1 | draft | **not-yet** | — | — |
| `R-200-182` | v1 | draft | **not-yet** | — | — |
| `R-200-183` | v1 | draft | **not-yet** | — | — |
| `R-200-184` | v1 | draft | **not-yet** | — | — |
| `R-200-200` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/models.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/service.py` | `ay_platform_core/tests/integration/c4_orchestrator/test_trace_and_steer_api.py`, `ay_platform_core/tests/unit/c4_orchestrator/test_trace_and_steer.py` |
| `R-200-201` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/db/repository.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/models.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/router.py` (+1 more) | `ay_platform_core/tests/integration/c4_orchestrator/test_trace_and_steer_api.py`, `ay_platform_core/tests/unit/c4_orchestrator/test_trace_and_steer.py` |
| `R-200-202` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/models.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/router.py`, `ay_platform_core/src/ay_platform_core/c4_orchestrator/service.py` | `ay_platform_core/tests/integration/c4_orchestrator/test_trace_and_steer_api.py`, `ay_platform_core/tests/unit/c4_orchestrator/test_trace_and_steer.py` |
| `R-200-203` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c4_orchestrator/service.py` | `ay_platform_core/tests/integration/c4_orchestrator/test_trace_and_steer_api.py`, `ay_platform_core/tests/unit/c4_orchestrator/test_trace_and_steer.py` |
| `R-200-204` | v1 | draft | **not-yet** | — | — |
| `R-200-205` | v1 | draft | **not-yet** | — | — |

## R-300-* — [300-SPEC-REQUIREMENTS-MGMT](./300-SPEC-REQUIREMENTS-MGMT.md)

| ID | v | status | overall | implementing | validating |
|---|---|---|---|---|---|
| `R-300-001` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/adapter/markdown.py`, `ay_platform_core/src/ay_platform_core/c5_requirements/models.py` | — |
| `R-300-002` | v1 | draft | **not-yet** | — | — |
| `R-300-003` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/adapter/markdown.py` | — |
| `R-300-004` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/adapter/markdown.py` | — |
| `R-300-005` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/adapter/markdown.py`, `ay_platform_core/src/ay_platform_core/c5_requirements/adapter/validator.py`, `ay_platform_core/src/ay_platform_core/c5_requirements/models.py` | — |
| `R-300-010` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/storage/minio_storage.py` | — |
| `R-300-011` | v1 | draft | **not-yet** | — | — |
| `R-300-012` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/db/repository.py` | — |
| `R-300-013` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/db/repository.py` | — |
| `R-300-020` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/router.py`, `ay_platform_core/src/ay_platform_core/c5_requirements/service.py` | — |
| `R-300-021` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/db/repository.py` | — |
| `R-300-022` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/service.py` | — |
| `R-300-023` | v1 | draft | **not-yet** | — | — |
| `R-300-024` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/models.py`, `ay_platform_core/src/ay_platform_core/c5_requirements/router.py` | — |
| `R-300-025` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/router.py` | — |
| `R-300-026` | v1 | draft | **not-yet** | — | — |
| `R-300-027` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/router.py` | — |
| `R-300-030` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/service.py` | — |
| `R-300-031` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/storage/minio_storage.py` | — |
| `R-300-032` | v1 | draft | **not-yet** | — | — |
| `R-300-033` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/service.py` | — |
| `R-300-034` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/service.py`, `ay_platform_core/src/ay_platform_core/c5_requirements/storage/minio_storage.py` | — |
| `R-300-040` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/db/repository.py` | — |
| `R-300-050` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/adapter/validator.py`, `ay_platform_core/src/ay_platform_core/c5_requirements/models.py`, `ay_platform_core/src/ay_platform_core/c5_requirements/service.py` | — |
| `R-300-051` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/adapter/validator.py` | — |
| `R-300-052` | v1 | draft | **not-yet** | — | — |
| `R-300-060` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/service.py` | — |
| `R-300-061` | v1 | draft | **not-yet** | — | — |
| `R-300-062` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/storage/minio_storage.py` | — |
| `R-300-063` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/router.py`, `ay_platform_core/src/ay_platform_core/c5_requirements/service.py` | — |
| `R-300-064` | v1 | draft | **not-yet** | — | — |
| `R-300-070` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/db/repository.py`, `ay_platform_core/src/ay_platform_core/c5_requirements/router.py`, `ay_platform_core/src/ay_platform_core/c5_requirements/service.py` | — |
| `R-300-071` | v1 | draft | **not-yet** | — | — |
| `R-300-072` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/service.py` | — |
| `R-300-073` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/router.py` | — |
| `R-300-080` | v1 | draft | **test-only** | — | `ay_platform_core/tests/integration/c5_requirements/test_import.py` |
| `R-300-081` | v1 | draft | **test-only** | — | `ay_platform_core/tests/integration/c5_requirements/test_import.py` |
| `R-300-082` | v1 | draft | **test-only** | — | `ay_platform_core/tests/integration/c5_requirements/test_import.py` |
| `R-300-083` | v1 | draft | **test-only** | — | `ay_platform_core/tests/integration/c5_requirements/test_import.py` |
| `R-300-084` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/router.py`, `ay_platform_core/src/ay_platform_core/c5_requirements/service.py` | — |
| `R-300-085` | v1 | draft | **not-yet** | — | — |
| `R-300-086` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/router.py`, `ay_platform_core/src/ay_platform_core/c5_requirements/service.py` | — |
| `R-300-090` | v1 | draft | **not-yet** | — | — |
| `R-300-091` | v1 | draft | **not-yet** | — | — |
| `R-300-100` | v1 | draft | **test-only** | — | `ay_platform_core/tests/unit/c6_validation/test_parsers.py` |
| `R-300-101` | v1 | draft | **not-yet** | — | — |
| `R-300-102` | v1 | draft | **not-yet** | — | — |
| `R-300-103` | v1 | draft | **not-yet** | — | — |
| `R-300-110` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c5_requirements/service.py` | — |
| `R-300-111` | v1 | draft | **not-yet** | — | — |
| `R-300-120` | v1 | draft | **not-yet** | — | — |
| `R-300-121` | v1 | draft | **not-yet** | — | — |

## R-400-* — [400-SPEC-MEMORY-RAG](./400-SPEC-MEMORY-RAG.md)

| ID | v | status | overall | implementing | validating |
|---|---|---|---|---|---|
| `R-400-001` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c7_memory/embedding/base.py`, `ay_platform_core/src/ay_platform_core/c7_memory/embedding/deterministic.py`, `ay_platform_core/src/ay_platform_core/c7_memory/embedding/ollama.py` | — |
| `R-400-002` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c7_memory/embedding/base.py`, `ay_platform_core/src/ay_platform_core/c7_memory/embedding/ollama.py` | — |
| `R-400-003` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c7_memory/embedding/deterministic.py` | — |
| `R-400-004` | v1 | draft | **not-yet** | — | — |
| `R-400-010` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c7_memory/db/repository.py`, `ay_platform_core/src/ay_platform_core/c7_memory/models.py` | — |
| `R-400-011` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c7_memory/db/repository.py`, `ay_platform_core/src/ay_platform_core/c7_memory/retrieval/similarity.py` | — |
| `R-400-012` | v1 | draft | **not-yet** | — | — |
| `R-400-013` | v1 | draft | **not-yet** | — | — |
| `R-400-020` | v2 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c7_memory/service.py`, `infra/c12_workflow/workflows/chunk_and_track.json`, `infra/k8s/base/c12_workflow/c12-workflow-configmap.yaml` | — |
| `R-400-021` | v2 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c7_memory/ingestion/parser.py` | `ay_platform_core/tests/integration/c7_memory/test_auto_kg_extraction.py`, `ay_platform_core/tests/integration/c7_memory/test_kg_extraction.py`, `ay_platform_core/tests/integration/c7_memory/test_upload_pipeline.py` |
| `R-400-022` | v2 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c7_memory/ingestion/chunker.py` | — |
| `R-400-023` | v1 | draft | **not-yet** | — | — |
| `R-400-024` | v1 | draft | **not-yet** | — | — |
| `R-400-030` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c7_memory/service.py` | — |
| `R-400-031` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c7_memory/service.py` | — |
| `R-400-032` | v1 | draft | **not-yet** | — | — |
| `R-400-040` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c7_memory/models.py`, `ay_platform_core/src/ay_platform_core/c7_memory/router.py`, `ay_platform_core/src/ay_platform_core/c7_memory/service.py` | `ay_platform_core/tests/integration/c7_memory/test_kg_hybrid_retrieve.py` |
| `R-400-041` | v1 | draft | **not-yet** | — | — |
| `R-400-042` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c7_memory/service.py` | — |
| `R-400-043` | v1 | draft | **not-yet** | — | — |
| `R-400-050` | v1 | draft | **not-yet** | — | — |
| `R-400-051` | v1 | draft | **not-yet** | — | — |
| `R-400-060` | v1 | draft | **not-yet** | — | — |
| `R-400-061` | v1 | draft | **not-yet** | — | — |
| `R-400-070` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c7_memory/router.py`, `ay_platform_core/src/ay_platform_core/c7_memory/service.py` | `ay_platform_core/tests/integration/c7_memory/test_blob_download.py` |
| `R-400-071` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c7_memory/service.py` | — |
| `R-400-100` | v1 | draft | **not-yet** | — | — |
| `R-400-101` | v1 | draft | **not-yet** | — | — |
| `R-400-110` | v1 | draft | **not-yet** | — | — |
| `R-400-120` | v1 | draft | **not-yet** | — | — |
| `R-400-200` | v2 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c7_memory/kg/repository.py` | `ay_platform_core/tests/integration/c7_memory/test_structural_extraction.py`, `ay_platform_core/tests/unit/c7_memory/test_code_extractor.py` |
| `R-400-201` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c7_memory/models.py` | `ay_platform_core/tests/integration/c7_memory/test_kg_summary.py`, `ay_platform_core/tests/integration/c7_memory/test_structural_extraction.py`, `ay_platform_core/tests/unit/c7_memory/test_kg_provenance.py` |
| `R-400-202` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c7_memory/db/repository.py` | `ay_platform_core/tests/eval/test_retrieval_quality.py`, `ay_platform_core/tests/integration/c7_memory/test_hybrid_lexical.py` |
| `R-400-203` | v1 | draft | **test-only** | — | `ay_platform_core/tests/integration/c7_memory/test_contextualisation.py` |
| `R-400-204` | v1 | draft | **not-yet** | — | — |
| `R-400-205` | v1 | draft | **not-yet** | — | — |
| `R-400-206` | v1 | draft | **not-yet** | — | — |
| `R-400-207` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c7_memory/artifacts.py`, `ay_platform_core/src/ay_platform_core/c7_memory/service.py`, `ay_platform_core/src/ay_platform_core/c7_memory/storage/minio_storage.py` | `ay_platform_core/tests/integration/c7_memory/test_artifact_rebuild.py`, `ay_platform_core/tests/unit/c7_memory/test_artifacts.py` |
| `R-400-208` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c7_memory/service.py`, `infra/c12_workflow/workflows/chunk_and_track.json`, `infra/k8s/base/c12_workflow/c12-workflow-configmap.yaml` | `ay_platform_core/tests/integration/c7_memory/test_processing_version.py`, `ay_platform_core/tests/unit/c7_memory/test_processing_version.py` |
| `R-400-209` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c7_memory/kg/repository.py` | `ay_platform_core/tests/integration/c7_memory/test_bitemporal.py` |
| `R-400-220` | v2 | draft | **not-yet** | — | — |
| `R-400-221` | v2 | draft | **not-yet** | — | — |
| `R-400-222` | v2 | draft | **not-yet** | — | — |
| `R-400-223` | v2 | draft | **not-yet** | — | — |
| `R-400-224` | v1 | draft | **not-yet** | — | — |
| `R-400-225` | v1 | draft | **not-yet** | — | — |

## R-500-* — [500-SPEC-UI-UX](./500-SPEC-UI-UX.md)

| ID | v | status | overall | implementing | validating |
|---|---|---|---|---|---|
| `R-500-001` | v1 | approved | **divergent** | — | — |
| `R-500-002` | v1 | approved | **divergent** | — | — |
| `R-500-003` | v1 | approved | **divergent** | — | — |
| `R-500-004` | v1 | approved | **divergent** | — | — |
| `R-500-005` | v1 | approved | **divergent** | — | — |
| `R-500-006` | v1 | approved | **divergent** | — | — |
| `R-500-007` | v1 | approved | **divergent** | — | — |
| `R-500-008` | v1 | approved | **divergent** | — | — |
| `R-500-009` | v1 | draft | **not-yet** | — | — |
| `R-500-010` | v2 | draft | **not-yet** | — | — |
| `R-500-011` | v1 | draft | **not-yet** | — | — |
| `R-500-012` | v1 | draft | **not-yet** | — | — |
| `R-500-013` | v1 | draft | **not-yet** | — | — |
| `R-500-014` | v1 | draft | **not-yet** | — | — |

## R-700-* — [700-SPEC-VERTICAL-COHERENCE](./700-SPEC-VERTICAL-COHERENCE.md)

| ID | v | status | overall | implementing | validating |
|---|---|---|---|---|---|
| `R-700-001` | v1 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/__init__.py`, `ay_platform_core/src/ay_platform_core/c6_validation/domains/code/plugin.py`, `ay_platform_core/src/ay_platform_core/c6_validation/models.py` (+1 more) | — |
| `R-700-002` | v1 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/__init__.py`, `ay_platform_core/src/ay_platform_core/c6_validation/plugin/registry.py` | — |
| `R-700-003` | v1 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/domains/code/plugin.py`, `ay_platform_core/src/ay_platform_core/c6_validation/plugin/base.py` | — |
| `R-700-010` | v1 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/router.py`, `ay_platform_core/src/ay_platform_core/c6_validation/service.py` | — |
| `R-700-011` | v1 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/service.py` | — |
| `R-700-012` | v1 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/db/repository.py`, `ay_platform_core/src/ay_platform_core/c6_validation/router.py`, `ay_platform_core/src/ay_platform_core/c6_validation/service.py` | — |
| `R-700-013` | v1 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/service.py`, `ay_platform_core/src/ay_platform_core/c6_validation/storage/minio_storage.py` | — |
| `R-700-014` | v1 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/domains/code/plugin.py`, `ay_platform_core/src/ay_platform_core/c6_validation/service.py` | — |
| `R-700-020` | v1 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/domains/code/checks.py` | — |
| `R-700-021` | v1 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/domains/code/checks.py` | — |
| `R-700-022` | v1 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/domains/code/checks.py` | — |
| `R-700-023` | v1 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/domains/code/checks.py` | — |
| `R-700-024` | v1 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/domains/code/checks.py` | — |
| `R-700-025` | v1 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/domains/code/checks.py` | — |
| `R-700-026` | v2 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/domains/code/checks.py` | — |
| `R-700-027` | v1 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/domains/code/checks.py` | — |
| `R-700-028` | v2 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/domains/code/checks.py` | — |
| `R-700-030` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/models.py` | — |
| `R-700-031` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/grading.py` | — |
| `R-700-032` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c6_validation/grading_judge.py`, `ay_platform_core/src/ay_platform_core/c6_validation/service.py` | `ay_platform_core/tests/unit/c6_validation/test_grading_judge.py` |
| `R-700-033` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c6_validation/db/repository.py`, `ay_platform_core/src/ay_platform_core/c6_validation/regression.py`, `ay_platform_core/src/ay_platform_core/c6_validation/service.py` | `ay_platform_core/tests/integration/c6_validation/test_regression_flow.py`, `ay_platform_core/tests/unit/c6_validation/test_regression.py` |
| `R-700-040` | v1 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/domains/code/parsers.py` | — |
| `R-700-041` | v1 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/domains/code/parsers.py` | — |
| `R-700-050` | v1 | approved | **implemented** | `ay_platform_core/src/ay_platform_core/c6_validation/config.py` | — |

## R-800-* — [800-SPEC-LLM-ABSTRACTION](./800-SPEC-LLM-ABSTRACTION.md)

| ID | v | status | overall | implementing | validating |
|---|---|---|---|---|---|
| `R-800-001` | v1 | draft | **implemented** | `infra/k8s/base/c8_gateway/litellm-deployment.yaml` | — |
| `R-800-002` | v1 | draft | **not-yet** | — | — |
| `R-800-003` | v1 | draft | **not-yet** | — | — |
| `R-800-004` | v1 | draft | **not-yet** | — | — |
| `R-800-010` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c8_llm/client.py`, `ay_platform_core/src/ay_platform_core/c8_llm/models.py` | — |
| `R-800-011` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c8_llm/client.py` | — |
| `R-800-012` | v1 | draft | **not-yet** | — | — |
| `R-800-013` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c8_llm/client.py`, `ay_platform_core/src/ay_platform_core/c8_llm/models.py` | — |
| `R-800-014` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c8_llm/client.py`, `ay_platform_core/src/ay_platform_core/c8_llm/models.py` | — |
| `R-800-015` | v1 | draft | **not-yet** | — | — |
| `R-800-020` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c8_llm/config.py` | — |
| `R-800-021` | v1 | draft | **not-yet** | — | — |
| `R-800-022` | v1 | draft | **not-yet** | — | — |
| `R-800-023` | v1 | draft | **not-yet** | — | — |
| `R-800-024` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c8_llm/config.py` | — |
| `R-800-030` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c8_llm/client.py`, `ay_platform_core/src/ay_platform_core/c8_llm/config.py` | `ay_platform_core/tests/unit/c8_llm/test_agent_route_resolver.py` |
| `R-800-031` | v1 | draft | **not-yet** | — | — |
| `R-800-032` | v1 | draft | **not-yet** | — | — |
| `R-800-033` | v1 | draft | **not-yet** | — | — |
| `R-800-040` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c8_llm/catalog.py` | — |
| `R-800-041` | v1 | draft | **not-yet** | — | — |
| `R-800-042` | v1 | draft | **not-yet** | — | — |
| `R-800-050` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c8_llm/catalog.py`, `ay_platform_core/src/ay_platform_core/c8_llm/validator.py` | — |
| `R-800-051` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c8_llm/catalog.py`, `ay_platform_core/src/ay_platform_core/c8_llm/validator.py` | — |
| `R-800-060` | v1 | draft | **not-yet** | — | — |
| `R-800-061` | v1 | draft | **not-yet** | — | — |
| `R-800-062` | v1 | draft | **not-yet** | — | — |
| `R-800-063` | v1 | draft | **not-yet** | — | — |
| `R-800-070` | v1 | draft | **tested** | `ay_platform_core/src/ay_platform_core/c8_llm/callbacks/cost_tracker.py`, `ay_platform_core/src/ay_platform_core/c8_llm/cost_sink_arango.py`, `ay_platform_core/src/ay_platform_core/c8_llm/main.py` (+4 more) | `ay_platform_core/tests/integration/c8_llm/test_cost_receiver_api.py`, `ay_platform_core/tests/unit/c8_llm/test_cost_receiver.py` |
| `R-800-071` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c8_llm/callbacks/cost_tracker.py`, `ay_platform_core/src/ay_platform_core/c8_llm/cost.py` | — |
| `R-800-072` | v1 | draft | **not-yet** | — | — |
| `R-800-073` | v1 | draft | **implemented** | `ay_platform_core/src/ay_platform_core/c8_llm/client.py`, `ay_platform_core/src/ay_platform_core/c8_llm/models.py` | — |
| `R-800-080` | v1 | draft | **not-yet** | — | — |
| `R-800-081` | v1 | draft | **not-yet** | — | — |
| `R-800-082` | v1 | draft | **not-yet** | — | — |
| `R-800-083` | v1 | draft | **not-yet** | — | — |
| `R-800-090` | v1 | draft | **not-yet** | — | — |
| `R-800-091` | v1 | draft | **not-yet** | — | — |
| `R-800-092` | v1 | draft | **not-yet** | — | — |
| `R-800-093` | v1 | draft | **not-yet** | — | — |
| `R-800-094` | v1 | draft | **not-yet** | — | — |
| `R-800-100` | v1 | draft | **not-yet** | — | — |
| `R-800-101` | v1 | draft | **not-yet** | — | — |
| `R-800-102` | v1 | draft | **not-yet** | — | — |
| `R-800-110` | v1 | draft | **not-yet** | — | — |
| `R-800-120` | v1 | draft | **not-yet** | — | — |
| `R-800-121` | v1 | draft | **not-yet** | — | — |
| `R-800-130` | v1 | draft | **not-yet** | — | — |
| `R-800-131` | v2 | draft | **not-yet** | — | — |
| `R-800-132` | v2 | draft | **not-yet** | — | — |
| `R-800-133` | v1 | draft | **not-yet** | — | — |
| `R-800-134` | v1 | draft | **not-yet** | — | — |

---

**End of 060-IMPLEMENTATION-STATUS.md.**
