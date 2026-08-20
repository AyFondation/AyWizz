<!-- =============================================================================
Version: 1
Path: infra/k8s/README.md
Description: From-scratch install guide for deploying the AyWizz platform on a
             Kubernetes cluster. Essential, didactic — the happy path only.
============================================================================= -->

# Deploy AyWizz on Kubernetes — from scratch

This is the shortest path from a clean cluster to a running platform.

---

## 0. Prerequisites

- A Kubernetes cluster + `kubectl` pointing at it
  (`kubectl config current-context`). Local dev = Docker Desktop with
  Kubernetes enabled.
- Docker, to build the two platform images.
- This repository, cloned.

Everything runs from the **monorepo root**.

---

## 1. Create the two environment files

The dev overlay reads two files (both git-ignored — you create them from the
committed templates):

```bash
cp infra/k8s/overlays/dev/.env.example         infra/k8s/overlays/dev/.env
cp infra/k8s/overlays/dev/.env.secret.example  infra/k8s/overlays/dev/.env.secret
```

- **`.env`** — non-secret config (service URLs, DB/bucket names). The defaults
  work as-is for local dev.
- **`.env.secret`** — the secrets. Fill them in (next step).

These become the `aywizz-config` ConfigMap and the `aywizz-secrets` Secret,
injected into every pod.

---

## 2. Fill in `.env.secret`

| Key | What it's for | Required |
|---|---|---|
| `ARANGO_ROOT_PASSWORD` | ArangoDB bootstrap (init Job + DB) | yes |
| `MINIO_ROOT_PASSWORD` | MinIO bootstrap (init Job + store) | yes |
| `ARANGO_PASSWORD` | app → ArangoDB at runtime | yes |
| `MINIO_SECRET_KEY` | app → MinIO at runtime | yes |
| `C2_JWT_SECRET_KEY` | signs the auth JWTs | yes |
| `C2_LOCAL_ADMIN_PASSWORD` | password of the seeded admin user | yes |
| `C2_LOCAL_PLATFORM_MANAGER_PASSWORD` | password of the seeded super-root (set its username in `.env` too) | recommended |
| `C8_GATEWAY_API_KEY` | shared gateway credential (clients + LiteLLM master key) | yes (any strong value) |
| `AY_SECRET_MASTER_KEY` | **encrypts the LLM provider keys** stored in the in-app registry | yes |

> **No provider key here.** The platform is provider-independent: you register
> a provider (any: Anthropic, OpenAI, a local endpoint…) and its API key
> **in the app** later (step 6), where it is stored encrypted.

Keep this file out of git. Use strong values in production.

---

## 3. Build the platform images

```bash
infra/scripts/k8s_build_images.sh
```

Produces `aywizz-api` and `aywizz-ui` in the Docker store the cluster shares.

---

## 4. Deploy

**First install** on a fresh cluster:

```bash
infra/k8s/run.sh dev --crds --wait
```

- `--crds` installs the Traefik CRDs — **once per cluster** (omit it afterwards).
- `--wait` blocks until every Deployment is ready.

The apply also runs one-shot **bootstrap Jobs** (ArangoDB, MinIO, workflow seed,
Ollama) and seeds the demo tenant + users on first C2 startup.

---

## 5. Access & verify

```bash
kubectl get pods -n aywizz      # ~17 pods, all Running / Completed
```

Open the UI at **http://localhost:56000** (local Docker Desktop) and sign in
with the admin / platform-manager credentials from step 2.

---

## 6. Register an LLM provider (required before any LLM call)

The platform ships with **no provider** — LLM calls fail until you configure
one. In the UI, under the LLM admin (`/admin/llm-*`):

1. **Create a provider**: a name, its `base_url` (API endpoint), and its API
   key (stored encrypted with `AY_SECRET_MASTER_KEY`).
2. **Create a model**: the upstream model name + its per-token costs, attached
   to the provider.
3. **Map the routing tiers** `flagship` / `balanced` / `fast` to your
   registered model(s).

Any provider works — there is no lock-in to a specific vendor.

---

## 7. Redeploy after a change

Rebuild the images (step 3), then:

```bash
infra/k8s/run.sh dev --restart --wait
```

No `--crds` this time. Use `--reinit` as well if you changed a bootstrap Job.

---

## Notes

- **Production** uses `infra/k8s/overlays/prod/` (SHA-pinned images, its own
  `.env` / `.env.secret`, TLS/ingress as separate operator decisions). Same
  flow, different overlay.
- **Validate a build without applying**: `infra/scripts/k8s_validate.sh dev`.
