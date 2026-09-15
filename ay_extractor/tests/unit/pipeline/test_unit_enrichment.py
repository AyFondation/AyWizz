# tests/unit/pipeline/test_unit_enrichment.py — v1
"""Tests for the Phase-2 text-enrichment orchestrator + config resolver.

Covers BOTH directions of the requirement:
  - ON  : standard runs the summarizer ; high additionally runs the
          decontextualizer + densifier (chunks decontextualized, summaries
          populated, global summary injected).
  - OFF : minimal runs no LLM agent ; explicit overrides flip individual
          options regardless of the tier preset.
"""

from __future__ import annotations

import json

from ayextractor.api.models import ConfigOverrides
from ayextractor.core.models import Chunk
from ayextractor.llm.base_client import BaseLLMClient
from ayextractor.llm.models import LLMResponse
from ayextractor.pipeline.enrichment import (
    ResolvedEnrichment,
    resolve_enrichment,
    run_text_enrichment,
)
from ayextractor.pipeline.state import PipelineState

# `asyncio_mode = "auto"` (pyproject) runs async tests without explicit marks.


# --- Scripted LLM doubles ---------------------------------------------------


class _ScriptedLLM(BaseLLMClient):
    """Returns a fixed JSON body for every completion (per-agent shape)."""

    def __init__(self, content: str) -> None:
        self._content = content

    async def complete(self, messages, system=None, max_tokens=4096, temperature=0.2, response_format=None, cache_system=False) -> LLMResponse:  # noqa: E501
        return LLMResponse(
            content=self._content, input_tokens=10, output_tokens=10,
            model="mock", provider="mock", latency_ms=1,
        )

    async def complete_with_vision(self, messages, images, system=None, max_tokens=4096, cache_system=False) -> LLMResponse:  # noqa: E501
        return LLMResponse(
            content=self._content, input_tokens=10, output_tokens=10,
            model="mock", provider="mock", latency_ms=1,
        )

    @property
    def supports_vision(self) -> bool:
        return True

    @property
    def provider_name(self) -> str:
        return "mock"


_SUMM = json.dumps({
    "summary": "Running summary of the document so far.",
    "confidence": 0.9,
})
_DECON = json.dumps({
    "decontextualized_content": "DECON: fully self-contained sentence.",
    "resolved_references": [],
    "confidence": 0.8,
})
_DENSE = json.dumps({
    "dense_summary": "Very dense summary.",
    "added_entities": [],
    "removed_details": [],
    "density_score": 0.7,
})


def _make_factory():
    clients = {
        "summarizer": _ScriptedLLM(_SUMM),
        "decontextualizer": _ScriptedLLM(_DECON),
        "densifier": _ScriptedLLM(_DENSE),
    }
    calls: dict[str, int] = {"summarizer": 0, "decontextualizer": 0, "densifier": 0}

    def factory(name: str) -> BaseLLMClient:
        calls[name] = calls.get(name, 0) + 1
        return clients[name]

    return factory, calls


def _make_state(n: int = 2) -> PipelineState:
    chunks = [
        Chunk(
            id=f"c{i}", position=i,
            content=f"Chunk {i}: it references the entity and the directive.",
            source_file="doc.md", char_count=50, word_count=9,
            token_count_est=12, fingerprint=f"fp{i}",
        )
        for i in range(n)
    ]
    return PipelineState(run_id="r1", document_id="d1", language="en", chunks=chunks)


# --- resolve_enrichment -----------------------------------------------------


def test_resolve_minimal_all_off():
    r = resolve_enrichment("minimal", None)
    assert (r.summarization, r.decontextualization, r.densification) == (False, False, False)
    assert r.image_vision is False
    assert r.any_text is False
    assert r.needs_llm is False


def test_resolve_standard_summary_only():
    r = resolve_enrichment("standard", None)
    assert (r.summarization, r.decontextualization, r.densification) == (True, False, False)
    # standard enables image vision (any non-minimal tier turns the options on).
    assert r.image_vision is True
    assert r.any_text is True


def test_resolve_high_all_on():
    r = resolve_enrichment("high", None)
    assert (r.summarization, r.decontextualization, r.densification) == (True, True, True)
    assert r.image_vision is True


def test_resolve_image_vision_override():
    # high preset, but the project disables image vision explicitly.
    r = resolve_enrichment("high", ConfigOverrides(image_vision_enabled=False))
    assert r.image_vision is False
    # …and a minimal project can opt INTO image vision alone.
    r2 = resolve_enrichment("minimal", ConfigOverrides(image_vision_enabled=True))
    assert r2.image_vision is True
    assert r2.needs_llm is True
    assert r2.any_text is False


def test_override_turns_option_on_against_preset():
    # minimal preset, but the project explicitly enables decontextualization.
    r = resolve_enrichment("minimal", ConfigOverrides(decontextualization_enabled=True))
    assert r.decontextualization is True
    assert r.summarization is False


def test_override_turns_option_off_against_preset():
    # high preset, but the project explicitly disables densification.
    r = resolve_enrichment("high", ConfigOverrides(densification_enabled=False))
    assert r.densification is False
    assert r.summarization is True  # still on from the high preset


def test_densification_implies_summarization():
    r = resolve_enrichment("minimal", ConfigOverrides(densification_enabled=True))
    assert r.densification is True
    assert r.summarization is True  # needed to produce the refine summary


# --- run_text_enrichment ----------------------------------------------------


async def test_high_runs_all_agents_and_enriches_chunks():
    state = _make_state(2)
    originals = [c.content for c in state.chunks]
    factory, calls = _make_factory()

    await run_text_enrichment(
        state=state,
        llm_factory=factory,
        document_title="Doc",
        resolved=resolve_enrichment("high", None),
    )

    # MAP→REDUCE document summary ; the region partial is shared by its chunks.
    assert state.refine_summary == "Running summary of the document so far."
    assert all(c.context_summary == "Running summary of the document so far." for c in state.chunks)
    # Densification → dense summary + global summary on every chunk.
    assert state.dense_summary == "Very dense summary."
    assert all(c.global_summary == "Very dense summary." for c in state.chunks)
    # Decontextualization replaced content + preserved the original.
    for c, original in zip(state.chunks, originals, strict=True):
        assert c.content == "DECON: fully self-contained sentence."
        assert c.original_content == original
        assert c.decontextualization is not None and c.decontextualization.applied is True
    # MAP batches 2 chunks into ONE summariser call (batch=5) ; REDUCE of a
    # single partial needs no extra call. Decontextualisation is per-chunk
    # (concurrent), densifier once.
    assert calls == {"summarizer": 1, "decontextualizer": 2, "densifier": 1}


async def test_standard_runs_only_summarizer():
    state = _make_state(2)
    originals = [c.content for c in state.chunks]
    factory, calls = _make_factory()

    await run_text_enrichment(
        state=state,
        llm_factory=factory,
        document_title="Doc",
        resolved=resolve_enrichment("standard", None),
    )

    assert state.refine_summary == "Running summary of the document so far."
    assert state.dense_summary == ""  # densifier did not run
    # C: the document summary surfaces even on `standard` (no densify) — every
    # chunk's `global_summary` = the map-reduce document summary (refine_summary).
    assert all(
        c.global_summary == "Running summary of the document so far." for c in state.chunks
    )
    for c, original in zip(state.chunks, originals, strict=True):
        assert c.content == original  # NOT decontextualized
        assert c.decontextualization is None
    # 2 chunks → 1 MAP batch → 1 summariser call ; no decon/densify on standard.
    assert calls == {"summarizer": 1, "decontextualizer": 0, "densifier": 0}


async def test_minimal_resolved_runs_no_agent():
    state = _make_state(2)
    originals = [c.content for c in state.chunks]
    factory, calls = _make_factory()

    # A minimal-resolved config has nothing to do — assert the orchestrator is
    # a no-op (the facade guards on `any_text`, but the orchestrator must be
    # safe to call regardless).
    await run_text_enrichment(
        state=state,
        llm_factory=factory,
        document_title="Doc",
        resolved=ResolvedEnrichment(
            summarization=False,
            decontextualization=False,
            densification=False,
            image_vision=False,
            densifier_iterations=5,
        ),
    )

    assert state.refine_summary == ""
    assert state.dense_summary == ""
    assert [c.content for c in state.chunks] == originals
    assert calls == {"summarizer": 0, "decontextualizer": 0, "densifier": 0}


async def test_one_batch_failure_does_not_abort_the_rest():
    # 6 chunks → 2 MAP batches (batch=5). The summarizer fails on the FIRST
    # call only ; the other batch still produces a partial → a document summary.
    state = _make_state(6)
    base_factory, _calls = _make_factory()
    flips = {"n": 0}

    class _Flaky(_ScriptedLLM):
        async def complete(self, *a, **k):
            flips["n"] += 1
            if flips["n"] == 1:
                raise RuntimeError("transient")
            return await super().complete(*a, **k)

    def factory2(name: str) -> BaseLLMClient:
        if name == "summarizer":
            return _Flaky(_SUMM)
        return base_factory(name)

    await run_text_enrichment(
        state=state,
        llm_factory=factory2,
        document_title="Doc",
        resolved=resolve_enrichment("standard", None),
    )

    # One batch failed (recorded) ; the surviving batch yields the summary.
    assert any("summarizer" in e for e in state.errors)
    assert state.refine_summary == "Running summary of the document so far."


# --- facade.analyze end-to-end (local mode, no MinIO) -----------------------

_MD = (
    "# Cyber Report\n\n"
    "The NIS2 directive applies across the EU. It mandates incident reporting.\n\n"
    "## Scope\n\n"
    "Essential entities must comply. They face audits and fines.\n"
)


async def test_facade_high_tier_enriches_end_to_end(tmp_path):
    from ayextractor.api.facade import analyze
    from ayextractor.api.models import DocumentInput, Metadata

    factory, calls = _make_factory()
    result = await analyze(
        DocumentInput(content=_MD, format="md", filename="report.md"),
        Metadata(quality_tier="high", output_path=tmp_path),
        llm_factory=factory,
    )

    assert result.chunks_count >= 1
    # high → densification ran → summary is the dense one.
    assert result.summary == "Very dense summary."
    assert calls["summarizer"] >= 1
    assert calls["decontextualizer"] >= 1
    assert calls["densifier"] == 1


async def test_facade_minimal_tier_runs_no_enrichment(tmp_path):
    from ayextractor.api.facade import analyze
    from ayextractor.api.models import DocumentInput, Metadata

    factory, calls = _make_factory()
    result = await analyze(
        DocumentInput(content=_MD, format="md", filename="report.md"),
        Metadata(quality_tier="minimal", output_path=tmp_path),
        llm_factory=factory,
    )

    assert result.chunks_count >= 1
    assert result.summary == ""  # no summary on minimal
    assert calls == {"summarizer": 0, "decontextualizer": 0, "densifier": 0}
