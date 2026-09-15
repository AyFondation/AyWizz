# src/storage/run_manager.py — v1
"""Run lifecycle management: create, carry steps, finalize, update latest symlink.

See spec §5.1-5.3 for document_id format and run layout.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ayextractor.storage import layout
from ayextractor.storage.base_output_writer import BaseOutputWriter
from ayextractor.storage.models import RunManifest, StepManifest


def generate_document_id(timestamp: datetime | None = None) -> str:
    """Generate a document_id: yyyymmdd_hhmmss_{uuid4_short}.

    See spec §5.1.
    """
    ts = timestamp or datetime.now(timezone.utc)
    short_uuid = uuid.uuid4().hex[:8]
    return f"{ts.strftime('%Y%m%d_%H%M%S')}_{short_uuid}"


def generate_run_id(timestamp: datetime | None = None) -> str:
    """Generate a run_id: yyyymmdd_hhmm_{uuid4_short}."""
    ts = timestamp or datetime.now(timezone.utc)
    short_uuid = uuid.uuid4().hex[:5]
    return f"{ts.strftime('%Y%m%d_%H%M')}_{short_uuid}"


async def create_run(
    writer: BaseOutputWriter,
    output_path: Path,
    document_id: str,
    run_id: str,
    pipeline_version: str,
    config_overrides: dict | None = None,
    llm_assignments: dict[str, str] | None = None,
) -> tuple[Path, RunManifest]:
    """Create a new run directory with manifest.

    Args:
        writer: Output writer backend.
        output_path: Base output directory.
        document_id: Document identifier.
        run_id: Run identifier.
        pipeline_version: Current pipeline version.
        config_overrides: Applied configuration overrides.
        llm_assignments: Resolved LLM assignments per component.

    Returns:
        Tuple of (run_path, manifest).
    """
    run_path = layout.run_dir(output_path, document_id, run_id)
    layout.ensure_run_directories(run_path)

    # Ensure source/ exists
    source = layout.source_dir(output_path, document_id)
    source.mkdir(parents=True, exist_ok=True)

    manifest = RunManifest(
        run_id=run_id,
        document_id=document_id,
        pipeline_version=pipeline_version,
        created_at=datetime.now(timezone.utc),
        status="running",
        config_overrides_applied=config_overrides or {},
        llm_assignments=llm_assignments or {},
        prompt_hashes={},
        steps={},
    )

    # Write initial manifest
    manifest_json = manifest.model_dump_json(indent=2)
    await writer.write(str(layout.run_manifest_path(run_path)), manifest_json)

    return run_path, manifest


async def finalize_run(
    writer: BaseOutputWriter,
    output_path: Path,
    document_id: str,
    run_path: Path,
    manifest: RunManifest,
    status: str = "completed",
) -> None:
    """Finalize a run: update manifest, update latest symlink.

    Args:
        writer: Output writer backend.
        output_path: Base output directory.
        document_id: Document identifier.
        run_path: Path to this run.
        manifest: Run manifest to update.
        status: Final status (completed, failed, partial).
    """
    manifest.completed_at = datetime.now(timezone.utc)
    manifest.status = status  # type: ignore[assignment]

    # Write updated manifest
    manifest_json = manifest.model_dump_json(indent=2)
    await writer.write(str(layout.run_manifest_path(run_path)), manifest_json)

    # Update latest symlink
    latest = layout.latest_link(output_path, document_id)
    target = run_path.relative_to(latest.parent)
    await writer.create_symlink(str(target), str(latest))


async def record_step(
    manifest: RunManifest,
    step_name: str,
    origin: str = "fresh",
    carried_from: str | None = None,
) -> None:
    """Record a step in the manifest.

    Args:
        manifest: Run manifest to update (in-place).
        step_name: Step identifier (e.g. "extraction", "chunking").
        origin: "fresh" or "carried_from".
        carried_from: Source run_id if carried.
    """
    manifest.steps[step_name] = StepManifest(
        origin=origin,  # type: ignore[arg-type]
        carried_from=carried_from,
        started_at=datetime.now(timezone.utc),
    )


async def complete_step(
    manifest: RunManifest,
    step_name: str,
    output_hash: str | None = None,
) -> None:
    """Mark a step as completed in the manifest."""
    if step_name in manifest.steps:
        manifest.steps[step_name].completed_at = datetime.now(timezone.utc)
        manifest.steps[step_name].output_hash = output_hash
