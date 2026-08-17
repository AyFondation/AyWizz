# =============================================================================
# File: test_prompt_cache.py
# Version: 3
# Path: ay_platform_core/tests/unit/c8_llm/test_prompt_cache.py
#
# @relation validates:R-800-147
# Description: Unit tests for the PROVIDER-AWARE prompt-cache breakpoint
#              injection (`_apply_static_prompt_cache`). Applied AFTER upstream
#              resolution, it reads the rewritten `<wire_format>/<upstream>`
#              model and emits the Anthropic `cache_control` marker ONLY for
#              wire formats that require it ; automatic-caching providers
#              (OpenAI, Gemini) and unresolved aliases get no marker — sending
#              an Anthropic-shaped block to OpenAI would error.
# =============================================================================

from __future__ import annotations

from typing import Any

import pytest

from ay_platform_core.c8_llm.client import (
    _apply_adaptive_thinking,
    _apply_static_prompt_cache,
)


@pytest.mark.unit
def test_adaptive_thinking_sets_marker() -> None:
    """R-800-147: verbose reasoning requests adaptive extended thinking."""
    body: dict[str, Any] = {"model": "anthropic/claude", "messages": []}
    _apply_adaptive_thinking(body)
    assert body["thinking"] == {"type": "adaptive"}


@pytest.mark.unit
def test_adaptive_thinking_does_not_clobber_explicit() -> None:
    body: dict[str, Any] = {"thinking": {"type": "enabled"}}
    _apply_adaptive_thinking(body)
    assert body["thinking"] == {"type": "enabled"}


_EPHEMERAL = {"type": "ephemeral"}


@pytest.mark.unit
def test_anthropic_string_system_becomes_cached_text_block() -> None:
    body: dict[str, Any] = {
        "model": "anthropic/claude-haiku-4-5",
        "messages": [{"role": "system", "content": "Stable instructions"}],
    }
    _apply_static_prompt_cache(body)
    assert body["messages"][0]["content"] == [
        {"type": "text", "text": "Stable instructions", "cache_control": _EPHEMERAL}
    ]


@pytest.mark.unit
def test_anthropic_block_list_system_caches_last_block() -> None:
    body: dict[str, Any] = {
        "model": "anthropic/claude-sonnet-4-6",
        "messages": [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "A"},
                    {"type": "text", "text": "B"},
                ],
            }
        ],
    }
    _apply_static_prompt_cache(body)
    blocks = body["messages"][0]["content"]
    assert "cache_control" not in blocks[0]  # only the LAST block is the breakpoint
    assert blocks[1]["cache_control"] == _EPHEMERAL


@pytest.mark.unit
def test_anthropic_last_system_message_is_chosen() -> None:
    body: dict[str, Any] = {
        "model": "anthropic/claude-haiku-4-5",
        "messages": [
            {"role": "system", "content": "first"},
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "second"},
        ],
    }
    _apply_static_prompt_cache(body)
    assert body["messages"][0]["content"] == "first"
    assert body["messages"][2]["content"][0]["cache_control"] == _EPHEMERAL


@pytest.mark.unit
def test_openai_provider_gets_no_marker() -> None:
    """The bug this guards against: an Anthropic `cache_control` block sent to a
    wire format with automatic server-side caching (OpenAI) is REJECTED."""
    body: dict[str, Any] = {
        "model": "openai/gpt-4o",
        "messages": [{"role": "system", "content": "Stable instructions"}],
    }
    _apply_static_prompt_cache(body)
    assert body["messages"][0]["content"] == "Stable instructions"  # untouched


@pytest.mark.unit
def test_unresolved_alias_without_slash_is_noop() -> None:
    """Mock / unresolved alias (no `<wire_format>/` prefix) → provider unknown,
    never guess a marker."""
    body: dict[str, Any] = {
        "model": "mock-model",
        "messages": [{"role": "system", "content": "Stable instructions"}],
    }
    _apply_static_prompt_cache(body)
    assert body["messages"][0]["content"] == "Stable instructions"  # untouched


@pytest.mark.unit
def test_no_system_message_is_noop() -> None:
    body: dict[str, Any] = {
        "model": "anthropic/claude-haiku-4-5",
        "messages": [{"role": "user", "content": "hi"}],
    }
    _apply_static_prompt_cache(body)
    assert body["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.unit
def test_missing_model_is_noop() -> None:
    body: dict[str, Any] = {"messages": [{"role": "system", "content": "x"}]}
    _apply_static_prompt_cache(body)
    assert body == {"messages": [{"role": "system", "content": "x"}]}
