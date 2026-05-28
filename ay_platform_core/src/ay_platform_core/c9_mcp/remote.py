# =============================================================================
# File: remote.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c9_mcp/remote.py
# Description: Thin HTTP adapters that expose the C5 + C6 service facades
#              that C9 tools call. In tests we pass real in-process services;
#              in the deployed container we pass instances of these adapters,
#              which translate method calls into HTTP requests to C5 / C6.
#              This keeps the tool handlers identical across both wirings.
#
# @relation implements:R-100-015
#
#              Only the small subset of service methods actually called by
#              the C9 tool adapters is implemented. Signatures mirror the
#              real services so the tools don't care which wiring they run
#              against.
# =============================================================================

from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException, status

from ay_platform_core.c5_requirements.models import (
    DocumentPublic,
    EntityPublic,
    RelationEdge,
    RelationType,
    RequirementStatus,
)
from ay_platform_core.c6_validation.models import (
    CodeArtifact,
    Finding,
    FindingPage,
    PluginDescriptor,
    RunTriggerRequest,
    RunTriggerResponse,
    ValidationRun,
)


def _raise_for_http(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail=detail)


class RemoteRequirementsService:
    """HTTP adapter exposing the subset of ``RequirementsService`` that C9 uses."""

    def __init__(self, base_url: str, client: httpx.AsyncClient) -> None:
        self._base = base_url.rstrip("/")
        self._client = client

    async def list_entities(
        self,
        project_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
        status_filter: RequirementStatus | None = None,
        category_filter: str | None = None,
        domain_filter: str | None = None,
        text_filter: str | None = None,
    ) -> tuple[list[EntityPublic], str | None]:
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        if status_filter is not None:
            params["status"] = status_filter.value
        if category_filter is not None:
            params["category"] = category_filter
        if domain_filter is not None:
            params["domain"] = domain_filter
        if text_filter is not None:
            params["text"] = text_filter
        resp = await self._client.get(
            f"{self._base}/api/v1/projects/{project_id}/requirements/entities",
            params=params,
        )
        _raise_for_http(resp)
        body = resp.json()
        entities = [EntityPublic.model_validate(e) for e in body.get("entities", [])]
        return entities, body.get("next_cursor")

    async def get_entity(self, project_id: str, entity_id: str) -> EntityPublic:
        resp = await self._client.get(
            f"{self._base}/api/v1/projects/{project_id}/requirements/entities/{entity_id}"
        )
        _raise_for_http(resp)
        return EntityPublic.model_validate(resp.json())

    async def list_documents(
        self,
        project_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[DocumentPublic], str | None]:
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        resp = await self._client.get(
            f"{self._base}/api/v1/projects/{project_id}/requirements/documents",
            params=params,
        )
        _raise_for_http(resp)
        body = resp.json()
        docs = [DocumentPublic.model_validate(d) for d in body.get("documents", [])]
        return docs, body.get("next_cursor")

    async def get_document(
        self, project_id: str, slug: str
    ) -> DocumentPublic:
        resp = await self._client.get(
            f"{self._base}/api/v1/projects/{project_id}/requirements/documents/{slug}"
        )
        _raise_for_http(resp)
        return DocumentPublic.model_validate(resp.json())

    async def list_relations(
        self, project_id: str, source_id: str, rel_type: RelationType | None
    ) -> list[RelationEdge]:
        params: dict[str, Any] = {"source": source_id}
        if rel_type is not None:
            params["type"] = rel_type.value
        resp = await self._client.get(
            f"{self._base}/api/v1/projects/{project_id}/requirements/relations",
            params=params,
        )
        _raise_for_http(resp)
        body = resp.json()
        return [RelationEdge.model_validate(r) for r in body.get("relations", [])]


class RemoteValidationService:
    """HTTP adapter exposing the subset of ``ValidationService`` that C9 uses."""

    def __init__(self, base_url: str, client: httpx.AsyncClient) -> None:
        self._base = base_url.rstrip("/")
        self._client = client

    def list_plugins(self) -> list[PluginDescriptor]:
        # Synchronous method in ValidationService; emulate with httpx.get (sync
        # wrapping would add complexity; instead, panic: C9 tools call this
        # from within an async handler, so expose an async path via list_*.
        raise NotImplementedError("use alist_plugins for remote adapter")

    def list_domains(self) -> list[str]:
        raise NotImplementedError("use alist_domains for remote adapter")

    async def alist_plugins(self) -> list[PluginDescriptor]:
        resp = await self._client.get(f"{self._base}/api/v1/validation/plugins")
        _raise_for_http(resp)
        return [PluginDescriptor.model_validate(p) for p in resp.json()]

    async def alist_domains(self) -> list[str]:
        resp = await self._client.get(f"{self._base}/api/v1/validation/domains")
        _raise_for_http(resp)
        body = resp.json()
        domains = body.get("domains", [])
        return [str(d) for d in domains]

    async def trigger_run(
        self,
        payload: RunTriggerRequest,
        *,
        requirements: list[dict[str, Any]],
        artifacts: list[CodeArtifact],
    ) -> RunTriggerResponse:
        body = payload.model_dump(mode="json")
        # requirements/artifacts already live inside payload; upstream C6 consumes them
        body["requirements"] = requirements
        body["artifacts"] = [a.model_dump(mode="json") for a in artifacts]
        resp = await self._client.post(
            f"{self._base}/api/v1/validation/runs", json=body
        )
        if resp.status_code not in (200, 202):
            _raise_for_http(resp)
        return RunTriggerResponse.model_validate(resp.json())

    async def get_run(self, run_id: str) -> ValidationRun:
        resp = await self._client.get(
            f"{self._base}/api/v1/validation/runs/{run_id}"
        )
        _raise_for_http(resp)
        return ValidationRun.model_validate(resp.json())

    async def list_findings(
        self, run_id: str, *, limit: int = 100, offset: int = 0
    ) -> FindingPage:
        resp = await self._client.get(
            f"{self._base}/api/v1/validation/runs/{run_id}/findings",
            params={"limit": limit, "offset": offset},
        )
        _raise_for_http(resp)
        return FindingPage.model_validate(resp.json())

    async def get_finding(self, finding_id: str) -> Finding:
        resp = await self._client.get(
            f"{self._base}/api/v1/validation/findings/{finding_id}"
        )
        if resp.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status_code=404, detail="finding not found")
        _raise_for_http(resp)
        return Finding.model_validate(resp.json())
