# src/main.py — v2
"""CLI entry point — analyze, stats commands.

D-020 v1 scope reduction: the `batch` subcommand was removed because the
`batch/` module is stripped. Batch ingestion now belongs to C12 (n8n
workflow) per R-100-081 v2; the CLI single-doc `analyze` remains for
dev/debug.

Usage:
    ayextractor analyze <file> [options]
    ayextractor stats <output_dir>

See spec §34; D-020 R-100-125 for the v1 scope.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from ayextractor.version import __version__

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """Main CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)

    if not hasattr(args, "func"):
        parser.print_help()
        return 1

    try:
        return asyncio.run(args.func(args))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 130
    except Exception as exc:
        logger.error("Fatal error: %s", exc, exc_info=args.verbose)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="ayextractor",
        description=f"ayExtractor v{__version__} — Multi-agent document analyzer",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging",
    )

    subparsers = parser.add_subparsers(dest="command")

    # --- analyze ---
    p_analyze = subparsers.add_parser(
        "analyze", help="Analyze a single document",
    )
    p_analyze.add_argument("file", type=Path, help="Path to document")
    p_analyze.add_argument(
        "-o", "--output", type=Path, default=Path("./output"),
        help="Output directory (default: ./output)",
    )
    p_analyze.add_argument(
        "-t", "--type", dest="doc_type", default="report",
        help="Document type: book, article, report, whitepaper (default: report)",
    )
    p_analyze.add_argument(
        "--language", default=None,
        help="Language hint (auto-detected if omitted)",
    )
    p_analyze.add_argument(
        "--resume-run", default=None,
        help="Run ID to resume from",
    )
    p_analyze.add_argument(
        "--resume-step", type=int, default=None,
        help="Step number to resume from",
    )
    p_analyze.set_defaults(func=_cmd_analyze)

    # `batch` subcommand removed by D-020 v1 strip; C12 (n8n) owns batch
    # orchestration now.

    # --- stats ---
    p_stats = subparsers.add_parser(
        "stats", help="Show processing statistics",
    )
    p_stats.add_argument(
        "output_dir", type=Path, help="Output directory to inspect",
    )
    p_stats.set_defaults(func=_cmd_stats)

    return parser


async def _cmd_analyze(args: argparse.Namespace) -> int:
    """Execute single-document analysis."""
    from ayextractor.api.facade import analyze
    from ayextractor.api.models import DocumentInput, Metadata

    file_path: Path = args.file
    if not file_path.exists():
        logger.error("File not found: %s", file_path)
        return 1

    fmt = _detect_format(file_path)
    if fmt is None:
        logger.error("Unsupported format: %s", file_path.suffix)
        return 1

    document = DocumentInput(
        content=file_path,
        format=fmt,
        filename=file_path.name,
    )
    metadata = Metadata(
        document_type=args.doc_type,
        output_path=args.output,
        language=args.language,
        resume_from_run=args.resume_run,
        resume_from_step=args.resume_step,
    )

    logger.info("Analyzing %s (%s)", file_path.name, fmt)
    result = await analyze(document, metadata)
    _print_result_summary(result)
    return 0


async def _cmd_stats(args: argparse.Namespace) -> int:
    """Display processing statistics for an output directory."""
    output_dir: Path = args.output_dir
    if not output_dir.is_dir():
        logger.error("Not a directory: %s", output_dir)
        return 1

    # Count documents and runs
    doc_dirs = [d for d in output_dir.iterdir() if d.is_dir()]
    total_runs = 0
    for doc_dir in doc_dirs:
        runs = [r for r in doc_dir.iterdir() if r.is_dir()]
        total_runs += len(runs)

    print(f"\nStatistics for {output_dir}:")
    print(f"  Documents:  {len(doc_dirs)}")
    print(f"  Total runs: {total_runs}")
    return 0


def _detect_format(path: Path) -> str | None:
    """Detect document format from file extension."""
    ext_map = {
        ".pdf": "pdf",
        ".epub": "epub",
        ".docx": "docx",
        ".md": "md",
        ".txt": "txt",
        ".png": "image",
        ".jpg": "image",
        ".jpeg": "image",
        ".webp": "image",
    }
    return ext_map.get(path.suffix.lower())


def _print_result_summary(result: object) -> None:
    """Print a human-readable summary of AnalysisResult."""
    print(f"\nAnalysis complete:")
    print(f"  Document ID:  {result.document_id}")
    print(f"  Run ID:       {result.run_id}")
    print(f"  Chunks:       {result.chunks_count}")
    print(f"  Output:       {result.run_dir}")
    if result.summary:
        preview = result.summary[:200]
        if len(result.summary) > 200:
            preview += "..."
        print(f"  Summary:      {preview}")


def _setup_logging(verbose: bool) -> None:
    """Configure logging for CLI usage."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


if __name__ == "__main__":
    sys.exit(main())
