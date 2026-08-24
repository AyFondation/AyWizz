# =============================================================================
# File: test_reembed_trigger.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_reembed_trigger.py
# Description: Unit tests for the D-011/R-400-222 re-embed auto-trigger: a
#              vector-affecting embedding-model edit notifies every using
#              project; an alias-only edit does not; a project switching model
#              notifies (first selection / no-op re-select do not). Plus the
#              ReembedNotifier itself (no-op when unconfigured, best-effort
#              POST otherwise). In-memory fakes; no network for the service
#              tests.
# =============================================================================

from __future__ import annotations

import itertools
import json
from typing import Any

import httpx
import pytest

from ay_platform_core.c8_llm.registry.embedding_catalog_service import (
    EmbeddingCatalogService,
)
from ay_platform_core.c8_llm.registry.embedding_models import (
    EmbeddingAdapter,
    EmbeddingModelUpsert,
    EmbeddingProviderUpsert,
)
from ay_platform_core.c8_llm.registry.embedding_reembed_notifier import ReembedNotifier
from ay_platform_core.c8_llm.registry.embedding_service import (
    EmbeddingModelService,
    EmbeddingProviderService,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _Store:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def upsert(self, document: dict[str, Any]) -> None:
        self.docs[document["_key"]] = dict(document)

    async def get(self, key: str) -> dict[str, Any] | None:
        return self.docs.get(key)

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        return next((d for d in self.docs.values() if d.get("name") == name), None)

    async def get_by_alias(self, alias: str) -> dict[str, Any] | None:
        return next((d for d in self.docs.values() if d.get("alias") == alias), None)

    async def list_all(self) -> list[dict[str, Any]]:
        return list(self.docs.values())

    async def list_for_provider(self, provider_id: str) -> list[dict[str, Any]]:
        return [d for d in self.docs.values() if d.get("provider_id") == provider_id]

    async def list_for_tenant(self, tenant_id: str) -> list[dict[str, Any]]:
        return [d for d in self.docs.values() if d.get("tenant_id") == tenant_id]

    async def list_for_model(self, model_id: str) -> list[dict[str, Any]]:
        return [d for d in self.docs.values() if d.get("model_id") == model_id]

    async def list_using_model(self, model_id: str) -> list[dict[str, Any]]:
        return [d for d in self.docs.values() if d.get("model_id") == model_id]

    async def delete(self, key: str) -> bool:
        return self.docs.pop(key, None) is not None


class _FakeNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def notify(
        self, *, tenant_id: str, project_id: str, model_id: str
    ) -> None:
        self.calls.append((tenant_id, project_id, model_id))


def _ids() -> Any:
    return (f"id{i}" for i in itertools.count()).__next__


async def _make_model(
    project: _Store, notifier: _FakeNotifier
) -> tuple[EmbeddingModelService, str, str]:
    prov_store, model_store = _Store(), _Store()
    psvc = EmbeddingProviderService(
        prov_store, model_store, None, clock=lambda: "t", id_factory=_ids()
    )
    pub = await psvc.create_provider(
        EmbeddingProviderUpsert(
            name="P", adapter=EmbeddingAdapter.OLLAMA, base_url="http://x"
        )
    )
    msvc = EmbeddingModelService(
        model_store, prov_store, project, notifier, clock=lambda: "t", id_factory=_ids()
    )
    created = await msvc.create_model(
        EmbeddingModelUpsert(
            alias="mini",
            provider_id=pub.provider_id,
            upstream_model="all-minilm",
            dimension=384,
        )
    )
    return msvc, created.model_id, pub.provider_id


# --- Model edit trigger -----------------------------------------------------


async def test_vector_affecting_model_edit_notifies_using_projects() -> None:
    project = _Store()
    notifier = _FakeNotifier()
    msvc, model_id, provider_id = await _make_model(project, notifier)
    # Two projects (across tenants) select this model.
    project.docs["t1:pA"] = {
        "_key": "t1:pA", "tenant_id": "t1", "project_id": "pA", "model_id": model_id
    }
    project.docs["t2:pB"] = {
        "_key": "t2:pB", "tenant_id": "t2", "project_id": "pB", "model_id": model_id
    }
    # Dimension change → vectors change → both projects notified.
    await msvc.update_model(
        model_id,
        EmbeddingModelUpsert(
            alias="mini", provider_id=provider_id,
            upstream_model="all-minilm", dimension=768,
        ),
    )
    assert set(notifier.calls) == {
        ("t1", "pA", model_id),
        ("t2", "pB", model_id),
    }


async def test_alias_only_model_edit_does_not_notify() -> None:
    project = _Store()
    notifier = _FakeNotifier()
    msvc, model_id, provider_id = await _make_model(project, notifier)
    project.docs["t1:pA"] = {
        "_key": "t1:pA", "tenant_id": "t1", "project_id": "pA", "model_id": model_id
    }
    # Same upstream/dimension/provider — only the alias changes → no re-embed.
    await msvc.update_model(
        model_id,
        EmbeddingModelUpsert(
            alias="renamed", provider_id=provider_id,
            upstream_model="all-minilm", dimension=384,
        ),
    )
    assert notifier.calls == []


# --- Project selection switch trigger ---------------------------------------


async def test_project_switch_notifies_but_not_first_or_reselect() -> None:
    catalog, project, model = _Store(), _Store(), _Store()
    notifier = _FakeNotifier()
    csvc = EmbeddingCatalogService(catalog, project, model, notifier)
    for mid in ("m1", "m2"):
        catalog.docs[f"t1:{mid}"] = {
            "_key": f"t1:{mid}", "tenant_id": "t1", "model_id": mid,
            "enabled": True, "default_for_new_projects": False,
        }

    # First selection (no prior vectors) → no notify.
    await csvc.set_project_embedding("t1", "pA", "m1")
    assert notifier.calls == []

    # Switch to a different model → notify.
    await csvc.set_project_embedding("t1", "pA", "m2")
    assert notifier.calls == [("t1", "pA", "m2")]

    # Re-select the same model → no additional notify.
    await csvc.set_project_embedding("t1", "pA", "m2")
    assert notifier.calls == [("t1", "pA", "m2")]


# --- ReembedNotifier --------------------------------------------------------


async def test_notifier_is_noop_when_unconfigured() -> None:
    n = ReembedNotifier(webhook_url="")
    # Must not raise, must not attempt any request.
    await n.notify(tenant_id="t1", project_id="pA", model_id="m1")
    await n.aclose()


async def test_notifier_posts_expected_payload() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    n = ReembedNotifier(webhook_url="http://c12/uploads/reembed-project", client=client)
    await n.notify(tenant_id="t1", project_id="pA", model_id="m1")
    assert seen["url"] == "http://c12/uploads/reembed-project"
    assert seen["body"] == {
        "job": "reembed", "tenant_id": "t1", "project_id": "pA", "model_id": "m1"
    }
    await client.aclose()


async def test_notifier_swallows_transport_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    n = ReembedNotifier(webhook_url="http://c12/x", client=client)
    # Best-effort: a transport failure is logged, never raised.
    await n.notify(tenant_id="t1", project_id="pA", model_id="m1")
    await client.aclose()
