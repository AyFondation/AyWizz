# src/storage/reader.py — v1
"""Read run outputs for resume or consultation.

Provides helpers to load manifests, chunks, and analysis results
from previously completed runs.
"""

from __future__ import annotations

import json
from pathlib import Path

from ayextractor.core.models import Chunk
from ayextractor.storage import layout
from ayextractor.storage.models import RunManifest


def load_manifest(run_path: Path) -> RunManifest:
    """Load a RunManifest from a run directory."""
    path = layout.run_manifest_path(run_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return RunManifest(**data)


def load_chunks(run_path: Path) -> list[Chunk]:
    """Load all chunks from a run's 02_chunks/ directory."""
    chunks_path = layout.chunks_dir(run_path)
    chunks: list[Chunk] = []

    index_path = layout.chunks_index_path(run_path)
    if index_path.exists():
        index_data = json.loads(index_path.read_text(encoding="utf-8"))
        for entry in index_data.get("chunks", []):
            chunk_file = chunks_path / f"{entry['id']}.json"
            if chunk_file.exists():
                data = json.loads(chunk_file.read_text(encoding="utf-8"))
                chunks.append(Chunk(**data))
    else:
        # Fallback: load all chunk_*.json files by name
        for f in sorted(chunks_path.glob("chunk_*.json")):
            if f.name.endswith("_original.txt"):
                continue
            data = json.loads(f.read_text(encoding="utf-8"))
            chunks.append(Chunk(**data))

    return chunks


def load_dense_summary(run_path: Path) -> str | None:
    """Load dense summary if available."""
    path = layout.dense_summary_path(run_path)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def load_enriched_text(run_path: Path) -> str | None:
    """Load enriched text from extraction step."""
    path = layout.enriched_text_path(run_path)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def find_latest_run(output_path: Path, document_id: str) -> Path | None:
    """Find the latest run directory for a document."""
    latest = layout.latest_link(output_path, document_id)
    if latest.exists():
        return latest.resolve()

    # Fallback: find most recent run by name
    runs = layout.runs_dir(output_path, document_id)
    if not runs.is_dir():
        return None

    run_dirs = sorted(runs.iterdir(), reverse=True)
    return run_dirs[0] if run_dirs else None
