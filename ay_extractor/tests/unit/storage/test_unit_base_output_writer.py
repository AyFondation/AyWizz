# tests/unit/storage/test_base_output_writer.py — v1
"""Tests for storage/base_output_writer.py — BaseOutputWriter ABC."""

from __future__ import annotations

import pytest

from ayextractor.storage.base_output_writer import BaseOutputWriter


class TestBaseOutputWriter:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            BaseOutputWriter()  # type: ignore[abstract]

    def test_has_required_methods(self):
        for method in ["write", "read", "exists", "copy", "create_symlink", "list_dir"]:
            assert hasattr(BaseOutputWriter, method)
