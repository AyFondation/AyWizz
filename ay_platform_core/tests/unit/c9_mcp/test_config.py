# =============================================================================
# File: test_config.py
# Version: 1
# Path: ay_platform_core/tests/unit/c9_mcp/test_config.py
# Description: Unit tests for MCPConfig — defaults, env overrides, lower-bound
#              validation on `max_tool_args_bytes`.
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c9_mcp.config import MCPConfig


@pytest.mark.unit
class TestMCPConfig:
    def test_defaults_load(self) -> None:
        cfg = MCPConfig()
        assert cfg.server_name == "ay-platform-core"
        assert cfg.server_version == "1.0.0"
        assert cfg.protocol_version == "2025-03-26"
        assert cfg.max_tool_args_bytes == 256_000

    def test_env_prefix_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("C9_SERVER_NAME", "my-fork")
        monkeypatch.setenv("C9_SERVER_VERSION", "2.1.0")
        monkeypatch.setenv("C9_MAX_TOOL_ARGS_BYTES", "50000")
        cfg = MCPConfig()
        assert cfg.server_name == "my-fork"
        assert cfg.server_version == "2.1.0"
        assert cfg.max_tool_args_bytes == 50_000

    def test_unknown_env_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("C9_TOTALLY_UNKNOWN", "noise")
        cfg = MCPConfig()
        assert cfg.server_name == "ay-platform-core"

    def test_max_args_lower_bound_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pydantic import ValidationError  # noqa: PLC0415

        monkeypatch.setenv("C9_MAX_TOOL_ARGS_BYTES", "1")
        with pytest.raises(ValidationError):
            MCPConfig()
