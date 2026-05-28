# =============================================================================
# File: test_parser.py
# Version: 1
# Path: ay_platform_core/tests/unit/_observability/test_parser.py
# Description: Severity-extraction tests for the log-line parser. Covers
#              JSON, token (`level=…`), prefix (`ERROR …`), Python
#              tracebacks, alias normalisation, and unknown-token
#              fallback.
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core._observability.parser import (
    SEVERITY_RANK,
    is_at_least,
    normalise_severity,
    parse_severity,
)

pytestmark = pytest.mark.unit


class TestParseSeverity:
    @pytest.mark.parametrize(
        "line,expected",
        [
            ('{"level":"ERROR","msg":"boom"}', "ERROR"),
            ('{"severity":"WARN","msg":"x"}', "WARNING"),
            ('{"log_level":"FATAL"}', "CRITICAL"),
            ('{"msg":"plain"}', "INFO"),
        ],
        ids=["json-error", "json-warn", "json-fatal", "json-no-level"],
    )
    def test_json_payload(self, line: str, expected: str) -> None:
        assert parse_severity(line) == expected

    @pytest.mark.parametrize(
        "line,expected",
        [
            ("ts=12 level=ERROR msg=boom", "ERROR"),
            ('foo severity="warning" bar', "WARNING"),
            ("LEVEL: CRITICAL", "CRITICAL"),
            ("severity=err handled", "ERROR"),
        ],
        ids=["token-error", "token-warn", "token-critical", "token-err-alias"],
    )
    def test_token_form(self, line: str, expected: str) -> None:
        assert parse_severity(line) == expected

    @pytest.mark.parametrize(
        "line,expected",
        [
            ("ERROR something failed", "ERROR"),
            ("WARN: deprecated", "WARNING"),
            ("DEBUG noise", "DEBUG"),
            ("INFO ok", "INFO"),
        ],
        ids=["prefix-error", "prefix-warn", "prefix-debug", "prefix-info"],
    )
    def test_prefix_form(self, line: str, expected: str) -> None:
        assert parse_severity(line) == expected

    def test_python_traceback(self) -> None:
        assert parse_severity("Traceback (most recent call last):") == "ERROR"

    def test_unknown_falls_back_to_info(self) -> None:
        # No level, no token, no prefix, no traceback marker.
        assert parse_severity("a quiet line") == "INFO"

    def test_garbage_json_does_not_raise(self) -> None:
        # Looks like JSON but isn't valid — must fall back to other strategies
        # and ultimately to INFO without throwing.
        assert parse_severity("{not really json}") == "INFO"

    def test_unknown_level_token_normalised_to_info(self) -> None:
        # An unknown level keyword still becomes INFO via the normaliser.
        assert parse_severity("level=NOTICE-ish action") == "INFO"


class TestNormalise:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("warn", "WARNING"),
            ("WARN", "WARNING"),
            ("Fatal", "CRITICAL"),
            ("err", "ERROR"),
            ("ERROR", "ERROR"),
            ("UNKNOWN", "INFO"),
        ],
    )
    def test_aliases(self, raw: str, expected: str) -> None:
        assert normalise_severity(raw) == expected


class TestIsAtLeast:
    def test_strict_ranking(self) -> None:
        assert is_at_least("ERROR", "WARNING")
        assert is_at_least("CRITICAL", "ERROR")
        assert not is_at_least("INFO", "WARNING")
        assert not is_at_least("DEBUG", "ERROR")

    def test_equal_severity_passes(self) -> None:
        assert is_at_least("ERROR", "ERROR")
        assert is_at_least("INFO", "INFO")

    def test_unknown_minimum_treated_as_zero(self) -> None:
        # Conservative: unknown thresholds let everything through.
        assert is_at_least("INFO", "MYSTERY")

    def test_rank_table_complete(self) -> None:
        # Every documented severity has a rank — a guard against typos
        # if someone adds a level without updating the table.
        for sev in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            assert sev in SEVERITY_RANK
