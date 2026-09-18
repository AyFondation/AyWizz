# =============================================================================
# File: Dockerfile
# Version: 3
# Path: infra/c13_extractor/docker/Dockerfile
# Description: C13 (AyExtractor) container image — HTTP wrapper around
#              `facade.analyze`. Per D-020 R-100-125 v2 §3 vendoring rule,
#              the image installs a wheel built from the local
#              `ay_extractor/` source tree (not from PyPI). The
#              `MONOREPO_GIT_SHA` build-arg is stamped in the runtime env so
#              `run_manifest.json` records the build commit alongside the
#              `ayextractor` package version.
#
#              The image is intentionally MINIMAL: per-format extractors
#              (PyMuPDF, python-docx, refextract), OpenAI SDK + boto3,
#              FastAPI + uvicorn. No heavyweight ML libs — embeddings are
#              computed by C8 (LiteLLM), not in-process.
#
#              CMD is production-grade (no --reload). Dev compose can
#              override with `command:` for live reload.
# =============================================================================

ARG PYTHON_VERSION=3.13-slim
ARG MONOREPO_GIT_SHA=dev

# ----------------------------------------------------------------------------
# Builder stage: install build deps, create venv, install ayextractor from
# source with the `[all]` extra so every Phase 1+2 capability lights up.
# ----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION} AS builder

LABEL stage="builder"

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY ay_extractor/pyproject.toml /app/ay_extractor/pyproject.toml
COPY ay_extractor/src /app/ay_extractor/src

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
RUN pip install --upgrade pip setuptools wheel
# `[all]` extra pulls in pdf/docx/llm/pipeline/minio/http/references.
RUN pip install -e "/app/ay_extractor[all]"

# ----------------------------------------------------------------------------
# Runtime stage: minimal Python image with the prepared venv + source tree.
# ----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION} AS runtime

LABEL org.opencontainers.image.title="C13 — AyExtractor" \
      org.opencontainers.image.description="Document extraction + chunking service (D-020 R-100-125 v2). HTTP surface POST /analyze + GET /status + GET /healthz. Writes R-400-220 v2 artifacts to MinIO. Routes all LLM calls through C8 LiteLLM via OPENAI_BASE_URL." \
      org.opencontainers.image.vendor="AyWizz" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later" \
      org.opencontainers.image.source="https://github.com/AyFondation/AyWizz" \
      org.opencontainers.image.documentation="https://github.com/AyFondation/AyWizz/blob/main/requirements/999-SYNTHESIS.md#d-020"

ARG MONOREPO_GIT_SHA=dev

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    MONOREPO_GIT_SHA="${MONOREPO_GIT_SHA}"

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app

RUN groupadd --system app && useradd --system --gid app --no-create-home app \
    && chown -R app:app /app

USER app
WORKDIR /app

EXPOSE 8000

# Production-grade CMD. Single-worker uvicorn — C13 is a long-running
# stateful runtime (in-memory _runs registry); horizontal scaling is
# achieved by multiple Deployments behind a load balancer, NOT by
# multiple uvicorn workers in one container.
#
# THE PROXY PRELUDE — identical construct to infra/docker/Dockerfile.api, and
# carried here for the same reason: C13 reaches MinIO and C7 by in-cluster
# Service name, `httpx` / `requests` honour `HTTP_PROXY`, and a managed
# cluster's injected `NO_PROXY` never lists this platform's Service names. The
# proxy then drops the call rather than refusing it, so the failure surfaces
# as a startup that never completes rather than as a connection error.
#
# APPEND, NEVER REPLACE — the injected value carries the cloud metadata
# endpoint and internal ranges, which must survive. Suffix matching never
# covers a single-label name (`.svc` does not match `minio`), so the bare
# Service names are listed explicitly. Both spellings are set.
CMD _NP="localhost,127.0.0.1,::1,.svc,.svc.cluster.local,.cluster.local,arangodb,ay-platform-ui,c1-gateway,c1-gateway-dashboard,c12,c12-workflow,c13-extractor,c16-backup,c2,c2-auth,c3-conversation,c4-orchestrator,c5-requirements,c6-validation,c7,c7-memory,c8-admin,c8-cost-receiver,c9-mcp,gitea,litellm,minio,mock-llm,nats,ollama" && \
    export NO_PROXY="${NO_PROXY:+${NO_PROXY},}${_NP}" && \
    export no_proxy="${no_proxy:+${no_proxy},}${_NP}" && \
    exec uvicorn "ayextractor.api.http:app" --host 0.0.0.0 --port 8000
