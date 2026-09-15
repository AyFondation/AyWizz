# src/storage/layout.py — v1
"""Output directory structure definition.

Defines path conventions for document output, runs, and step directories.
See spec §5.2 and §5.3 for full layout documentation.
"""

from __future__ import annotations

from pathlib import Path


# Top-level directories under {output_path}/{document_id}/
SOURCE_DIR = "source"
RUNS_DIR = "runs"
LATEST_LINK = "latest"

# Run-level directories under {run_id}/
METADATA_DIR = "00_metadata"
EXTRACTION_DIR = "01_extraction"
CHUNKS_DIR = "02_chunks"
CONCEPTS_DIR = "03_concepts"
SYNTHESIS_DIR = "04_synthesis"

# Sub-directories
TABLES_DIR = "tables"
IMAGES_DIR = "images"
TRIPLETS_RAW_DIR = "triplets_raw"


def document_root(output_path: Path, document_id: str) -> Path:
    """Return root directory for a document."""
    return output_path / document_id


def source_dir(output_path: Path, document_id: str) -> Path:
    """Return source/ directory for a document."""
    return document_root(output_path, document_id) / SOURCE_DIR


def runs_dir(output_path: Path, document_id: str) -> Path:
    """Return runs/ directory for a document."""
    return document_root(output_path, document_id) / RUNS_DIR


def run_dir(output_path: Path, document_id: str, run_id: str) -> Path:
    """Return a specific run directory."""
    return runs_dir(output_path, document_id) / run_id


def latest_link(output_path: Path, document_id: str) -> Path:
    """Return path to the 'latest' symlink."""
    return document_root(output_path, document_id) / LATEST_LINK


# --- Run-level paths ---

def metadata_dir(run_path: Path) -> Path:
    return run_path / METADATA_DIR


def extraction_dir(run_path: Path) -> Path:
    return run_path / EXTRACTION_DIR


def chunks_dir(run_path: Path) -> Path:
    return run_path / CHUNKS_DIR


def concepts_dir(run_path: Path) -> Path:
    return run_path / CONCEPTS_DIR


def synthesis_dir(run_path: Path) -> Path:
    return run_path / SYNTHESIS_DIR


# --- Specific file paths ---

def run_manifest_path(run_path: Path) -> Path:
    return run_path / "run_manifest.json"


def fingerprint_path(run_path: Path) -> Path:
    return metadata_dir(run_path) / "fingerprint.json"


def language_path(run_path: Path) -> Path:
    return metadata_dir(run_path) / "language.txt"


def structure_path(run_path: Path) -> Path:
    return metadata_dir(run_path) / "document_structure.json"


def raw_text_path(run_path: Path) -> Path:
    return extraction_dir(run_path) / "raw_text.txt"


def enriched_text_path(run_path: Path) -> Path:
    return extraction_dir(run_path) / "enriched_text.txt"


def references_path(run_path: Path) -> Path:
    return extraction_dir(run_path) / "references.json"


def chunks_index_path(run_path: Path) -> Path:
    return chunks_dir(run_path) / "chunks_index.json"


def chunk_path(run_path: Path, chunk_id: str) -> Path:
    return chunks_dir(run_path) / f"{chunk_id}.json"


def chunk_original_path(run_path: Path, chunk_id: str) -> Path:
    return chunks_dir(run_path) / f"{chunk_id}_original.txt"


def refine_summary_path(run_path: Path) -> Path:
    return chunks_dir(run_path) / "refine_summary.txt"


def dense_summary_path(run_path: Path) -> Path:
    return chunks_dir(run_path) / "dense_summary.txt"


def graph_json_path(run_path: Path) -> Path:
    return synthesis_dir(run_path) / "graph.json"


def communities_path(run_path: Path) -> Path:
    return synthesis_dir(run_path) / "communities.json"


def execution_stats_path(run_path: Path) -> Path:
    return synthesis_dir(run_path) / "execution_stats.json"


def calls_log_path(run_path: Path) -> Path:
    return synthesis_dir(run_path) / "calls_log.jsonl"


def ensure_run_directories(run_path: Path) -> None:
    """Create all standard directories for a new run."""
    for dir_fn in [metadata_dir, extraction_dir, chunks_dir, concepts_dir, synthesis_dir]:
        dir_fn(run_path).mkdir(parents=True, exist_ok=True)

    # Sub-directories
    (extraction_dir(run_path) / TABLES_DIR).mkdir(exist_ok=True)
    (extraction_dir(run_path) / IMAGES_DIR).mkdir(exist_ok=True)
    (concepts_dir(run_path) / TRIPLETS_RAW_DIR).mkdir(exist_ok=True)
