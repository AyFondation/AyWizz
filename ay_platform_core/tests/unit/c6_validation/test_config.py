# =============================================================================
# File: test_config.py
# Version: 1
# Path: ay_platform_core/tests/unit/c6_validation/test_config.py
# Description: Unit tests for ValidationConfig — defaults, env-var overrides,
#              rejection of extraneous keys. Without these the
#              pydantic-settings class is uncovered by the broader suite.
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c6_validation.config import ValidationConfig


@pytest.mark.unit
class TestValidationConfig:
    def test_defaults_load(self) -> None:
        cfg = ValidationConfig()
        assert cfg.arango_db == "platform"
        assert cfg.minio_bucket == "validation"
        assert cfg.default_check_enabled is True
        assert cfg.max_findings_per_run == 5_000

    def test_env_prefix_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("C6_ARANGO_DB", "override-db")
        monkeypatch.setenv("C6_MINIO_BUCKET", "override-bucket")
        monkeypatch.setenv("C6_MAX_FINDINGS_PER_RUN", "42")
        cfg = ValidationConfig()
        assert cfg.arango_db == "override-db"
        assert cfg.minio_bucket == "override-bucket"
        assert cfg.max_findings_per_run == 42

    def test_unknown_env_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("C6_TOTALLY_UNKNOWN_KEY", "noise")
        cfg = ValidationConfig()
        assert cfg.arango_db == "platform"

    def test_invalid_max_findings_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pydantic import ValidationError  # noqa: PLC0415 — local to the test

        monkeypatch.setenv("C6_MAX_FINDINGS_PER_RUN", "5")
        with pytest.raises(ValidationError):
            ValidationConfig()
