# tests/unit/extraction/test_unit_image_pipeline.py — v1
"""Tests for the embedded-image enrichment pipeline (dedup + vision caption).

Proves the two behaviours the feature promises:
  - DEDUP: identical images (same bytes) are captioned ONCE, not per occurrence.
  - CAPTION: the vision model's description replaces the extractor placeholder,
    a per-image JSON artifact is written (keyed by sha8), and a failure on one
    image does not abort the rest.
"""

from __future__ import annotations

import json

from ayextractor.core.models import ExtractionResult, ImageAnalysis
from ayextractor.extraction.image_pipeline import (
    ImageBlob,
    caption_image_blobs,
    sha256_hex,
)
from ayextractor.llm.base_client import BaseLLMClient
from ayextractor.llm.models import LLMResponse
from ayextractor.pipeline.state import PipelineState
from ayextractor.storage.minio_layout import RunPrefix


class _VisionLLM(BaseLLMClient):
    """Scripted vision client; counts how many images it is asked to analyse."""

    def __init__(self, content: str, fail_on: bytes | None = None) -> None:
        self._content = content
        self._fail_on = fail_on
        self.calls = 0

    async def complete(self, messages, system=None, max_tokens=4096, temperature=0.2, response_format=None, cache_system=False) -> LLMResponse:  # noqa: E501
        raise NotImplementedError

    async def complete_with_vision(self, messages, images, system=None, max_tokens=4096, cache_system=False) -> LLMResponse:  # noqa: E501
        self.calls += 1
        if self._fail_on is not None and images and images[0].data == self._fail_on:
            raise RuntimeError("vision boom")
        return LLMResponse(
            content=self._content, input_tokens=10, output_tokens=10,
            model="mock-vision", provider="mock", latency_ms=1,
        )

    @property
    def supports_vision(self) -> bool:
        return True

    @property
    def provider_name(self) -> str:
        return "mock"


class _FakeWriter:
    def __init__(self) -> None:
        self.writes: dict[str, str] = {}

    async def write(self, key: str, data: str) -> None:
        self.writes[key] = data


_CAPTION = json.dumps(
    {"type": "diagram", "description": "A labelled flowchart.", "entities": ["Box A"]}
)


def _state_with_placeholders(image_ids: list[str]) -> PipelineState:
    images = [
        ImageAnalysis(id=i, type="photo", description="[pending analysis]", source_page=1)
        for i in image_ids
    ]
    extraction = ExtractionResult(raw_text="t", enriched_text="t", images=images)
    return PipelineState(run_id="r1", document_id="d1", extraction_result=extraction)


def _run_prefix() -> RunPrefix:
    return RunPrefix(tenant_id="t", project_id="p", source_id="s", run_id="r1")


# Real-sized image payloads (≥ the decorative-skip threshold of 3072 bytes) so
# they exercise the captioning path. A separate test covers the skip itself.
_IMG_A = b"A" * 4096
_IMG_B = b"B" * 4096


async def test_dedup_captions_identical_images_once():
    # Two identical images (same bytes) + one distinct → 2 UNIQUE captions.
    blobs = [
        ImageBlob("img_p1_01", _IMG_A, "image/png", 1),
        ImageBlob("img_p1_02", _IMG_A, "image/png", 1),  # duplicate of the first
        ImageBlob("img_p2_01", _IMG_B, "image/jpeg", 2),
    ]
    state = _state_with_placeholders(["img_p1_01", "img_p1_02", "img_p2_01"])
    vision = _VisionLLM(_CAPTION)
    writer = _FakeWriter()

    analyses = await caption_image_blobs(
        blobs=blobs,
        state=state,
        llm_factory=lambda name: vision,
        writer=writer,
        run_prefix=_run_prefix(),
    )

    # DEDUP: 3 occurrences, only 2 unique images → 2 vision calls (not 3).
    assert vision.calls == 2
    assert len(analyses) == 2
    assert state.total_llm_calls == 2
    # One per-image JSON artifact per UNIQUE sha8.
    assert len(writer.writes) == 2
    sha_a = sha256_hex(_IMG_A)
    key_a = next(k for k in writer.writes if sha_a[:8] in k)
    payload = json.loads(writer.writes[key_a])
    assert payload["sha256"] == sha_a
    assert payload["occurrences"] == 2  # the duplicate is recorded
    assert sorted(payload["image_ids"]) == ["img_p1_01", "img_p1_02"]


async def test_captions_fold_into_extraction_images():
    blobs = [
        ImageBlob("img_p1_01", _IMG_A, "image/png", 1),
        ImageBlob("img_p1_02", _IMG_A, "image/png", 1),
        ImageBlob("img_p2_01", _IMG_B, "image/jpeg", 2),
    ]
    state = _state_with_placeholders(["img_p1_01", "img_p1_02", "img_p2_01"])
    vision = _VisionLLM(_CAPTION)

    await caption_image_blobs(
        blobs=blobs, state=state, llm_factory=lambda name: vision,
    )

    # Every placeholder description is replaced by the generated caption, and the
    # two identical images share the same caption.
    descriptions = [img.description for img in state.extraction_result.images]
    assert descriptions == ["A labelled flowchart."] * 3
    assert all(img.type == "diagram" for img in state.extraction_result.images)


async def test_one_image_failure_does_not_abort():
    blobs = [
        ImageBlob("img_p1_01", _IMG_A, "image/png", 1),
        ImageBlob("img_p2_01", _IMG_B, "image/jpeg", 2),
    ]
    state = _state_with_placeholders(["img_p1_01", "img_p2_01"])
    vision = _VisionLLM(_CAPTION, fail_on=_IMG_A)

    analyses = await caption_image_blobs(
        blobs=blobs, state=state, llm_factory=lambda name: vision,
    )

    # The failing image is recorded; the other still captioned.
    assert len(analyses) == 1
    assert any("image_analyzer:" in e for e in state.errors)
    assert state.total_llm_calls == 1


async def test_uses_image_analyzer_agent_for_model_resolution():
    # The vision model is resolved via the "image_analyzer" agent name —
    # independent of the text agents (a project can use a different model).
    requested: list[str] = []
    vision = _VisionLLM(_CAPTION)

    def factory(name: str) -> BaseLLMClient:
        requested.append(name)
        return vision

    await caption_image_blobs(
        blobs=[ImageBlob("img_p1_01", _IMG_A, "image/png", 1)],
        state=_state_with_placeholders(["img_p1_01"]),
        llm_factory=factory,
    )
    assert requested == ["image_analyzer"]


async def test_empty_blobs_is_noop():
    state = _state_with_placeholders([])
    analyses = await caption_image_blobs(
        blobs=[], state=state, llm_factory=lambda name: _VisionLLM(_CAPTION),
    )
    assert analyses == {}
    assert state.total_llm_calls == 0


async def test_decorative_images_below_threshold_are_skipped():
    # Cost optimisation: icons/logos (tiny byte payloads) are NOT worth a vision
    # call. A small image is skipped (no LLM call, no artifact) while a real one
    # in the same batch is still captioned.
    tiny = b"icon"  # 4 bytes — well below the 3072-byte threshold
    blobs = [
        ImageBlob("img_logo", tiny, "image/png", 1),
        ImageBlob("img_fig", _IMG_A, "image/png", 2),
    ]
    state = _state_with_placeholders(["img_logo", "img_fig"])
    vision = _VisionLLM(_CAPTION)

    analyses = await caption_image_blobs(
        blobs=blobs, state=state, llm_factory=lambda name: vision,
    )

    # Only the real image is captioned ; the decorative one is skipped entirely.
    assert vision.calls == 1
    assert sha256_hex(_IMG_A) in analyses
    assert sha256_hex(tiny) not in analyses
    assert state.total_llm_calls == 1
