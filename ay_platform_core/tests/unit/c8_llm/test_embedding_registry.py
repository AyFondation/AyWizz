# =============================================================================
# File: test_embedding_registry.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_embedding_registry.py
# Description: Block 1 of the in-app EMBEDDING registry (D-011, parallel to the
#              chat registry): the Pydantic contracts (round-trip, write-only
#              key projection, DIMENSION validation) and the ArangoDB repos
#              exercised over a fake db (upsert / get / get_by_alias /
#              list_for_provider for the future delete-guard / delete).
# =============================================================================

from __future__ import annotations

from typing import Any

import pytest

from ay_platform_core.c8_llm.registry.embedding_models import (
    EmbeddingAdapter,
    EmbeddingModelEntry,
    EmbeddingProviderEntry,
)
from ay_platform_core.c8_llm.registry.embedding_repository import (
    EmbeddingModelRepository,
    EmbeddingProviderRepository,
)

pytestmark = pytest.mark.unit


# --- Contracts --------------------------------------------------------------


def test_provider_roundtrip_and_public_hides_secret() -> None:
    prov = EmbeddingProviderEntry(
        provider_id="ep1",
        name="Ollama-local",
        adapter=EmbeddingAdapter.OLLAMA,
        base_url="http://ollama:11434",
        api_key_ciphertext="ay.1.k1.X.Y",
        api_key_hint="…cd",
        effective_from="2026-08-20T00:00:00+00:00",
    )
    doc = prov.to_document()
    assert doc["_key"] == "ep1"
    assert EmbeddingProviderEntry.from_document(doc) == prov
    pub = prov.to_public()
    assert pub.key_status == "set"
    # The public projection carries NO ciphertext field at all.
    assert "api_key_ciphertext" not in pub.model_dump()


def test_provider_public_key_status_not_set_without_ciphertext() -> None:
    prov = EmbeddingProviderEntry(
        provider_id="ep2", name="det", adapter=EmbeddingAdapter.DETERMINISTIC_HASH,
        effective_from="t",
    )
    assert prov.base_url == ""  # deterministic-hash needs no endpoint
    assert prov.to_public().key_status == "not_set"


def test_model_requires_positive_dimension() -> None:
    with pytest.raises(ValueError, match="dimension"):
        EmbeddingModelEntry(
            model_id="m1", alias="mini", provider_id="ep1",
            upstream_model="all-minilm", dimension=0, effective_from="t",
        )


def test_model_roundtrip_and_public() -> None:
    m = EmbeddingModelEntry(
        model_id="m1", alias="mini", provider_id="ep1",
        upstream_model="all-minilm", dimension=384, effective_from="t",
    )
    assert EmbeddingModelEntry.from_document(m.to_document()) == m
    pub = m.to_public()
    assert pub.dimension == 384 and pub.alias == "mini"


# --- Repositories over a fake db -------------------------------------------


class _FakeColl:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.indexes: list[dict[str, Any]] = []

    def insert(self, document: dict[str, Any], overwrite: bool = False) -> None:
        self.docs[document["_key"]] = dict(document)

    def get(self, key: str) -> dict[str, Any] | None:
        return self.docs.get(key)

    def delete(self, key: str) -> None:
        self.docs.pop(key, None)

    def all(self) -> list[dict[str, Any]]:
        return list(self.docs.values())

    def add_index(self, spec: dict[str, Any]) -> None:
        self.indexes.append(spec)


class _FakeAql:
    def __init__(self, db: _FakeDb) -> None:
        self._db = db

    def execute(self, query: str, bind_vars: dict[str, Any]) -> Any:
        coll = self._db.coll(bind_vars["@coll"])
        rows = coll.all()
        if "@alias" in query or "m.alias == @alias" in query:
            return [r for r in rows if r.get("alias") == bind_vars.get("alias")]
        if "p.name == @name" in query:
            return [r for r in rows if r.get("name") == bind_vars.get("name")]
        if "m.provider_id == @pid" in query:
            return [r for r in rows if r.get("provider_id") == bind_vars.get("pid")]
        return rows


class _FakeDb:
    def __init__(self) -> None:
        self._colls: dict[str, _FakeColl] = {}
        self.aql = _FakeAql(self)

    def collections(self) -> list[dict[str, str]]:
        return [{"name": n} for n in self._colls]

    def create_collection(self, name: str) -> None:
        self._colls[name] = _FakeColl()

    def collection(self, name: str) -> _FakeColl:
        return self._colls.setdefault(name, _FakeColl())

    def coll(self, name: str) -> _FakeColl:
        return self.collection(name)


@pytest.mark.asyncio
async def test_provider_repo_crud_and_get_by_name() -> None:
    db = _FakeDb()
    repo = EmbeddingProviderRepository(db)
    await repo.ensure_collections()
    doc = EmbeddingProviderEntry(
        provider_id="ep1", name="Ollama", adapter=EmbeddingAdapter.OLLAMA,
        base_url="http://ollama:11434", effective_from="t",
    ).to_document()
    await repo.upsert(doc)
    got = await repo.get("ep1")
    assert got is not None and got["name"] == "Ollama"
    by_name = await repo.get_by_name("Ollama")
    assert by_name is not None and by_name["provider_id"] == "ep1"
    assert await repo.get_by_name("nope") is None
    assert await repo.delete("ep1") is True
    assert await repo.delete("ep1") is False


@pytest.mark.asyncio
async def test_model_repo_list_for_provider_backs_delete_guard() -> None:
    db = _FakeDb()
    repo = EmbeddingModelRepository(db)
    await repo.ensure_collections()
    for i, pid in enumerate(["ep1", "ep1", "ep2"]):
        await repo.upsert(
            EmbeddingModelEntry(
                model_id=f"m{i}", alias=f"a{i}", provider_id=pid,
                upstream_model="all-minilm", dimension=384, effective_from="t",
            ).to_document()
        )
    on_ep1 = await repo.list_for_provider("ep1")
    assert {m["model_id"] for m in on_ep1} == {"m0", "m1"}
    a2 = await repo.get_by_alias("a2")
    assert a2 is not None and a2["provider_id"] == "ep2"
    assert len(await repo.list_all()) == 3
