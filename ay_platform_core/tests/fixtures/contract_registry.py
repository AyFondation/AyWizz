# =============================================================================
# File: contract_registry.py
# Version: 10
# Path: ay_platform_core/tests/fixtures/contract_registry.py
# Description: Central registry of component-exposed contracts (Pydantic
#              schemas, event schemas, REST request/response types).
#              Consumed by tests/coherence/test_interface_consistency.py
#              to verify that every consumer of a contract matches the
#              producer's declared schema.
#
#              Each component SHALL declare its public contracts via
#              register_contract() in its own module. This module
#              aggregates all registrations at import time.
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExposedContract:
    """A single contract exposed by one component and consumed by others.

    Attributes:
        producer: Identifier of the producing component (e.g. "C2_auth").
        name: Stable human-readable contract name (e.g. "JWT", "ConversationCreatedEvent").
        schema: The Pydantic model class (or other schema type) that defines the
                contract's structure. This is the source of truth.
        consumers: Identifiers of components that consume this contract.
        transport: How the contract is transmitted ("rest", "nats", "python-import").
        description: Optional one-line description.
    """

    producer: str
    name: str
    schema: type[Any]
    consumers: tuple[str, ...]
    transport: str
    description: str = ""


_REGISTRY: list[ExposedContract] = []


def register_contract(contract: ExposedContract) -> None:
    """Register a contract. Called at component module import time.

    Raises:
        ValueError: if a contract with the same (producer, name) already exists.
    """
    for existing in _REGISTRY:
        if existing.producer == contract.producer and existing.name == contract.name:
            raise ValueError(
                f"Contract already registered: producer={contract.producer}, "
                f"name={contract.name}"
            )
    _REGISTRY.append(contract)


def get_registry() -> tuple[ExposedContract, ...]:
    """Return an immutable snapshot of the registry."""
    return tuple(_REGISTRY)


def clear_registry() -> None:
    """Clear the registry. Test-only utility."""
    _REGISTRY.clear()


def find_by_producer(producer: str) -> tuple[ExposedContract, ...]:
    """Return all contracts exposed by a given producer."""
    return tuple(c for c in _REGISTRY if c.producer == producer)


def find_by_consumer(consumer: str) -> tuple[ExposedContract, ...]:
    """Return all contracts consumed by a given component."""
    return tuple(c for c in _REGISTRY if consumer in c.consumers)


# ---------------------------------------------------------------------------
# C2 Auth Service contracts
# Registered here (test infrastructure) — production code must not import tests.
# ---------------------------------------------------------------------------

from ay_platform_core.c2_auth.models import (  # noqa: E402
    JWTClaims,
    LoginRequest,
    TokenResponse,
    UserPublic,
)

register_contract(
    ExposedContract(
        producer="C2_auth",
        name="JWTClaims",
        schema=JWTClaims,
        consumers=("C1_gateway", "C3_conversation", "C4_orchestrator"),
        transport="rest",
        description="Platform-internal JWT claim set. E-100-001.",
    )
)
register_contract(
    ExposedContract(
        producer="C2_auth",
        name="LoginRequest",
        schema=LoginRequest,
        consumers=("C1_gateway",),
        transport="rest",
        description="Credential payload for POST /auth/login and /auth/token.",
    )
)
register_contract(
    ExposedContract(
        producer="C2_auth",
        name="TokenResponse",
        schema=TokenResponse,
        consumers=("C1_gateway",),
        transport="rest",
        description="Successful auth response with access_token and expires_in.",
    )
)
register_contract(
    ExposedContract(
        producer="C2_auth",
        name="UserPublic",
        schema=UserPublic,
        consumers=("C1_gateway",),
        transport="rest",
        description="User data safe for external exposure (hash excluded). R-100-012.",
    )
)

# ---------------------------------------------------------------------------
# C3 Conversation Service contracts
# ---------------------------------------------------------------------------

from ay_platform_core.c3_conversation.models import (  # noqa: E402
    ConversationPublic,
    MessagePublic,
    PromptReference,
)

register_contract(
    ExposedContract(
        producer="C3_conversation",
        name="ConversationPublic",
        schema=ConversationPublic,
        consumers=("C1_gateway", "C4_orchestrator"),
        transport="rest",
        description="Public view of a conversation session (no internal metadata).",
    )
)
register_contract(
    ExposedContract(
        producer="C3_conversation",
        name="MessagePublic",
        schema=MessagePublic,
        consumers=("C1_gateway", "C4_orchestrator"),
        transport="rest",
        description="Public view of a conversation message.",
    )
)
register_contract(
    ExposedContract(
        producer="C3_conversation",
        name="PromptReference",
        schema=PromptReference,
        consumers=("C1_gateway", "ay_platform_ui"),
        transport="rest",
        description=(
            "Operator-attached prompt reference (E-200-008, R-200-180..184). "
            "File or excerpt pinned to a chat turn ; resolved server-side "
            "and inlined as a separate system block before the user message."
        ),
    )
)

# ---------------------------------------------------------------------------
# C5 Requirements Service contracts
# ---------------------------------------------------------------------------

from ay_platform_core.c5_requirements.models import (  # noqa: E402
    DocumentPublic,
    EntityPublic,
    HistoryEntry,
    RelationEdge,
)

register_contract(
    ExposedContract(
        producer="C5_requirements",
        name="EntityPublic",
        schema=EntityPublic,
        consumers=(
            "C1_gateway",
            "C3_conversation",
            "C4_orchestrator",
            "C6_validation",
            "C7_memory",
            "C9_mcp",
        ),
        transport="rest",
        description="Entity as exposed through the REST API. Content hash excluded.",
    )
)
register_contract(
    ExposedContract(
        producer="C5_requirements",
        name="DocumentPublic",
        schema=DocumentPublic,
        consumers=("C1_gateway", "C4_orchestrator", "C6_validation", "C9_mcp"),
        transport="rest",
        description="Document descriptor (metadata + optional body).",
    )
)
register_contract(
    ExposedContract(
        producer="C5_requirements",
        name="HistoryEntry",
        schema=HistoryEntry,
        consumers=("C6_validation",),
        transport="rest",
        description="One version pointer in the requirements history (R-300-032).",
    )
)
register_contract(
    ExposedContract(
        producer="C5_requirements",
        name="RelationEdge",
        schema=RelationEdge,
        consumers=("C6_validation", "C7_memory"),
        transport="rest",
        description="Edge in req_relations (derives-from, impacts, tailoring-of, supersedes).",
    )
)

# ---------------------------------------------------------------------------
# C8 LLM Gateway contracts
# ---------------------------------------------------------------------------

from ay_platform_core.c8_llm.models import (  # noqa: E402
    BudgetStatus,
    CallRecord,
    ChatCompletionRequest,
    ChatCompletionResponse,
    CostSummary,
)

register_contract(
    ExposedContract(
        producer="C8_llm",
        name="ChatCompletionRequest",
        schema=ChatCompletionRequest,
        consumers=(
            "C3_conversation",
            "C4_orchestrator",
            "C6_validation",
            "C7_memory",
        ),
        transport="rest",
        description="OpenAI-compatible request body for POST /v1/chat/completions.",
    )
)
register_contract(
    ExposedContract(
        producer="C8_llm",
        name="ChatCompletionResponse",
        schema=ChatCompletionResponse,
        consumers=(
            "C3_conversation",
            "C4_orchestrator",
            "C6_validation",
            "C7_memory",
        ),
        transport="rest",
        description="OpenAI-compatible non-streaming response from C8.",
    )
)
register_contract(
    ExposedContract(
        producer="C8_llm",
        name="CostSummary",
        schema=CostSummary,
        consumers=("C1_gateway", "C4_orchestrator"),
        transport="rest",
        description="Aggregated cost summary returned by /admin/v1/costs/summary (R-800-073).",
    )
)
register_contract(
    ExposedContract(
        producer="C8_llm",
        name="BudgetStatus",
        schema=BudgetStatus,
        consumers=("C1_gateway", "C4_orchestrator"),
        transport="rest",
        description="Budget window snapshot returned by /admin/v1/budgets (R-800-063).",
    )
)
register_contract(
    ExposedContract(
        producer="C8_llm",
        name="CallRecord",
        schema=CallRecord,
        consumers=("C4_orchestrator",),
        transport="rest",
        description="One row of llm_calls (E-800-002), consumed by cost dashboards.",
    )
)

# ---------------------------------------------------------------------------
# C4 Orchestrator contracts
# ---------------------------------------------------------------------------

from ay_platform_core.c4_orchestrator.models import (  # noqa: E402
    AgentCompletion,
    DomainDescriptor,
    RunPublic,
    RunSteer,
    TraceEvent,
)

register_contract(
    ExposedContract(
        producer="C4_orchestrator",
        name="RunPublic",
        schema=RunPublic,
        consumers=("C1_gateway", "C3_conversation", "ay_platform_ui"),
        transport="rest",
        description=(
            "Run state exposed through the REST API (E-200-001 projection). "
            "v2 (Tranche B) includes a 200-event sliding window of TraceEvents."
        ),
    )
)
register_contract(
    ExposedContract(
        producer="C4_orchestrator",
        name="TraceEvent",
        schema=TraceEvent,
        consumers=("C1_gateway", "ay_platform_ui"),
        transport="rest",
        description=(
            "One entry of a run's append-only trace ledger (E-200-006). "
            "Surfaced via `RunPublic.trace` (sliding window) and the "
            "paginated `GET /runs/{id}/trace?before=<ts>` endpoint."
        ),
    )
)
register_contract(
    ExposedContract(
        producer="C4_orchestrator",
        name="RunSteer",
        schema=RunSteer,
        consumers=("C1_gateway", "ay_platform_ui"),
        transport="rest",
        description=(
            "POST /runs/{id}/steer body (E-200-007). A single operator "
            "hint queued FIFO, consumed at the next phase / sub-agent "
            "boundary per R-200-203."
        ),
    )
)
register_contract(
    ExposedContract(
        producer="C4_orchestrator",
        name="AgentCompletion",
        schema=AgentCompletion,
        consumers=("C4_orchestrator",),
        transport="python-import",
        description=(
            "Return envelope of an agent invocation (E-200-002). "
            "Consumed by the dispatcher/state machine."
        ),
    )
)
register_contract(
    ExposedContract(
        producer="C4_orchestrator",
        name="DomainDescriptor",
        schema=DomainDescriptor,
        consumers=("C4_orchestrator",),
        transport="python-import",
        description=(
            "Domain plug-in registration schema (E-200-003). Loaded from YAML "
            "at C4 startup."
        ),
    )
)

# C4 — Project artifacts surface (R-200-131..133). Separate from
# RunPublic — runs of the orchestrator state machine and artifact
# runs of the file-tree surface are different bookkeeping concepts.
from ay_platform_core.c4_orchestrator.artifacts_models import (  # noqa: E402
    ArtifactCommit,
    ArtifactCommitList,
    ArtifactNode,
    ArtifactRunList,
    ArtifactRunPublic,
    ArtifactTree,
)

register_contract(
    ExposedContract(
        producer="C4_orchestrator",
        name="ArtifactRunPublic",
        schema=ArtifactRunPublic,
        consumers=("C1_gateway", "ay_platform_ui"),
        transport="rest",
        description=(
            "One row from `GET /api/v1/projects/{pid}/artifacts/runs` "
            "(R-200-131). Run-level metadata only — file-level "
            "details land in ArtifactNode."
        ),
    )
)

# C4 — Source-files surface (§5.18 Tranche B). Recursive tree + metadata
# contracts surfaced by the new source_router endpoints (R-200-170..174).
from ay_platform_core.c4_orchestrator.source_router import (  # noqa: E402
    SourceFileMeta,
    SourceTreeNode,
)

register_contract(
    ExposedContract(
        producer="C4_orchestrator",
        name="SourceTreeNode",
        schema=SourceTreeNode,
        consumers=("C1_gateway", "ay_platform_ui"),
        transport="rest",
        description=(
            "Recursive source-files tree node (R-200-170). Distinct "
            "from ArtifactNode (flat per-run shape) — this contract "
            "carries `children?` for the hierarchical projection."
        ),
    )
)
register_contract(
    ExposedContract(
        producer="C4_orchestrator",
        name="SourceFileMeta",
        schema=SourceFileMeta,
        consumers=("C1_gateway", "ay_platform_ui"),
        transport="rest",
        description=(
            "Per-file metadata response for `GET /source/file/{path}/meta` "
            "(R-200-173). Best-effort Gitea commit fields + MinIO size + "
            "mime type."
        ),
    )
)
register_contract(
    ExposedContract(
        producer="C4_orchestrator",
        name="ArtifactRunList",
        schema=ArtifactRunList,
        consumers=("C1_gateway", "ay_platform_ui"),
        transport="rest",
        description="Wrapper for the artifact-runs listing response.",
    )
)

# C4 — Sub-agent context bundle contracts (P2.1.a, R-200-033). The
# orchestrator builds these envelopes ; the sub-agent runtime
# (`ay_platform_core._sub_agent`) consumes them inside ephemeral K8s
# pods. Registered so the parallel-definitions coherence check
# recognises the legitimate id-field overlap with RunPublic /
# AgentCompletion (same domain, different concern).
from ay_platform_core._sub_agent.models import (  # noqa: E402
    SubAgentRunReport,
    TaskEnvelope,
)

register_contract(
    ExposedContract(
        producer="C4_orchestrator",
        name="TaskEnvelope",
        schema=TaskEnvelope,
        consumers=("C4_orchestrator",),  # sub-agent runtime is part of C4
        transport="python-import",
        description=(
            "Sub-agent context bundle manifest (R-200-033). Written to "
            "MinIO at `c4-dispatch/<run_id>/<sub_agent_id>/manifest.json` "
            "by the K8sDispatcher ; read by the in-pod runtime."
        ),
    )
)
register_contract(
    ExposedContract(
        producer="C4_orchestrator",
        name="SubAgentRunReport",
        schema=SubAgentRunReport,
        consumers=("C4_orchestrator",),
        transport="python-import",
        description=(
            "Completion report written by the sub-agent runtime to "
            "`<bundle_prefix>output/completion.json`. The orchestrator "
            "reads it back after the pod terminates."
        ),
    )
)
register_contract(
    ExposedContract(
        producer="C4_orchestrator",
        name="ArtifactNode",
        schema=ArtifactNode,
        consumers=("C1_gateway", "ay_platform_ui"),
        transport="rest",
        description=(
            "One node of the per-run flat tree returned by "
            "`GET .../artifacts/runs/{rid}/tree` (R-200-131)."
        ),
    )
)
register_contract(
    ExposedContract(
        producer="C4_orchestrator",
        name="ArtifactTree",
        schema=ArtifactTree,
        consumers=("C1_gateway", "ay_platform_ui"),
        transport="rest",
        description="Wrapper for the artifact-tree response.",
    )
)
register_contract(
    ExposedContract(
        producer="C4_orchestrator",
        name="ArtifactCommit",
        schema=ArtifactCommit,
        consumers=("C1_gateway", "ay_platform_ui"),
        transport="rest",
        description=(
            "One commit returned by "
            "`GET /api/v1/projects/{pid}/git/commits` (R-200-147)."
        ),
    )
)
register_contract(
    ExposedContract(
        producer="C4_orchestrator",
        name="ArtifactCommitList",
        schema=ArtifactCommitList,
        consumers=("C1_gateway", "ay_platform_ui"),
        transport="rest",
        description="Wrapper for the artifact-commits response.",
    )
)

# C4 — Chat-direct DocGen document API (D-015 / R-200-153..156).
from ay_platform_core.c4_orchestrator.documents_router import (  # noqa: E402
    DocumentList,
    DocumentRef,
    DocumentUpdate,
    DocumentWrite,
)

register_contract(
    ExposedContract(
        producer="C4_orchestrator",
        name="DocumentWrite",
        schema=DocumentWrite,
        consumers=("C1_gateway", "ay_platform_ui", "C3_conversation"),
        transport="rest",
        description=(
            "Body of POST /api/v1/projects/{pid}/documents — create or "
            "overwrite a document (D-015 / R-200-153)."
        ),
    )
)
register_contract(
    ExposedContract(
        producer="C4_orchestrator",
        name="DocumentUpdate",
        schema=DocumentUpdate,
        consumers=("C1_gateway", "ay_platform_ui", "C3_conversation"),
        transport="rest",
        description=(
            "Body of PUT /api/v1/projects/{pid}/documents/{path} "
            "(D-015 / R-200-153)."
        ),
    )
)
register_contract(
    ExposedContract(
        producer="C4_orchestrator",
        name="DocumentRef",
        schema=DocumentRef,
        consumers=("C1_gateway", "ay_platform_ui", "C3_conversation"),
        transport="rest",
        description=(
            "One document listing row + the create/update response "
            "(D-015 / R-200-153)."
        ),
    )
)
register_contract(
    ExposedContract(
        producer="C4_orchestrator",
        name="DocumentList",
        schema=DocumentList,
        consumers=("C1_gateway", "ay_platform_ui", "C3_conversation"),
        transport="rest",
        description="Wrapper for the documents listing (D-015 / R-200-153).",
    )
)

# ---------------------------------------------------------------------------
# C7 Memory Service contracts
# ---------------------------------------------------------------------------

from ay_platform_core.c7_memory.models import (  # noqa: E402
    ChunkPublic,
    RetrievalRequest,
    RetrievalResponse,
    SourcePublic,
)

register_contract(
    ExposedContract(
        producer="C7_memory",
        name="RetrievalRequest",
        schema=RetrievalRequest,
        consumers=("C3_conversation", "C4_orchestrator", "C9_mcp"),
        transport="rest",
        description="Federated retrieval request body (R-400-040).",
    )
)
register_contract(
    ExposedContract(
        producer="C7_memory",
        name="RetrievalResponse",
        schema=RetrievalResponse,
        consumers=("C3_conversation", "C4_orchestrator", "C9_mcp"),
        transport="rest",
        description="Federated retrieval response with merged/weighted hits.",
    )
)
register_contract(
    ExposedContract(
        producer="C7_memory",
        name="SourcePublic",
        schema=SourcePublic,
        consumers=("C1_gateway", "C12_workflow"),
        transport="rest",
        description="External source descriptor (E-400-003 projection).",
    )
)
register_contract(
    ExposedContract(
        producer="C7_memory",
        name="ChunkPublic",
        schema=ChunkPublic,
        consumers=("C4_orchestrator",),
        transport="rest",
        description="A single embedded chunk returned by entity-embed operations.",
    )
)
# NOTE: the KG-extraction response models (KGExtractionResult,
# StructuralKGResult) are deliberately NOT registered as contracts — they are
# terminal REST responses not imported as a Python type by any other
# component (consistent with KGExtractionResult). Registering them would also
# false-trip the parallel-definition check against the KGArtifact replay
# model, which shares field NAMES (source_id/entities/relations) but neither
# the types nor the purpose.

# ---------------------------------------------------------------------------
# C6 Validation Pipeline Registry contracts
# ---------------------------------------------------------------------------

from ay_platform_core.c6_validation.models import (  # noqa: E402
    Finding,
    PluginDescriptor,
    RunTriggerRequest,
    RunTriggerResponse,
    ValidationRun,
)

register_contract(
    ExposedContract(
        producer="C6_validation",
        name="RunTriggerRequest",
        schema=RunTriggerRequest,
        consumers=("C4_orchestrator", "C9_mcp"),
        transport="rest",
        description="Payload for POST /validation/runs (R-700-010).",
    )
)
register_contract(
    ExposedContract(
        producer="C6_validation",
        name="RunTriggerResponse",
        schema=RunTriggerResponse,
        consumers=("C4_orchestrator", "C9_mcp"),
        transport="rest",
        description="202 response carrying the new run_id.",
    )
)
register_contract(
    ExposedContract(
        producer="C6_validation",
        name="ValidationRun",
        schema=ValidationRun,
        consumers=("C1_gateway", "C3_conversation", "C4_orchestrator", "C9_mcp"),
        transport="rest",
        description="Run row with status + summary counts (E-700-002).",
    )
)
register_contract(
    ExposedContract(
        producer="C6_validation",
        name="Finding",
        schema=Finding,
        consumers=("C1_gateway", "C3_conversation", "C4_orchestrator", "C9_mcp"),
        transport="rest",
        description="Single validation finding (E-700-001).",
    )
)
register_contract(
    ExposedContract(
        producer="C6_validation",
        name="PluginDescriptor",
        schema=PluginDescriptor,
        consumers=("C9_mcp",),
        transport="rest",
        description="Plugin metadata exposed via /validation/plugins.",
    )
)

# ---------------------------------------------------------------------------
# C9 MCP Server contracts
# ---------------------------------------------------------------------------

from ay_platform_core.c9_mcp.models import (  # noqa: E402
    JSONRPCRequest,
    JSONRPCResponse,
    ToolSpec,
)

register_contract(
    ExposedContract(
        producer="C9_mcp",
        name="JSONRPCRequest",
        schema=JSONRPCRequest,
        consumers=("external_mcp_client",),
        transport="rest",
        description="JSON-RPC 2.0 request envelope consumed by POST /api/v1/mcp.",
    )
)
register_contract(
    ExposedContract(
        producer="C9_mcp",
        name="JSONRPCResponse",
        schema=JSONRPCResponse,
        consumers=("external_mcp_client",),
        transport="rest",
        description="JSON-RPC 2.0 response envelope returned by POST /api/v1/mcp.",
    )
)
register_contract(
    ExposedContract(
        producer="C9_mcp",
        name="ToolSpec",
        schema=ToolSpec,
        consumers=("external_mcp_client",),
        transport="rest",
        description="MCP tool declaration surfaced via tools/list and GET /api/v1/mcp/tools.",
    )
)
