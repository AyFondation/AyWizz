# =============================================================================
# File: test_contextualizer.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_contextualizer.py
# Description: Unit tests for cumulative chunk contextualisation (R-400-203).
#              The C8 LLM client is faked (we mock the dependency, not the
#              function under test — CLAUDE.md §10.2) :
#                - one context per chunk ; document carried as the system
#                  prefix ; agent_name forwarded ;
#                - empty document → all-blank, no LLM call ;
#                - best-effort : a per-chunk failure yields "" for that chunk
#                  (never raises) ;
#                - multi-modal (list-of-parts) content is flattened.
# =============================================================================

from __future__ import annotations

import types
from typing import Any

import pytest

from ay_platform_core.c7_memory.contextualizer import contextualise_chunks

pytestmark = pytest.mark.unit


def _resp(content: Any) -> Any:
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))]
    )


class _FakeLLM:
    def __init__(self, *, reply: str = "Context.", raise_on: str | None = None,
                 content_override: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._reply = reply
        self._raise_on = raise_on
        self._content_override = content_override

    async def chat_completion(self, request: Any, **kwargs: Any) -> Any:
        self.calls.append({"request": request, **kwargs})
        user = request.messages[-1].content
        if self._raise_on is not None and self._raise_on in user:
            raise RuntimeError("simulated provider error")
        if self._content_override is not None:
            return _resp(self._content_override)
        return _resp(self._reply)


async def _run(llm: Any, document: str, chunks: list[str]) -> list[str]:
    return await contextualise_chunks(
        llm_client=llm,
        document=document,
        chunk_texts=chunks,
        agent_name="c7-contextualizer",
        tenant_id="t1",
        project_id="p1",
        source_id="s1",
    )


class TestContextualiseChunks:
    async def test_one_context_per_chunk(self) -> None:
        llm = _FakeLLM(reply="This chunk is about the gateway.")
        out = await _run(llm, "Full document text about the gateway.", ["a", "b", "c"])
        assert out == ["This chunk is about the gateway."] * 3
        assert len(llm.calls) == 3

    async def test_document_is_the_system_prefix_and_agent_forwarded(self) -> None:
        llm = _FakeLLM()
        await _run(llm, "DOC-MARKER body", ["chunk-one"])
        call = llm.calls[0]
        assert call["agent_name"] == "c7-contextualizer"
        system = call["request"].messages[0].content
        user = call["request"].messages[-1].content
        assert "DOC-MARKER body" in system  # cache-friendly stable prefix
        assert "chunk-one" in user

    async def test_empty_document_returns_blanks_without_calling_llm(self) -> None:
        llm = _FakeLLM()
        out = await _run(llm, "   ", ["a", "b"])
        assert out == ["", ""]
        assert llm.calls == []

    async def test_failure_yields_empty_for_that_chunk_only(self) -> None:
        llm = _FakeLLM(reply="ctx", raise_on="BADCHUNK")
        out = await _run(llm, "doc", ["ok-1", "BADCHUNK", "ok-2"])
        assert out == ["ctx", "", "ctx"]  # the failing chunk degrades, not the run

    async def test_multimodal_content_is_flattened(self) -> None:
        parts = [{"type": "text", "text": "Part-A "}, {"type": "text", "text": "Part-B"}]
        llm = _FakeLLM(content_override=parts)
        out = await _run(llm, "doc", ["x"])
        assert out == ["Part-A Part-B"]
