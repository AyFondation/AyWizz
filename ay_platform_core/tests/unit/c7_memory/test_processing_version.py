# =============================================================================
# File: test_processing_version.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_processing_version.py
# Description: Unit test for R-400-208 processing-version descriptor — the
#              deterministic string that makes a source's pipeline (chunk
#              window/overlap + embedding model) comparable so staleness
#              can be detected.
#
# @relation validates:R-400-208
# =============================================================================

from __future__ import annotations

from ay_platform_core.c7_memory.service import _format_processing_version


def test_version_is_deterministic_and_carries_chunk_and_model() -> None:
    v = _format_processing_version(512, 64, "all-mpnet-base-v2")
    assert v == "chunk=512/64;embed=all-mpnet-base-v2"
    # Same inputs -> same descriptor (the basis for staleness comparison).
    assert v == _format_processing_version(512, 64, "all-mpnet-base-v2")


def test_chunk_config_change_changes_version() -> None:
    a = _format_processing_version(512, 64, "m")
    b = _format_processing_version(256, 64, "m")
    assert a != b


def test_model_change_changes_version() -> None:
    a = _format_processing_version(512, 64, "model-a")
    b = _format_processing_version(512, 64, "model-b")
    assert a != b


def test_missing_model_id_is_explicit() -> None:
    assert _format_processing_version(512, 64, None) == "chunk=512/64;embed=unknown"
