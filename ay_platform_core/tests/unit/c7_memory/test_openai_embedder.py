# =============================================================================
# File: test_openai_embedder.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_openai_embedder.py
# Description: Unit tests for OpenAIEmbedder (D-011) against a mocked OpenAI
#              `/embeddings` endpoint: request shape (model + input), response
#              parsing with index ordering, declared-dimension validation
#              (R-400-226), and HTTP/shape error handling. No network.
# =============================================================================

from __future__ import annotations

import json

import httpx
import pytest

from ay_platform_core.c7_memory.embedding.openai import OpenAIEmbedder

pytestmark = pytest.mark.unit


def _client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler, base_url="http://openai.test/v1")


def _ok_response(vectors: list[list[float]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "object": "list",
            "data": [
                {"object": "embedding", "index": i, "embedding": v}
                for i, v in enumerate(vectors)
            ],
            "model": "text-embedding-3-small",
        },
    )


async def test_embed_one_posts_expected_body_and_parses_vector() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return _ok_response([[0.1, 0.2, 0.3, 0.4]])

    emb = OpenAIEmbedder(
        base_url="http://openai.test/v1",
        model_id="text-embedding-3-small",
        api_key="sk-x",
        dimension=4,
        client=_client(httpx.MockTransport(handler)),
    )
    vec = await emb.embed_one("hello")
    assert vec == [0.1, 0.2, 0.3, 0.4]
    assert seen["url"] == "http://openai.test/v1/embeddings"
    assert seen["body"] == {"model": "text-embedding-3-small", "input": ["hello"]}


async def test_embed_batch_orders_by_index() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # Return the two embeddings OUT of order to prove sorting by index.
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [9.0, 9.0]},
                    {"index": 0, "embedding": [1.0, 1.0]},
                ]
            },
        )

    emb = OpenAIEmbedder(
        base_url="http://openai.test/v1",
        model_id="m",
        api_key="sk-x",
        dimension=2,
        client=_client(httpx.MockTransport(handler)),
    )
    out = await emb.embed_batch(["a", "b"])
    assert out == [[1.0, 1.0], [9.0, 9.0]]


async def test_dimension_mismatch_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok_response([[0.1, 0.2, 0.3]])  # 3 dims

    emb = OpenAIEmbedder(
        base_url="http://openai.test/v1",
        model_id="m",
        api_key="sk-x",
        dimension=4,  # declared 4, API returns 3
        client=_client(httpx.MockTransport(handler)),
    )
    with pytest.raises(RuntimeError, match="dimension 3 != declared 4"):
        await emb.embed_one("x")


async def test_non_200_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    emb = OpenAIEmbedder(
        base_url="http://openai.test/v1",
        model_id="m",
        api_key="bad",
        dimension=2,
        client=_client(httpx.MockTransport(handler)),
    )
    with pytest.raises(RuntimeError, match="HTTP 401"):
        await emb.embed_one("x")


async def test_empty_batch_short_circuits() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not call the API for an empty batch")

    emb = OpenAIEmbedder(
        base_url="http://openai.test/v1",
        model_id="m",
        api_key="sk-x",
        dimension=2,
        client=_client(httpx.MockTransport(handler)),
    )
    assert await emb.embed_batch([]) == []
