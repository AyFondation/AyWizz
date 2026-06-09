#!/usr/bin/env python3
# =============================================================================
# File: seed_demo_ux.py
# Version: 4
# Path: ay_platform_core/scripts/seed_demo_ux.py
# Description: Post-stack demo data seeder for the manual-test stack
#              brought up by `e2e_stack.sh dev`.
#
#              v4 (2026-06-05): LLM-governance demo seed — registers the 3
#              Claude tiers in the platform registry (as `superroot` /
#              tenant_manager), catalogues them for `tenant-test`, and sets
#              `model_quality=medium` on `project-test` so the registry /
#              catalogue / picker surfaces render populated and an upload
#              resolves a concrete model. No API key is set (dev c8-admin has
#              no master key ; the proxy authenticates upstream with its env
#              key — the registry only selects WHICH model runs).
#
#              Distinct from
#              `seed_e2e.py` (which targets the `demo` project for the
#              pytest e2e suite) — this one targets the `project-test`
#              that C2's `_ensure_demo_seed()` provisioned at lifespan
#              start, and uses the `tenant-admin` credentials surfaced
#              on /ux/config.
#
#              v3 (2026-05-12) : seeds an artifact demo run via the
#              new C4 admin endpoint (R-200-131). 4 sample files
#              (README.md, hello.py, requirements.txt, src/main.py)
#              so the new "Code source" section in the UX renders
#              with content immediately.
#
#              v2 (2026-05-11) : Phase D + E seeds — 1 empty C3
#              conversation (the operator chats it) and 1 C5
#              requirements document (so /requirements isn't empty).
#              Phase C source seed (v1) stays.
#
# Usage:
#   python ay_platform_core/scripts/seed_demo_ux.py [--base-url URL]
# =============================================================================

from __future__ import annotations

import argparse
import asyncio
import base64
import sys
import time
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://localhost:56000"
TENANT_ID = "tenant-test"
PROJECT_ID = "project-test"
DOCGEN_PROJECT_ID = "project-docgen"
DOCGEN_PROJECT_NAME = "Demo DocGen Project"
ADMIN_USERNAME = "tenant-admin"
ADMIN_PASSWORD = "dev-tenant"
# tenant_manager super-root — owns the platform LLM registry.
SUPERROOT_USERNAME = "superroot"
SUPERROOT_PASSWORD = "dev-superroot"

# LLM-governance demo seed (mirrors the canonical litellm-config.yaml model_list
# so the registry/catalogue/picker surfaces render with the same 3 Claude tiers
# the proxy actually serves). No API key is set — the dev c8-admin has no master
# key (key writes 503), and resolution does not need one: the proxy authenticates
# upstream with its own env key ; the registry only selects WHICH model runs.
# One platform PROVIDER (endpoint + credential) the 3 demo models reference by id.
GOVERNANCE_PROVIDER: dict[str, Any] = {
    "name": "Anthropic",
    "base_url": "https://api.anthropic.com",
    "wire_format": "anthropic",
}
GOVERNANCE_MODELS: list[dict[str, Any]] = [
    {
        "alias": "claude-haiku-fast",
        "upstream_model": "claude-haiku-4-5-20251001",
        "capabilities": {"vision": True, "tool_calling": True, "context_window": 200000},
        "provider_cost_in_per_1m": 0.80,
        "provider_cost_out_per_1m": 4.00,
        "default_model_quality": "low",
        "enabled": True,
    },
    {
        "alias": "claude-sonnet-midtier",
        "upstream_model": "claude-sonnet-4-6",
        "capabilities": {"vision": False, "tool_calling": True, "context_window": 200000},
        "provider_cost_in_per_1m": 3.00,
        "provider_cost_out_per_1m": 15.00,
        "default_model_quality": "medium",
        "enabled": True,
    },
    {
        "alias": "claude-opus-flagship",
        "upstream_model": "claude-opus-4-7",
        "capabilities": {"vision": True, "tool_calling": True, "context_window": 200000},
        "provider_cost_in_per_1m": 15.00,
        "provider_cost_out_per_1m": 75.00,
        "default_model_quality": "high",
        "enabled": True,
    },
]
# The project's chosen quality (drives ingestion model selection via resolution).
DEMO_PROJECT_MODEL_QUALITY = "medium"

# Sample source corpus — small text files so the parse → chunk → embed
# pipeline runs in well under a second. Each entry yields one row in the
# Sources page.
DEMO_CONVERSATION_TITLE = "Welcome to the test project"
DEMO_DOC_SLUG = "900-SPEC-DEMO"
DEMO_DOC_BODY = """---
document: 900-SPEC-DEMO
version: 1
path: projects/project-test/requirements/900-SPEC-DEMO.md
language: en
status: draft
---

# Demo spec for the manual-test stack

This document is seeded by `seed_demo_ux.py` so the **Requirements**
section has at least one entry to render. Replace or delete freely.

#### R-900-001

```yaml
id: R-900-001
version: 1
status: approved
category: functional
```

The platform SHALL allow project editors to browse seeded requirements
through the C5 read-only surface.
"""


# Demo artifact run — the 4 files mimic what a `codegen` pipeline
# would produce on a "Hello World" prompt. Deterministic run_id so
# re-running the seeder upserts the same row idempotently.
DEMO_ARTIFACT_RUN_ID = "demo-run-001"
DEMO_ARTIFACT_LABEL = "Demo run — Hello World scaffold"
DEMO_ARTIFACT_FILES: list[dict[str, str]] = [
    {
        "path": "README.md",
        "content": (
            "# Hello World scaffold\n\n"
            "Generated by the AyWizz demo seeder. Showcases the\n"
            "**Code source** section : tree on the left, Monaco\n"
            "preview on the right, no MinIO links exposed.\n\n"
            "## Files\n\n"
            "- `hello.py` — entry point.\n"
            "- `src/main.py` — the actual logic.\n"
            "- `requirements.txt` — Python dependencies.\n"
        ),
    },
    {
        "path": "hello.py",
        "content": (
            '"""Hello World entry point."""\n'
            "from src.main import greet\n\n"
            'if __name__ == "__main__":\n'
            '    print(greet("AyWizz"))\n'
        ),
    },
    {
        "path": "requirements.txt",
        "content": "# (no runtime deps yet)\n",
    },
    {
        "path": "src/main.py",
        "content": (
            '"""Core logic."""\n\n\n'
            "def greet(name: str) -> str:\n"
            '    """Return a friendly greeting."""\n'
            '    return f"Hello, {name}!"\n'
        ),
    },
]


# Demo artifact run for the DocGen project — a small library of
# markdown documents organised in nested folders so the VSCode-like
# tree view has something interesting to render at startup. README.md
# at the root mirrors the "every project has at least one README.md
# with its name inside" invariant (will be enforced at project
# creation time once the projects-router gets the bootstrap step).
# D-015 : the DocGen corpus is the perpetual `live-docs` run. Seeding
# into it (not a separate `demo-docs-001`) means chat-created documents
# appear in the SAME tree as the seeded library — one coherent corpus
# per project instead of two disjoint runs.
DOCGEN_ARTIFACT_RUN_ID = "live-docs"
DOCGEN_ARTIFACT_LABEL = "Live documents"
DOCGEN_ARTIFACT_FILES: list[dict[str, str]] = [
    {
        "path": "README.md",
        "content": (
            f"# {DOCGEN_PROJECT_NAME}\n\n"
            f"Welcome to **{DOCGEN_PROJECT_NAME}**.\n\n"
            "This project demos the DocGen profile — the assistant\n"
            "creates and updates documents in this tree as you chat.\n\n"
            "## Sections\n\n"
            "- `docs/getting-started.md` — operator-facing quickstart.\n"
            "- `docs/architecture/overview.md` — high-level diagram.\n"
            "- `reports/quarterly/Q1-2026.md` — sample report.\n"
        ),
    },
    {
        "path": "docs/getting-started.md",
        "content": (
            "# Getting started\n\n"
            "1. Open the **Conversations** tab.\n"
            "2. Tell the assistant what document you want.\n"
            "3. The assistant proposes a draft ; iterate by asking\n"
            "   for changes ; selected snippets get cited in the chat.\n"
        ),
    },
    {
        "path": "docs/architecture/overview.md",
        "content": (
            "# Architecture overview\n\n"
            "DocGen flows are conversational : the model invokes\n"
            "platform tools (`create_document` / `update_document` /\n"
            "`read_document`) that mutate the project's MinIO tree.\n"
            "Every change is also pushed to the project's Gitea repo\n"
            "so a clone gives an auditable history.\n"
        ),
    },
    {
        "path": "docs/architecture/sequence.md",
        "content": (
            "# Sequence — conversation-driven document update\n\n"
            "```\n"
            "User → C3 chat : 'add a section about caching'\n"
            "C3 → C8 LLM   : prompt + tool catalogue\n"
            "C8 ← LLM      : tool_call(update_document, ...)\n"
            "C3 → C4 art.  : write through ArtifactsService\n"
            "C3 → User     : confirmation + diff preview\n"
            "```\n"
        ),
    },
    {
        "path": "reports/quarterly/Q1-2026.md",
        "content": (
            "# Quarterly report — Q1 2026\n\n"
            "Placeholder report seeded by `seed_demo_ux.py`. Ask the\n"
            "assistant to expand each bullet — it will refine in-place.\n\n"
            "- Highlights\n"
            "- Risks\n"
            "- Next steps\n"
        ),
    },
    {
        "path": "reports/quarterly/Q2-2026-draft.md",
        "content": (
            "# Quarterly report — Q2 2026 (draft)\n\n"
            "Empty stub. Tell the assistant to draft a status update\n"
            "based on the Q1 report above.\n"
        ),
    },
]


DEMO_SOURCES: list[dict[str, str]] = [
    {
        "source_id": "demo-readme",
        "mime_type": "text/markdown",
        "filename": "README.md",
        "content": (
            "# AyWizz Test Project\n\n"
            "This is a demo source seeded by `seed_demo_ux.py`.\n\n"
            "## What it's for\n\n"
            "- Validate the **Sources** section renders with non-empty data\n"
            "- Exercise C7's Markdown parser end-to-end\n"
            "- Provide a known corpus for `chat with RAG` demos\n\n"
            "Edit me, replace me, or delete me — the seed is idempotent.\n"
        ),
    },
    {
        "source_id": "demo-platform-note",
        "mime_type": "text/plain",
        "filename": "platform-note.txt",
        "content": (
            "AyWizz platform — quick orientation note.\n\n"
            "Architecture is built around a domain-agnostic backbone\n"
            "with pluggable production domains. v1 ships the `code`\n"
            "profile only ; future profiles (data, doc, etc.) will\n"
            "plug into the same shell without UX rebuilds.\n\n"
            "The auth model has 5 roles : tenant_manager (super-root,\n"
            "content-blind), admin / tenant_admin, project_owner,\n"
            "project_editor, project_viewer.\n"
        ),
    },
]


class SeedError(RuntimeError):
    """Raised when the seeder cannot complete because the stack is unreachable or
    the seed data conflicts irrecoverably."""


async def wait_stack_ready(
    base_url: str, *, timeout_s: float, poll_interval_s: float = 1.0
) -> None:
    """Block until `/ux/config` returns 200 AND `dev_credentials`
    is populated (which means C2's `_ensure_demo_seed` has run)."""
    deadline = time.monotonic() + timeout_s
    last_err: str | None = None
    async with httpx.AsyncClient(timeout=3.0) as client:
        while time.monotonic() < deadline:
            try:
                resp = await client.get(f"{base_url}/ux/config")
                if resp.status_code == 200:
                    body = resp.json()
                    creds = body.get("dev_credentials") or []
                    if creds:
                        return
                    last_err = "/ux/config 200 but dev_credentials empty"
                else:
                    last_err = f"/ux/config -> HTTP {resp.status_code}"
            except httpx.RequestError as exc:
                last_err = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(poll_interval_s)
    raise SeedError(f"stack never became ready: {last_err!r}")


async def obtain_token(
    client: httpx.AsyncClient,
    base_url: str,
    username: str = ADMIN_USERNAME,
    password: str = ADMIN_PASSWORD,
) -> str:
    """Login and return the access token. Defaults to the demo `tenant-admin`
    (`admin` role, full r/w in `tenant-test`); pass `superroot` for the
    tenant_manager-gated platform registry."""
    resp = await client.post(
        f"{base_url}/auth/login",
        json={"username": username, "password": password},
    )
    if resp.status_code != 200:
        raise SeedError(
            f"/auth/login as {username} failed: {resp.status_code} {resp.text}"
        )
    token = resp.json().get("access_token")
    if not token:
        raise SeedError(f"no access_token in /auth/login response: {resp.text}")
    return str(token)


async def ensure_provider(client: httpx.AsyncClient, base_url: str, token: str) -> str:
    """Ensure the platform PROVIDER exists (tenant_manager). Idempotent by name.
    Returns the stable provider_id."""
    headers = {"Authorization": f"Bearer {token}"}
    listing = await client.get(f"{base_url}/admin/v1/llm/providers", headers=headers)
    if listing.status_code == 200:
        for p in listing.json().get("providers", []):
            if p.get("name") == GOVERNANCE_PROVIDER["name"]:
                return str(p["provider_id"])
    resp = await client.post(
        f"{base_url}/admin/v1/llm/providers", headers=headers, json=GOVERNANCE_PROVIDER
    )
    if resp.status_code == 201:
        return str(resp.json()["provider_id"])
    raise SeedError(f"provider seed failed: {resp.status_code} {resp.text[:200]}")


async def ensure_registry_model(
    client: httpx.AsyncClient, base_url: str, token: str, provider_id: str, model: dict[str, Any]
) -> str:
    """Ensure a model exists in the PLATFORM registry (tenant_manager),
    referencing `provider_id`. Idempotent by alias. Returns the model_id."""
    headers = {"Authorization": f"Bearer {token}"}
    listing = await client.get(f"{base_url}/admin/v1/llm/registry", headers=headers)
    if listing.status_code == 200:
        for m in listing.json().get("models", []):
            if m.get("alias") == model["alias"]:
                return str(m["model_id"])
    resp = await client.post(
        f"{base_url}/admin/v1/llm/registry",
        headers=headers,
        json={**model, "provider_id": provider_id},
    )
    if resp.status_code == 201:
        return str(resp.json()["model_id"])
    raise SeedError(
        f"registry seed {model['alias']!r} failed: {resp.status_code} {resp.text[:200]}"
    )


async def ensure_catalog_model(
    client: httpx.AsyncClient, base_url: str, token: str, model_id: str
) -> str:
    """Enable a registry model (by id) in the tenant CATALOGUE (admin)."""
    resp = await client.put(
        f"{base_url}/api/v1/llm/catalog/{model_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"enabled": True, "default_for_new_projects": True},
    )
    if resp.status_code == 200:
        return "created"
    raise SeedError(
        f"catalogue seed {model_id!r} failed: {resp.status_code} {resp.text[:200]}"
    )


async def ensure_project_model_quality(
    client: httpx.AsyncClient, base_url: str, token: str
) -> str:
    """Set `model_quality` on project-test's enrichment config (merging it into
    the current config so other enrichment fields are preserved)."""
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{base_url}/api/v1/memory/projects/{PROJECT_ID}/enrichment-config"
    get_resp = await client.get(url, headers=headers)
    cfg: dict[str, Any] = get_resp.json() if get_resp.status_code == 200 else {}
    cfg["model_quality"] = DEMO_PROJECT_MODEL_QUALITY
    put_resp = await client.put(url, headers=headers, json=cfg)
    if put_resp.status_code == 200:
        return "created"
    raise SeedError(
        f"project model_quality seed failed: "
        f"{put_resp.status_code} {put_resp.text[:200]}"
    )


async def ensure_demo_conversation(
    client: httpx.AsyncClient, base_url: str, token: str,
) -> str:
    """Create one empty C3 conversation scoped to project-test.
    Returns "created" / "exists" — `exists` when a conversation with
    the same title already lives under the caller."""
    headers = {"Authorization": f"Bearer {token}"}
    # Check existing conversations first (idempotency).
    resp_list = await client.get(
        f"{base_url}/api/v1/conversations", headers=headers,
    )
    if resp_list.status_code == 200:
        existing = resp_list.json().get("conversations", [])
        for c in existing:
            if (
                c.get("project_id") == PROJECT_ID
                and c.get("title") == DEMO_CONVERSATION_TITLE
            ):
                return "exists"

    resp = await client.post(
        f"{base_url}/api/v1/conversations",
        json={"title": DEMO_CONVERSATION_TITLE, "project_id": PROJECT_ID},
        headers=headers,
    )
    if resp.status_code != 201:
        raise SeedError(
            f"create conversation failed: {resp.status_code} {resp.text}"
        )
    return "created"


async def ensure_demo_requirements_doc(
    client: httpx.AsyncClient, base_url: str, token: str,
) -> str:
    """Create + populate a single demo requirements document in C5.
    Two-step (POST then PUT) per C5's API. Idempotent : a 409 on
    create maps to `exists` ; a 412/428 etag mismatch on PUT maps to
    `exists` (already seeded with this content)."""
    headers = {"Authorization": f"Bearer {token}"}
    create_resp = await client.post(
        f"{base_url}/api/v1/projects/{PROJECT_ID}/requirements/documents",
        json={"slug": DEMO_DOC_SLUG},
        headers=headers,
    )
    if create_resp.status_code not in (201, 409):
        raise SeedError(
            f"create doc failed: {create_resp.status_code} {create_resp.text}"
        )

    put_headers = {**headers, "If-Match": f'"{DEMO_DOC_SLUG}@v1"'}
    put_resp = await client.put(
        f"{base_url}/api/v1/projects/{PROJECT_ID}/requirements/documents/{DEMO_DOC_SLUG}",
        json={"content": DEMO_DOC_BODY},
        headers=put_headers,
    )
    if put_resp.status_code == 200:
        return "created"
    if put_resp.status_code in (412, 428):
        return "exists"
    raise SeedError(f"put doc failed: {put_resp.status_code} {put_resp.text}")


async def ensure_demo_source(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    entry: dict[str, str],
) -> str:
    """Upload one demo source via the multipart endpoint.

    Returns "created", "exists", or raises. C7 returns 409 when the
    `source_id` already exists in this project — idempotent on re-run.
    """
    headers = {"Authorization": f"Bearer {token}"}
    # multipart form : file, source_id, mime_type
    files = {
        "file": (
            entry["filename"],
            entry["content"].encode("utf-8"),
            entry["mime_type"],
        ),
    }
    data = {
        "source_id": entry["source_id"],
        "mime_type": entry["mime_type"],
    }
    resp = await client.post(
        f"{base_url}/api/v1/memory/projects/{PROJECT_ID}/sources/upload",
        files=files,
        data=data,
        headers=headers,
    )
    if resp.status_code == 201:
        return "created"
    if resp.status_code == 409:
        return "exists"
    raise SeedError(
        f"upload source {entry['source_id']!r} failed: "
        f"{resp.status_code} {resp.text}"
    )


async def ensure_demo_artifacts(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    *,
    project_id: str = PROJECT_ID,
    run_id: str = DEMO_ARTIFACT_RUN_ID,
    label: str = DEMO_ARTIFACT_LABEL,
    files: list[dict[str, str]] | None = None,
) -> str:
    """POST a demo artifact run via the C4 admin seed endpoint for
    `project_id`. Idempotent : same `run_id` upserts the row +
    re-uploads each file (MinIO key collision is overwritten).
    Returns the descriptor (`run/<id>`) the caller logs."""
    payload_files = files if files is not None else DEMO_ARTIFACT_FILES
    payload = {
        "run_id": run_id,
        "label": label,
        "files": [
            {
                "path": f["path"],
                "content_b64": base64.b64encode(
                    f["content"].encode("utf-8"),
                ).decode("ascii"),
            }
            for f in payload_files
        ],
    }
    resp = await client.post(
        f"{base_url}/api/v1/admin/projects/{project_id}/artifacts/seed",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=30.0,
    )
    if resp.status_code in (200, 201):
        body = resp.json()
        return f"run/{body.get('run_id', run_id)}"
    raise SeedError(
        f"artifact seed for project {project_id!r} failed: "
        f"{resp.status_code} {resp.text[:200]}",
    )


async def seed_governance(
    client: httpx.AsyncClient, base_url: str, admin_token: str
) -> list[str]:
    """Seed the LLM-governance demo data: the platform registry (as
    tenant_manager super-root) + the tenant catalogue + the project's
    model_quality. Best-effort — a failure leaves the rest of the demo intact.
    Returns the list of created descriptors for the run summary."""
    created: list[str] = []
    try:
        su_token = await obtain_token(
            client, base_url, SUPERROOT_USERNAME, SUPERROOT_PASSWORD
        )
        provider_id = await ensure_provider(client, base_url, su_token)
        print(f"   [created] llm-provider: {GOVERNANCE_PROVIDER['name']}")
        created.append("llm-provider/Anthropic")
        model_ids: list[str] = []
        for model in GOVERNANCE_MODELS:
            model_ids.append(
                await ensure_registry_model(client, base_url, su_token, provider_id, model)
            )
        print(f"   [created] llm-registry: {len(GOVERNANCE_MODELS)} models")
        created.append(f"llm-registry/{len(GOVERNANCE_MODELS)}")
        for model_id in model_ids:
            await ensure_catalog_model(client, base_url, admin_token, model_id)
        print(f"   [created] llm-catalogue: {len(GOVERNANCE_MODELS)} models")
        created.append(f"llm-catalogue/{len(GOVERNANCE_MODELS)}")
        await ensure_project_model_quality(client, base_url, admin_token)
        print(f"   [created] project model_quality: {DEMO_PROJECT_MODEL_QUALITY}")
        created.append(f"model-quality/{DEMO_PROJECT_MODEL_QUALITY}")
    except SeedError as exc:
        print(f"   [error] llm-governance: {exc}", file=sys.stderr)
    return created


async def run(args: argparse.Namespace) -> int:
    print(f"==> Waiting for stack at {args.base_url}…")
    await wait_stack_ready(args.base_url, timeout_s=args.timeout_s)

    print(f"==> Logging in as {ADMIN_USERNAME}…")
    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await obtain_token(client, args.base_url)

        print(f"==> Seeding {len(DEMO_SOURCES)} source(s) into {PROJECT_ID}…")
        results: dict[str, list[str]] = {"created": [], "exists": []}
        for entry in DEMO_SOURCES:
            try:
                outcome = await ensure_demo_source(client, args.base_url, token, entry)
                results[outcome].append(entry["source_id"])
                print(f"   [{outcome}] source/{entry['source_id']}")
            except SeedError as exc:
                # Non-fatal on individual sources — keep going. C7 may
                # transiently reject (e.g. if Ollama is still warming).
                print(f"   [error] source/{entry['source_id']}: {exc}", file=sys.stderr)

        # Phase D : seed one empty conversation so /conversations is
        # non-empty. The operator chats it manually.
        try:
            outcome = await ensure_demo_conversation(client, args.base_url, token)
            results[outcome].append(f"conversation/{DEMO_CONVERSATION_TITLE!r}")
            print(f"   [{outcome}] conversation: {DEMO_CONVERSATION_TITLE!r}")
        except SeedError as exc:
            print(f"   [error] conversation: {exc}", file=sys.stderr)

        # Phase E : seed one requirements document so /requirements
        # has something to render.
        try:
            outcome = await ensure_demo_requirements_doc(client, args.base_url, token)
            results[outcome].append(f"doc/{DEMO_DOC_SLUG}")
            print(f"   [{outcome}] requirements doc: {DEMO_DOC_SLUG}")
        except SeedError as exc:
            print(f"   [error] requirements doc: {exc}", file=sys.stderr)

        # Phase Artifacts : seed one C4 artifact run per demo project
        # so both the CodeGen "Code source" tab AND the DocGen
        # "Documents" tab have something to browse on day one.
        # Non-fatal — each section shows an empty state on failure.
        try:
            descriptor = await ensure_demo_artifacts(client, args.base_url, token)
            results["created"].append(descriptor)
            print(f"   [created] {descriptor}")
        except SeedError as exc:
            print(f"   [error] artifacts (code): {exc}", file=sys.stderr)
        try:
            descriptor = await ensure_demo_artifacts(
                client,
                args.base_url,
                token,
                project_id=DOCGEN_PROJECT_ID,
                run_id=DOCGEN_ARTIFACT_RUN_ID,
                label=DOCGEN_ARTIFACT_LABEL,
                files=DOCGEN_ARTIFACT_FILES,
            )
            results["created"].append(f"docgen/{descriptor}")
            print(f"   [created] docgen/{descriptor}")
        except SeedError as exc:
            print(f"   [error] artifacts (docgen): {exc}", file=sys.stderr)

        # Phase Governance : registry + tenant catalogue + project model_quality.
        results["created"].extend(
            await seed_governance(client, args.base_url, token)
        )

    summary: dict[str, Any] = {
        "base_url": args.base_url,
        "project_id": PROJECT_ID,
        "created": results["created"],
        "exists": results["exists"],
    }
    print("SEED DEMO UX OK:", summary)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed demo data for the manual-test UX stack."
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Public Traefik URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=120.0,
        help="Stack readiness timeout in seconds.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    rc = asyncio.run(run(_parse_args()))
    sys.exit(rc)
