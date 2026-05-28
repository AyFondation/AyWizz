<!-- =============================================================================
File: deployment.md
Version: 2
Path: infra/c13_extractor/docs/deployment.md
Description: Operator-facing deployment guide for C13 (AyExtractor) —
             container build, env wiring, k8s overlay activation, MinIO
             bucket setup, n8n credential setup, smoke test. Reference
             doc for the D-020 ingestion chain (n8n webhook → C13 →
             C7 /ingest-chunks).
============================================================================= -->

# C13 (AyExtractor) — Deployment Guide

This document covers the operator-facing steps to deploy C13 — the
external **Extraction & Chunking Service** the platform consumes per
**D-020** and **R-100-125 v2**. C13 is a dependency component (alongside
C10 MinIO, C11 ArangoDB, C12 n8n) ; it is NOT imported by any AyWizz
component (zero `import ayextractor` from `ay_platform_core/`).

For the spec, see:
- `requirements/100-SPEC-ARCHITECTURE.md` § R-100-125 (C13 contract).
- `requirements/400-SPEC-MEMORY-RAG.md` § R-400-220..225 (MinIO layout,
  ChunkRich, /ingest-chunks).
- `requirements/800-SPEC-LLM-ABSTRACTION.md` § R-800-130..134 (agents,
  screener, prompt caching).
- `requirements/999-SYNTHESIS.md` § D-020 v2.

---

## 1. Architecture recap

```
User upload
  └─► C1 Gateway (forward-auth)
       └─► C12 (n8n) — workflow `extract_and_ingest.json`
            ├─► MinIO PUT raw bytes  (bucket: `sources`)
            ├─► C13 POST /analyze    (async kick-off)
            │     └─► extract + chunk + write artifacts to MinIO
            │           (bucket: `c13-extractor-artifacts`)
            ├─► C13 GET /status      (poll until terminal,
            │                         MinIO-backed fallback)
            ├─► MinIO GET chunks.jsonl + run_manifest.json
            └─► C7 POST /ingest-chunks (R-400-223 v2 pure-INSERT)
                  └─► Arango `memory_chunks` (vectors as-is)
```

C13 itself routes ALL LLM calls through **C8 (LiteLLM)** via the
`OPENAI_BASE_URL` env switch — no provider keys ever sit on the C13
container.

---

## 2. Build the container image

The image is built from the local `ay_extractor/` source tree (per
D-020 R-100-125 §3 vendoring rule — no PyPI dependency on the platform
side).

### Local build (compose dev)

```bash
# From the monorepo root.
ay_platform_core/scripts/e2e_stack.sh build c13
```

Or directly:

```bash
docker compose -f infra/c13_extractor/docker-compose.c13.yml build \
  --build-arg MONOREPO_GIT_SHA=$(git rev-parse --short HEAD)
```

### CI build (GHCR)

The CI pipeline (`.github/workflows/ci-build-images.yml`) tags the
image as `ghcr.io/ayfondation/aywizz-c13-extractor:latest` once the test
gate passes on `main`. The k8s manifests reference this tag.

---

## 3. Environment variables (C13 container)

| Var | Required | Default | Purpose |
|---|---|---|---|
| `OPENAI_BASE_URL` | **yes** | `http://c8:8000/v1` | C8 LiteLLM endpoint. Standalone dev: any OpenAI-API-compatible URL. |
| `OPENAI_API_KEY` | **yes** | (empty) | C8 gateway bearer (`$C8_GATEWAY_API_KEY`). |
| `OUTPUT_WRITER` | yes | `minio` | Only value supported post D-020 v1 strip. |
| `OUTPUT_MINIO_BUCKET` | yes | `c13-extractor-artifacts` | Bucket for the run artifact set. |
| `OUTPUT_MINIO_ENDPOINT` | yes | (empty) | MinIO endpoint URL (e.g. `http://c10-minio:9000`). |
| `MINIO_ACCESS_KEY` | yes | (empty) | Dedicated runtime user per R-100-118. |
| `MINIO_SECRET_KEY` | yes | (empty) | Dedicated runtime user per R-100-118. |
| `MINIO_REGION` | no | `us-east-1` | S3 region (boto3). |
| `EMBEDDING_MODEL` | no | `voyage-3` | Model id resolved by C8 `agent_routes`. |
| `EMBEDDING_BATCH_SIZE` | no | `100` | Per-call batch cap. |
| `MONOREPO_GIT_SHA` | no | `dev` | Build-time stamp in run_manifest.json. |

Empty `OPENAI_API_KEY` lets the openai SDK fall back to env defaults —
acceptable for dev but **prod SHALL set the bearer explicitly** via
the `aywizz-secrets` Secret.

---

## 4. MinIO bucket setup

Two buckets are needed (the n8n workflow references both):

| Bucket | Purpose | Lifecycle |
|---|---|---|
| `sources` | Raw uploaded bytes (PUT by n8n, GET by C13's `/analyze`) | Versioned, no auto-delete. |
| `c13-extractor-artifacts` | Per-run artifact set (R-400-220 v2 layout) | Versioned, retention per ops policy. |

### Bootstrap (one-shot)

```bash
# Local compose dev — MinIO admin auto-creates on first PUT, no setup needed.
# k8s production — provision via mc (MinIO client) in an init container:

mc alias set aywizz http://c10-minio:9000 minioadmin minioadmin
mc mb aywizz/sources
mc mb aywizz/c13-extractor-artifacts
mc anonymous set none aywizz/sources
mc anonymous set none aywizz/c13-extractor-artifacts

# Dedicated runtime user (R-100-118 — never use root creds at runtime).
mc admin user add aywizz c13-runtime $C13_MINIO_SECRET_KEY
mc admin policy attach aywizz readwrite --user c13-runtime
```

The same dedicated user is also used by C7 to read C13's artifacts —
both components share read access to `c13-extractor-artifacts`; C13
also has write access.

---

## 5. k8s overlay activation

The C13 manifests live under `infra/k8s/base/c13_extractor/`. They are
NOT yet referenced from `infra/k8s/base/kustomization.yaml` — operator
opts in per overlay.

### Dev overlay (`infra/k8s/overlays/dev/`)

```yaml
# Add to overlays/dev/kustomization.yaml `resources:` list:
resources:
  # … existing entries …
  - ../../base/c13_extractor
```

### Apply

```bash
infra/k8s/run.sh dev
# Verify the pod is Ready:
kubectl -n aywizz get pods -l app.kubernetes.io/component=c13
# Smoke test the /healthz endpoint via the in-cluster service:
kubectl -n aywizz run curl --rm -it --restart=Never --image=curlimages/curl \
  -- curl -sS http://c13-extractor:8000/healthz
```

The expected response is `{"status": "ok", "version": "<git sha>"}`.

---

## 6. n8n credential setup

The `extract_and_ingest.json` workflow references two credentials:

- **`minio-admin-credentials`** (`httpHeaderAuth`) — the Authorization
  header for PUT/GET against MinIO. For dev compose, the value is the
  default `minioadmin:minioadmin` base64-encoded as
  `Basic bWluaW9hZG1pbjptaW5pb2FkbWlu`. For prod, use the dedicated
  c13-runtime user's bearer (or a presigned URL flow — TBD).

### Bootstrap (n8n UI)

1. Open n8n at `http://localhost:5678` (dev) or via the k8s ingress (prod).
2. Navigate to **Credentials → New → HTTP Header Auth**.
3. Name: `MinIO admin`. Header value: `Basic <base64>`.
4. Import the workflow:
   ```bash
   docker compose exec c12 n8n import:workflow \
     --input=/workflows/extract_and_ingest.json
   ```
5. Activate the workflow in the n8n UI.

---

## 7. Smoke test (end-to-end)

```bash
# Encode a small test file.
TEXT_B64=$(echo "Voyager 1 launched 1977." | base64 | tr -d '\n')

# Trigger the workflow.
curl -sS -X POST http://localhost:5678/webhook/extract-and-ingest \
  -H "Content-Type: application/json" \
  -d "{
    \"tenant_id\": \"tenant-test\",
    \"project_id\": \"project-test\",
    \"source_id\": \"src-smoke-001\",
    \"content_b64\": \"$TEXT_B64\",
    \"filename\": \"smoke.txt\",
    \"mime_type\": \"text/plain\",
    \"format\": \"txt\",
    \"quality_tier\": \"minimal\",
    \"uploaded_by\": \"alice\"
  }"
```

Expected response (after the workflow loops through C13 polling +
C7 ingest):

```json
{
  "accepted": true,
  "run_id": "20260528_1300_xxxxxxxxx",
  "source_id": "src-smoke-001",
  "chunk_count": 1,
  "processing_version": "chunk=512/64;embed=voyage-3",
  "status": "completed"
}
```

Verify the chunks landed in Arango:

```bash
curl -sS "http://localhost:8000/api/v1/memory/projects/project-test/sources/src-smoke-001" \
  -H "X-User-Id: alice" \
  -H "X-Tenant-Id: tenant-test" \
  -H "X-User-Roles: project_editor"
```

---

## 8. Failure modes & troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/analyze` returns 202 but `/status` stays `running` | C13 worker crashed mid-task | Check pod logs; verify `OPENAI_BASE_URL` reachable; pod restart drops in-memory `_runs` but MinIO `status.json` survives (n8n falls back automatically when scope query params are supplied). |
| n8n workflow stuck in poll loop | C13 `status.json` never updated | Check C13 pod logs for unhandled exceptions; `_runs` cache vs MinIO state divergence. |
| `/ingest-chunks` returns 400 | `embedding_dimension` mismatch | Check `chunks[].embedding` length matches `embedding_dimension` header field; the writer may have produced a partial run. |
| `/ingest-chunks` returns 413 | Per-project quota exceeded | Bump `C7_DEFAULT_QUOTA_BYTES` per tenant; verify the project's accumulated `token_count`. |
| `/ingest-chunks` returns 503 | C7 storage adapter unwired | Ensure C7 MinIO config is set (it shares the same MinIO instance as C13). |
| Auto-KG hook silently skipped | `auto_extract_kg_on_upload=False` or LLM/kg_repo unwired | Check C7 config + that C7 is built with the `[kg]` extra. |

---

## 9. Removed surfaces (D-020 session 7)

The following endpoints + workflows were deleted in the final
D-020 session. Operators relying on them must migrate to the new path.

| Removed | Replacement |
|---|---|
| `POST /api/v1/memory/projects/{pid}/sources/upload` | n8n `POST /uploads/extract-and-ingest` |
| `POST /api/v1/memory/projects/{pid}/sources/{sid}/reprocess` | Re-trigger the n8n workflow against the same `source_id` |
| `MemoryService.ingest_uploaded_source` | `MemoryService.ingest_chunks_from_extractor` (called via `/ingest-chunks`) |
| `MemoryService.reprocess_source` | (no direct equivalent — C12 owns reprocess) |
| `infra/c12_workflow/workflows/ingest_text_source.json` | `extract_and_ingest.json` |
| `infra/c12_workflow/workflows/chunk_and_track.json` | `extract_and_ingest.json` |

---

## 10. Open follow-ups (D-020.5)

Tracked for the next iteration, NOT part of the v1 ingestion chain:

- `urgency=background` mode — Anthropic Batch API integration (-50%
  on Phase 2 LLM cost, 1-24h latency). Spec exists (D-020 v2 §C1) ;
  implementation deferred.
- Resume-from-phase (R-400-225) — artifact diff orchestration so a
  failed run can resume Phase 2 without re-extracting Phase 1.
- Workflow polling cap — n8n Code node tracking iteration count.
- Cross-pod NATS broadcast — replace the in-memory `_runs` registry
  with a NATS-distributed cache for true cross-pod failover (today's
  MinIO fallback works for completed runs but a in-flight run on
  pod A can't be polled from pod B).
