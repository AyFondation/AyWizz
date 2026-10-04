# =============================================================================
# File: main.py
# Version: 8
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/main.py
# Description: FastAPI app factory for C5 Requirements Service.
#
#              v8: mounts baselines and rendering (310-SPEC §4.11) and
#              binds the six lookups of BaselineService — each a question
#              another surface already answers, so none is re-derived.
#              v7: mounts negotiated piloting (310-SPEC §4.9) and binds
#              its feature lookup to the impact graph — the inputs behind
#              Q-310-008's deterministic effort classification.
#              v6: mounts change absorption (310-SPEC §4.8). This module is
#              the composition root where the three injected lookups of
#              AbsorptionService are bound — coverage conclusion, object
#              version, and a requirement's split — because it is the only
#              place that knows all three stores are present.
#              v5: mounts the intake surface (310-SPEC §4.4 / §4.5).
#              v4: mounts the traceability graph (310-SPEC §4.4 / §4.7) and
#              injects its current-version lookup from the corpus index.
#              v3: mounts the process surface — cycles and workflows
#              (310-SPEC §4.2 / §4.3, D-024) — on the same MinIO bucket and
#              ArangoDB database.
#              v2: mounts the object-grain surface (310-SPEC §4.1, D-023).
#              Neither adds a component or a pod: one image, N containers
#              (R-100-114 v2) is unchanged.
#
# @relation implements:R-100-114
# =============================================================================

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI
from minio import Minio

from ay_platform_core.c5_requirements.absorption.repository import (
    AbsorptionRepository,
)
from ay_platform_core.c5_requirements.absorption.router import (
    router as absorption_router,
)
from ay_platform_core.c5_requirements.absorption.service import AbsorptionService
from ay_platform_core.c5_requirements.absorption.storage import AbsorptionStorage
from ay_platform_core.c5_requirements.baseline.repository import BaselineRepository
from ay_platform_core.c5_requirements.baseline.router import (
    router as baseline_router,
)
from ay_platform_core.c5_requirements.baseline.service import (
    BaselineService,
    NoPublishedCycleError,
)
from ay_platform_core.c5_requirements.baseline.storage import BaselineStorage
from ay_platform_core.c5_requirements.config import RequirementsConfig
from ay_platform_core.c5_requirements.coverage.repository import CoverageRepository
from ay_platform_core.c5_requirements.coverage.router import router as coverage_router
from ay_platform_core.c5_requirements.coverage.service import CoverageService
from ay_platform_core.c5_requirements.db.repository import RequirementsRepository
from ay_platform_core.c5_requirements.events.null_publisher import NullPublisher
from ay_platform_core.c5_requirements.execution.estimation import UnitFeatures
from ay_platform_core.c5_requirements.execution.repository import (
    ExecutionRepository,
)
from ay_platform_core.c5_requirements.execution.router import (
    router as execution_router,
)
from ay_platform_core.c5_requirements.execution.service import ExecutionService
from ay_platform_core.c5_requirements.execution.storage import ExecutionStorage
from ay_platform_core.c5_requirements.intake.models import SplitProposal
from ay_platform_core.c5_requirements.intake.router import router as intake_router
from ay_platform_core.c5_requirements.intake.service import IntakeService
from ay_platform_core.c5_requirements.intake.storage import IntakeStorage
from ay_platform_core.c5_requirements.objects.locks import LockManager
from ay_platform_core.c5_requirements.objects.models import (
    DocObjectPublic,
    ReviewState,
)
from ay_platform_core.c5_requirements.objects.repository import ObjectRepository
from ay_platform_core.c5_requirements.objects.router import router as objects_router
from ay_platform_core.c5_requirements.objects.service import ObjectService
from ay_platform_core.c5_requirements.objects.storage import ObjectStorage
from ay_platform_core.c5_requirements.process.repository import ProcessRepository
from ay_platform_core.c5_requirements.process.router import router as process_router
from ay_platform_core.c5_requirements.process.service import ProcessService
from ay_platform_core.c5_requirements.process.storage import ProcessStorage
from ay_platform_core.c5_requirements.router import router
from ay_platform_core.c5_requirements.service import RequirementsService
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage
from ay_platform_core.observability import (
    TraceContextMiddleware,
    configure_logging,
)
from ay_platform_core.observability.auth_guard import AuthGuardMiddleware
from ay_platform_core.observability.config import LoggingSettings


@dataclass(frozen=True, slots=True)
class _Traceability:
    """The 310-SPEC services and the index repositories they own.

    Extracted from `create_app` when that function passed ruff's statement
    ceiling (PLR0915) at increment 6. The gain is concrete and not merely
    lint-driven: five sub-surfaces with six injected lookups between them
    had made the composition root unreadable, and the lookups are what a
    reader most needs to follow. Flagged per CLAUDE.md §1.3.
    """

    coverage: CoverageService
    intake: IntakeService
    absorption: AbsorptionService
    execution: ExecutionService
    baseline: BaselineService
    baseline_storage: BaselineStorage
    repositories: tuple[Any, ...]


def _wire_traceability(
    db: Any,
    storage: RequirementsStorage,
    repo: RequirementsRepository,
    object_repo: ObjectRepository,
    object_service: ObjectService,
    process_service: ProcessService,
) -> _Traceability:
    """Compose the 310-SPEC surfaces onto the shared stores.

    No new pod and no new component (DV-03): distinct collections and path
    families on the SAME ArangoDB database and MinIO bucket.

    The injected lookups live here because this is the only place that
    knows coverage, intake, the object corpus and the impact graph are all
    present. Binding them inside each service would make every one of them
    untestable without a database.
    """

    # Coverage pins the version of a requirement ENTITY, which the existing
    # corpus index owns — hence a lookup rather than a direct read.
    async def current_version(project_id: str, target_id: str) -> int | None:
        row = await repo.get_entity(project_id, target_id)
        return int(row["version"]) if row and "version" in row else None

    # A drop is a file plus its extraction: MinIO only, no index.
    intake_service = IntakeService(IntakeStorage(storage))

    # R-310-177 v2: the speculative marking needs a coverage target's REVIEW
    # STATE, read from the object index rather than from MinIO — one indexed
    # lookup per target, and the marking is recomputed on every call so it
    # cannot drift from the graph.
    async def review_state_of(
        project_id: str, object_id: str
    ) -> ReviewState | None:
        row = await object_repo.get(project_id, object_id)
        if row is None or "review_state" not in row:
            return None
        return ReviewState(row["review_state"])

    coverage_repo = CoverageRepository(db)
    coverage_service = CoverageService(
        coverage_repo, process_service, current_version, review_state_of
    )

    async def is_covered(
        project_id: str, drop_id: str, requirement_id: str
    ) -> bool:
        # A split requirement is covered through its fragments, never
        # through its own allocations (R-310-096).
        fragments = await intake_service.fragments_of(
            project_id, drop_id, requirement_id
        )
        report = await coverage_service.requirement_coverage(
            project_id,
            requirement_id,
            fragments=[fragment.fragment_id for fragment in fragments] or None,
        )
        return report.is_covered

    async def split_of(
        project_id: str, drop_id: str, requirement_id: str
    ) -> SplitProposal | None:
        return await intake_service.split_of(project_id, drop_id, requirement_id)

    absorption_repo = AbsorptionRepository(db)
    absorption_service = AbsorptionService(
        AbsorptionStorage(storage),
        absorption_repo,
        coverage_repo,
        current_version,
        is_covered,
        split_of,
    )

    async def features_of(
        project_id: str, units: tuple[str, ...]
    ) -> list[UnitFeatures]:
        """Observe the features behind a deterministic effort estimate.

        `architecture_decision_requested` is left False, and that is
        correct rather than incomplete: a plan is proposed to decide
        whether to work on a batch AT ALL, which happens before any node of
        it is qualified (R-310-148 qualifies the nodes of an already-opened
        ticket). The flag matters on a re-plan, by which time the tickets
        exist.
        """
        found: list[UnitFeatures] = []
        for unit in units:
            impact = await absorption_service.impact_of(project_id, unit)
            found.append(
                UnitFeatures(
                    unit_id=unit,
                    impact_nodes=impact.node_count,
                    layers_crossed=max(
                        (node.depth for node in impact.nodes), default=0
                    ),
                    containers=len({node.container for node in impact.nodes}),
                )
            )
        return found

    execution_repo = ExecutionRepository(db)
    execution_service = ExecutionService(
        ExecutionStorage(storage), execution_repo, features_of
    )

    # Baselines (310-SPEC §4.11). Six lookups, every one of them a question
    # another surface already answers — so none is re-derived here.
    async def open_tickets(project_id: str) -> list[str]:
        rows = await absorption_repo.list_changes(project_id, only_open=True)
        return [str(row["_key"]) for row in rows]

    async def suspect_links(project_id: str) -> list[dict[str, object]]:
        return list(await coverage_service.suspect_links(project_id))

    async def rated_gaps(project_id: str) -> list[tuple[str, str]]:
        # A gap only blocks a baseline when the requirement is RATED
        # (R-310-201). An unrated gap is a gap the project chose to accept,
        # and refusing a baseline for it would make the gate unusable on any
        # real corpus.
        gaps: list[tuple[str, str]] = []
        for row in await coverage_repo.decisions(project_id):
            criticality = row.get("criticality")
            if not criticality or row.get("kind") != "allocation":
                continue
            requirement_id = str(row["requirement_id"])
            report = await coverage_service.requirement_coverage(
                project_id, requirement_id
            )
            if not report.is_covered:
                gaps.append((requirement_id, str(criticality)))
        return gaps

    async def resolve_cycle_containers(
        tenant_id: str, project_id: str, cycle_id: str
    ) -> tuple[int, list[str]]:
        cycle = await process_service.resolve_cycle(tenant_id, project_id, cycle_id)
        if cycle is None:
            raise NoPublishedCycleError(
                f"project {project_id!r} runs on no published cycle {cycle_id!r}; "
                "a baseline photographs a corpus structured by a cycle, so "
                "there is nothing to walk (R-310-200)"
            )
        ordered = sorted(cycle.containers, key=lambda spec: spec.ordinal)
        return cycle.version, [spec.slug for spec in ordered]

    async def container_objects(
        project_id: str, container: str
    ) -> list[DocObjectPublic]:
        return await object_service.list_container(project_id, container)

    async def container_links(
        project_id: str, container: str
    ) -> list[dict[str, object]]:
        rows = await coverage_repo.coverage_links(project_id, container=container)
        return [dict(row) for row in rows]

    baseline_storage = BaselineStorage(storage)
    baseline_repo = BaselineRepository(db)
    baseline_service = BaselineService(
        baseline_storage,
        baseline_repo,
        open_tickets,
        suspect_links,
        rated_gaps,
        resolve_cycle_containers,
        container_objects,
        container_links,
    )

    return _Traceability(
        coverage=coverage_service,
        intake=intake_service,
        absorption=absorption_service,
        execution=execution_service,
        baseline=baseline_service,
        baseline_storage=baseline_storage,
        repositories=(
            coverage_repo,
            absorption_repo,
            execution_repo,
            baseline_repo,
        ),
    )


def create_app(config: RequirementsConfig | None = None) -> FastAPI:
    cfg = config or RequirementsConfig()
    log_cfg = LoggingSettings()
    configure_logging(component="c5_requirements", settings=log_cfg)
    arango_client = ArangoClient(hosts=cfg.arango_url)
    db = arango_client.db(
        cfg.arango_db, username=cfg.arango_username, password=cfg.arango_password
    )
    repo = RequirementsRepository(db)

    minio_client = Minio(
        cfg.minio_endpoint,
        access_key=cfg.minio_access_key,
        secret_key=cfg.minio_secret_key,
        secure=cfg.minio_secure,
    )
    storage = RequirementsStorage(minio_client, cfg.minio_bucket)
    service = RequirementsService(repo, storage, NullPublisher())

    # Object-grain document model (310-SPEC §4.1). Shares the same MinIO
    # bucket and ArangoDB database — distinct path families and collections,
    # not a distinct component (D-023, no new pod).
    object_repo = ObjectRepository(db)
    object_locks = LockManager(db, lease_seconds=cfg.object_lock_lease_seconds)
    object_service = ObjectService(
        ObjectStorage(storage),
        object_repo,
        object_locks,
        draft_iteration_cap=cfg.draft_iteration_cap,
    )

    # The engineering process expressed as data (D-024). Same stores again;
    # distinct collections and path families, not a distinct component.
    process_repo = ProcessRepository(db)
    process_service = ProcessService(ProcessStorage(storage), process_repo)

    traceability = _wire_traceability(
        db, storage, repo, object_repo, object_service, process_service
    )
    coverage_service = traceability.coverage
    intake_service = traceability.intake
    absorption_service = traceability.absorption
    execution_service = traceability.execution
    baseline_service = traceability.baseline

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        repo._ensure_collections_sync()
        storage._ensure_bucket_sync()
        object_repo._ensure_collections_sync()
        object_locks._ensure_collections_sync()
        process_repo._ensure_collections_sync()
        for repository in traceability.repositories:
            repository._ensure_collections_sync()
        yield

    app = FastAPI(title="C5 Requirements Service", lifespan=lifespan)
    app.add_middleware(AuthGuardMiddleware, component="c5_requirements")
    app.add_middleware(TraceContextMiddleware, sample_rate=log_cfg.trace_sample_rate)
    app.include_router(router)
    app.include_router(objects_router)
    app.include_router(process_router)
    app.include_router(coverage_router)
    app.include_router(intake_router)
    app.include_router(absorption_router)
    app.include_router(execution_router)
    app.include_router(baseline_router)
    app.state.requirements_service = service
    app.state.object_service = object_service
    app.state.process_service = process_service
    app.state.coverage_service = coverage_service
    app.state.intake_service = intake_service
    app.state.absorption_service = absorption_service
    app.state.execution_service = execution_service
    app.state.baseline_service = baseline_service
    app.state.baseline_storage = traceability.baseline_storage

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "component": "c5_requirements"}

    return app


app = create_app()
