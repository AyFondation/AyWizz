# src/storage/minio_layout.py — v1
"""MinIO key builders for the R-400-220 v2 artifact layout.

D-020 v2 fixes the C13 → MinIO layout as:

    {bucket}/{tenant_id}/{project_id}/{source_id}/runs/{run_id}/
      00_metadata/run_manifest.json
      00_metadata/input_fingerprint.json
      01_extraction/enriched_text.md
      01_extraction/structure.json
      01_extraction/references.json
      01_extraction/images/img_{sha8}.json    # deduplicated by content hash
      01_extraction/tables/tbl_{NNN}.json
      02_chunks/chunks.jsonl
      02_chunks/embeddings.jsonl
      02_chunks/chunk_index.json
      02_chunks/dense_summary.md              # only if quality_tier=high
      02_chunks/screener_log.jsonl            # only if quality_tier=high
      status.json

This module returns RELATIVE key strings (no bucket prefix — the
`MinioWriter` adds bucket + global prefix). All keys are
forward-slash separated regardless of platform.

Counterpart of `storage/layout.py` (Path-based, used by the legacy CLI
and run_manager). When deploying as C13, the orchestration uses these
string keys directly with the MinioWriter; the Path-based layout is
the standalone-CLI fallback.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunPrefix:
    """Scoping tuple for a single C13 run.

    Provides the `tenant_id/project_id/source_id/runs/{run_id}` prefix
    common to all artifact keys for one run. Build once at the start of
    `facade.analyze` and pass through the pipeline.
    """

    tenant_id: str
    project_id: str
    source_id: str
    run_id: str

    @property
    def prefix(self) -> str:
        return (
            f"{self.tenant_id}/{self.project_id}/{self.source_id}"
            f"/runs/{self.run_id}"
        )

    def key(self, *parts: str) -> str:
        """Join `prefix` with arbitrary path parts using forward slashes."""
        return "/".join((self.prefix, *parts))


# --- Top-level run keys ---------------------------------------------------


def run_manifest_key(run: RunPrefix) -> str:
    return run.key("00_metadata", "run_manifest.json")


def input_fingerprint_key(run: RunPrefix) -> str:
    return run.key("00_metadata", "input_fingerprint.json")


def run_stats_key(run: RunPrefix) -> str:
    """Per-phase timing + token/call usage (R-400-226 observability)."""
    return run.key("00_metadata", "run_stats.json")


def status_key(run: RunPrefix) -> str:
    return run.key("status.json")


# --- Phase 1 extraction keys ----------------------------------------------


def enriched_text_key(run: RunPrefix) -> str:
    return run.key("01_extraction", "enriched_text.md")


def structure_key(run: RunPrefix) -> str:
    return run.key("01_extraction", "structure.json")


def references_key(run: RunPrefix) -> str:
    return run.key("01_extraction", "references.json")


def image_analysis_key(run: RunPrefix, image_sha8: str) -> str:
    """Per-image analysis result, named by the first 8 hex of the image sha256
    (R-400-220 v2 §image dedup)."""
    return run.key("01_extraction", "images", f"img_{image_sha8}.json")


def table_key(run: RunPrefix, table_index: int) -> str:
    """Per-table structured data, named by sequential index."""
    return run.key("01_extraction", "tables", f"tbl_{table_index:03d}.json")


# --- Phase 2 chunks keys --------------------------------------------------


def chunks_jsonl_key(run: RunPrefix) -> str:
    return run.key("02_chunks", "chunks.jsonl")


def embeddings_jsonl_key(run: RunPrefix) -> str:
    """R-400-222 v2 §embedding — one {chunk_id, embedding} per line."""
    return run.key("02_chunks", "embeddings.jsonl")


def chunk_index_key(run: RunPrefix) -> str:
    return run.key("02_chunks", "chunk_index.json")


def dense_summary_key(run: RunPrefix) -> str:
    """Chain of Density output — only written when quality_tier=high."""
    return run.key("02_chunks", "dense_summary.md")


def screener_log_key(run: RunPrefix) -> str:
    """R-400-220 v2 — decontextualiser screener verdicts (audit trail).

    Only present when quality_tier=high. One {chunk_id, verdict, reason}
    per line.
    """
    return run.key("02_chunks", "screener_log.jsonl")
