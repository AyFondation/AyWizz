# =============================================================================
# File: test_config.py
# Version: 1
# Path: ay_platform_core/tests/unit/c5_requirements/test_config.py
# Description: Unit tests for RequirementsConfig — ensure defaults load and
#              env-var prefix overrides apply. Without these tests the
#              Pydantic-settings class is never exercised in the suite.
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c5_requirements.config import RequirementsConfig


@pytest.mark.unit
class TestRequirementsConfig:
    def test_defaults_load(self) -> None:
        cfg = RequirementsConfig()
        assert cfg.minio_bucket == "requirements"
        assert cfg.arango_db == "platform"
        assert cfg.idempotency_ttl_seconds == 86400
        assert cfg.reconcile_interval_seconds == 900
        assert cfg.platform_environment == "development"

    def test_env_prefix_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("C5_MINIO_BUCKET", "override-bucket")
        monkeypatch.setenv("C5_ARANGO_DB", "override-db")
        # platform_environment is cross-cutting — read without prefix via
        # validation_alias so a single env-file entry propagates everywhere.
        monkeypatch.setenv("PLATFORM_ENVIRONMENT", "staging")
        cfg = RequirementsConfig()
        assert cfg.minio_bucket == "override-bucket"
        assert cfg.arango_db == "override-db"
        assert cfg.platform_environment == "staging"

    def test_unknown_env_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # extra='ignore' on SettingsConfigDict — unknown keys are silently
        # dropped rather than raising, so operator typos don't crash C5
        # startup.
        monkeypatch.setenv("C5_TOTALLY_UNKNOWN_KEY", "noise")
        cfg = RequirementsConfig()
        assert cfg.minio_bucket == "requirements"

    def test_invalid_environment_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pydantic import ValidationError  # noqa: PLC0415 — local to the test

        monkeypatch.setenv("PLATFORM_ENVIRONMENT", "wonderland")
        with pytest.raises(ValidationError):
            RequirementsConfig()
