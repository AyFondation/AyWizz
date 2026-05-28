<!--
File: README.md
Version: 6
Path: README.md
-->

# AyWizz

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Commercial License](https://img.shields.io/badge/license-commercial%20available-orange.svg)](LICENSE-COMMERCIAL.md)
![tests](https://github.com/AyFondation/AyWizz/actions/workflows/ci-tests.yml/badge.svg?branch=main)
![build](https://github.com/AyFondation/AyWizz/actions/workflows/ci-build-images.yml/badge.svg?branch=main)
![coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Sorriso/69f0ecb4365b782a836a18852d8189ed/raw/aywizz-coverage.json)

> Requirements-driven conversational platform for AI-accelerated artifact generation, with traceability and rigor at the level of regulated industries (automotive cybersecurity, ASPICE, safety-critical).

> **Licensed under [AGPL-3.0](LICENSE)** for open-source / non-commercial use.
> **Commercial licensing** available for proprietary or closed-source use — see [LICENSE-COMMERCIAL.md](LICENSE-COMMERCIAL.md).

---

## What it is

AyWizz turns "vibe coding" into **requirements-driven generation**: every produced artifact traces back to an explicit, versioned requirement, and every requirement is continuously validated against its implementation. Domain-agnostic backbone with pluggable production domains (v1 ships the `code` domain).

**Stack** — Python 3.13 / FastAPI · ArangoDB (vector + graph) · MinIO · n8n · LiteLLM · Traefik · Kubernetes (Docker Desktop locally, AKS in production).

## Repository layout

| Path | Role |
|---|---|
| [`requirements/`](requirements/) | Platform-wide specs — single source of truth |
| [`ay_platform_core/`](ay_platform_core/) | Python backbone (FastAPI components C2…C9, orchestration, memory, LLM client) |
| [`infra/`](infra/) | Infrastructure artifacts (Dockerfiles, Traefik, n8n, K8s manifests per component) |
| [`ay_platform_ui/`](ay_platform_ui/) | Next.js / TypeScript frontend (scaffold) |
| [`.github/workflows/`](.github/workflows/) | CI: tests + coherence + image build to GHCR |

## Documentation map

Read in this order to get up to speed:

1. [`requirements/050-ARCHITECTURE-OVERVIEW.md`](requirements/050-ARCHITECTURE-OVERVIEW.md) — one-page topology snapshot, credentials, "implemented vs. specified".
2. [`requirements/999-SYNTHESIS.md`](requirements/999-SYNTHESIS.md) — cross-cutting decisions (D-001 … D-014), roadmap.
3. [`requirements/100-SPEC-ARCHITECTURE.md`](requirements/100-SPEC-ARCHITECTURE.md) — component decomposition, contracts, deployment targets.
4. Detailed specs per area: [`300-`](requirements/300-SPEC-REQUIREMENTS-MGMT.md) requirements management, [`400-`](requirements/400-SPEC-MEMORY-RAG.md) memory & RAG, [`700-`](requirements/700-SPEC-VERTICAL-COHERENCE.md) coherence engine, [`800-`](requirements/800-SPEC-LLM-ABSTRACTION.md) LLM gateway.
5. [`CLAUDE.md`](CLAUDE.md) — operating manual for AI-assisted contributions to this monorepo.

## Quick start

Open the repo in **VS Code** with the **Dev Containers** extension, then *Reopen in Container* — Python 3.13 and the full toolchain are provisioned automatically.

```bash
# Run the full test suite (unit + contract + integration + coverage gate)
bash ay_platform_core/scripts/run_tests.sh local

# Bring up the deployable local stack
# (Traefik + ArangoDB + MinIO + n8n + every Python component on the shared ay-api:local image)
bash ay_platform_core/scripts/e2e_stack.sh up
```

Public ingress: `http://localhost:${PORT_C1_PUBLIC:-56000}` (host-port scheme defined by R-100-122).

## CI / CD

- [**`ci-tests`**](.github/workflows/ci-tests.yml) — runs on every push to `main`. Two parallel jobs: `tests` (unit + contract + integration + 80% line-coverage gate enforced by `pyproject.toml`) and `coherence` (spec ↔ code + code ↔ code AST checks). Both blocking.
- [**`ci-build-images`**](.github/workflows/ci-build-images.yml) — runs only when `ci-tests` succeeds. Builds `infra/docker/Dockerfile.api` and pushes `ghcr.io/ayfondation/aywizz-api:{latest,main,sha-<short>}` to GitHub Container Registry. No image is ever published from a broken commit.

## Project state

Live state lives in [`.claude/SESSION-STATE.md`](.claude/SESSION-STATE.md). At a glance: backbone components C1 (Traefik), C2 (Auth), C3 (Conversation), C4 (Orchestrator), C5 (Requirements), C6 (Validation), C7 (Memory), C8 (LLM Gateway), C9 (MCP), and C12 (Workflow Engine, n8n) are delivered; end-to-end deployable stack validated locally. Upcoming: production K8s manifests, the Next.js UI, and C15 sub-agent runtime.
