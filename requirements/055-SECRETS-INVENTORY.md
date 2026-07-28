---
document: 055-SECRETS-INVENTORY
version: 1
path: requirements/055-SECRETS-INVENTORY.md
language: en
status: draft
audience: any-fresh-session, security-review, operations
---

# Secrets & Credentials Inventory — what to protect and rotate

> **Purpose.** A single recap of every **real secret** the platform
> consumes: its env-var key, where it is declared (Pydantic Settings
> field) and deployed (env file / K8s / proxy config), and what it
> secures. Companion to the **credential classes** in
> [`050-ARCHITECTURE-OVERVIEW.md`](050-ARCHITECTURE-OVERVIEW.md) §"Credential
> classes" (R-100-118 v2) — that table says *who consumes* a class;
> this one enumerates the *individual secrets*.

> **Scope.** Lists secrets only. Non-secret operational config
> (service URLs, non-root usernames, demo-seed values, feature flags)
> is summarised in [Annex A](#annex-a--non-secret-tier-1-config-not-exhaustive)
> but is **not** a security concern. Authoritative on *names + locations*;
> defers to each component's `config.py` for field-level detail.

---

## 1. Real secrets (Tier-2 — `.env.secret`, never committed)

All of the following are sourced into the K8s `Opaque` Secret
**`aywizz-secrets`** (via `secretGenerator` in the dev/prod kustomize
overlays) and injected with `envFrom: [secretRef: aywizz-secrets]`.
In the local stack they live in the git-ignored `.env.secret`
(CLAUDE.md §4.6 **Tier 2** — Claude cannot read/edit these).

### 1.1 At-rest encryption (the keyring that protects every other reversible secret in the DB)

| Key (env) | Declared in | Deployed via | Purpose |
|---|---|---|---|
| `AY_SECRET_MASTER_KEY` | [`crypto/secret_cipher.py:73-99`](../ay_platform_core/src/ay_platform_core/crypto/secret_cipher.py) (`from_env`) | `.env.secret` → `aywizz-secrets` | AES-256-GCM master key (32 raw bytes, url-safe b64). Encrypts **reversible** secrets (LLM provider API keys) at rest in the C8 registry. |
| `AY_SECRET_MASTER_KEYS` | same | same | Rotation keyring `id:b64,id:b64,…` — first = active (encrypt), rest = decrypt-only. Use instead of the single key during rotation. |
| `AY_SECRET_MASTER_KEY_ID` | same | same | Optional key id for the single-key form (default `k1`). Embedded in each token so any key can decrypt. |

### 1.2 Authentication (JWT signing — C2)

| Key (env) | Declared in | Deployed via | Purpose |
|---|---|---|---|
| `C2_JWT_SECRET_KEY` | [`c2_auth/config.py:119-122`](../ay_platform_core/src/ay_platform_core/c2_auth/config.py) | `.env.secret` → `aywizz-secrets` | Symmetric HS256 signing key (≥32 chars). Dev default; **must** be replaced in prod. |
| `C2_JWT_PRIVATE_KEY` | [`c2_auth/config.py:123-126`](../ay_platform_core/src/ay_platform_core/c2_auth/config.py) | same | PEM private key for RS256/EdDSA signing (prod posture; empty ⇒ HS256). |
| `C2_JWT_PUBLIC_KEY` | [`c2_auth/config.py:127-130`](../ay_platform_core/src/ay_platform_core/c2_auth/config.py) | same | PEM public key for RS256/EdDSA verification. |

### 1.3 LLM egress (C8 gateway + provider keys)

| Key (env) | Declared in | Deployed via | Purpose |
|---|---|---|---|
| `C8_GATEWAY_API_KEY` | [`c8_llm/config.py:166`](../ay_platform_core/src/ay_platform_core/c8_llm/config.py) (`gateway_api_key`) | `.env.secret`; proxy `general_settings.master_key` in [`litellm-config.yaml:204`](../infra/c8_gateway/config/litellm-config.yaml) | Single shared Bearer presented by **all** C8 clients **and** enforced by the LiteLLM proxy as its virtual-key `master_key`. Real provider keys never leave the proxy (R-800-012). |
| `ANTHROPIC_API_KEY` | proxy only — **no** Pydantic field | [`litellm-config.yaml:73,104,128`](../infra/c8_gateway/config/litellm-config.yaml) (`api_key: os.environ/ANTHROPIC_API_KEY`); `aywizz-secrets` | Real Anthropic key (`sk-ant-…`). Read **only** by the LiteLLM proxy as the upstream-default. Absent in system-test (mock LLM). |

> **Note — provider keys configured via the HMI.** A per-provider
> upstream key set through the registry console is stored **encrypted**
> (`LLMProviderEntry.api_key_ciphertext`, via §2's cipher) and injected
> per-request by the C8 client, overriding the proxy default. It is a
> *stored* secret in ArangoDB, not an env var — protected by
> `AY_SECRET_MASTER_KEY`, never returned in plaintext (only an
> `api_key_hint` suffix is exposed).

### 1.4 Datastores & object store

| Key (env) | Class | Declared in | Deployed via | Purpose |
|---|---|---|---|---|
| `ARANGO_PASSWORD` | (b) runtime | [`c2_auth/config.py:105-107`](../ay_platform_core/src/ay_platform_core/c2_auth/config.py) (shared `validation_alias`) | `.env.secret` → `aywizz-secrets` | `ay_app` password — every component's runtime DB calls. |
| `ARANGO_ROOT_PASSWORD` | (a) bootstrap | env only (no Pydantic field) | `.env.secret`; `arangodb` image + `arangodb_init` one-shot | Root password — DB/user creation at first boot. **Never** used at runtime. |
| `MINIO_SECRET_KEY` | (b) runtime | [`c4_orchestrator/config.py:57-59`](../ay_platform_core/src/ay_platform_core/c4_orchestrator/config.py) | `.env.secret` → `aywizz-secrets` | `ay_app` secret key — all runtime object-store calls. |
| `MINIO_ROOT_PASSWORD` | (a) bootstrap | env only | `.env.secret`; `minio` image + `minio_init` one-shot | Root password — bucket/user/policy creation at first boot. |

### 1.5 Git (Gitea) & application admin

| Key (env) | Class | Declared in | Deployed via | Purpose |
|---|---|---|---|---|
| `GITEA_ROOT_PASSWORD` | (a)-like | [`c2_auth/config.py:91-95`](../ay_platform_core/src/ay_platform_core/c2_auth/config.py), [`c4_orchestrator/config.py:128-132`](../ay_platform_core/src/ay_platform_core/c4_orchestrator/config.py) | `.env.secret` → `aywizz-secrets` | Gitea root — C2 provisions per-tenant repos, C4 pushes artifacts. Migration to per-deployment tokens tracked by **Q-100-020**. |
| `C2_LOCAL_ADMIN_PASSWORD` | (c) app admin | [`c2_auth/config.py:145-148`](../ay_platform_core/src/ay_platform_core/c2_auth/config.py) | `.env.secret` → `aywizz-secrets` | Bootstrap admin password when `auth_mode=local`. Idempotent. |
| `C2_LOCAL_PLATFORM_MANAGER_PASSWORD` | (c) app admin | [`c2_auth/config.py:162-165`](../ay_platform_core/src/ay_platform_core/c2_auth/config.py) | same | Super-root `platform_manager` password (cross-tenant). Empty ⇒ not bootstrapped. |

### 1.6 Observability (optional)

| Key (env) | Declared in | Deployed via | Purpose |
|---|---|---|---|
| `OBS_ELASTICSEARCH_PASSWORD` | [`observability/workflow/config.py:73-76`](../ay_platform_core/src/ay_platform_core/observability/workflow/config.py) | `.env.secret` (when set) | Basic-Auth password for Elasticsearch egress. Empty ⇒ no auth (a secret only when a secured ES is wired). |

---

## 2. Crypto model — `SecretCipher` (the keyring of §1.1)

[`crypto/secret_cipher.py`](../ay_platform_core/src/ay_platform_core/crypto/secret_cipher.py)
is the **at-rest cipher for reversible secrets** (today: HMI-configured
LLM provider keys). It is deliberately a v1 *local-master-key* design,
built as the seam a KMS/Vault posture would extend.

**Token format** (self-describing, rotation-friendly):
`ay.1.<key_id>.<nonce_b64u>.<ciphertext+tag_b64u>`.

**Applied best practices:**

- **AES-256-GCM** (AEAD) — confidentiality **and** integrity / tamper
  detection; decryption fails loudly (`InvalidTag → SecretCipherError`).
- **Random 96-bit nonce** per encryption (`os.urandom`, never reused).
- **AAD context-binding** — each ciphertext is bound to its context
  string (e.g. `llm_provider:<id>:api_key`), so it cannot be replayed
  into another field/row. (Stable technical ids keep the AAD stable
  across renames.)
- **Rotation-ready keyring** — the `key_id` is embedded in the token;
  add a new active key, keep old keys decrypt-only, re-encrypt lazily,
  retire the old key when nothing references it.
- **Key material env-only, ciphertext-only at rest**; key length
  validated (32 bytes / AES-256).
- **Correct regime separation** — reversible secrets here; **passwords
  are NOT** (one-way **Argon2id** in C2, never decrypted).
- **No plaintext on read paths** — display uses `masked_suffix` (`…a1b2`).

**Known gaps (v1-scoped — deferred, not defects):**

1. **No KMS / no envelope encryption** (per-tenant/per-secret DEKs). The
   master key encrypts everything directly. Explicitly out of v1 scope;
   the cipher is the extension seam (swap the keyring source, keep the
   token format).
2. **Master key delivered as an env var** (`os.environ`). Hardening
   opportunity: mount the K8s Secret as a **file** (or fetch from Vault
   at runtime) to shrink the leak surface (`/proc/<pid>/environ`,
   child-process inheritance, crash dumps). ← cheapest improvement.
3. **No automated rotation / re-encryption job** — the format supports
   rotation but re-encryption is manual; no runbook yet.
4. **Random-nonce GCM birthday bound** (~2³² encryptions/key) — a
   non-issue at the current volume (a handful of keys); XChaCha20-Poly1305
   (192-bit nonce) would add headroom at scale.
5. **No decrypt-access audit log** — a potential gap for regulated
   contexts (ISO 21434).

---

## 3. Where secrets live (tiers & deployment)

| Tier | Files | Claude access | Contents |
|---|---|---|---|
| **1 — versioned, non-secret** | `.env.example`, `ay_platform_core/tests/.env.test` | read/edit (diff review) | placeholders, test literals, dev defaults (Annex A) |
| **2 — sensitive** | `.env`, `.env.prod`, `.env.local`, `.env.secret` | **denied** (CLAUDE.md §4.6) | every secret in §1 |

- **Kubernetes:** dev/prod overlays' `secretGenerator` build the
  `aywizz-secrets` Opaque Secret from `.env.secret`; every Deployment
  mounts it via `envFrom`. The **system-test** overlay uses deterministic
  test literals and intentionally omits real provider keys (mock LLM).
- **Local stack:** `ay_platform_core/scripts/e2e_stack.sh` passes
  `.env.test` to compose and **optionally** `.env.secret` (dev profile)
  so C13/the gateway can reach a real provider.
- **Single env file per environment** (R-100-110 v2): each variable
  appears exactly once; an env-coherence test guards against drift.

---

## Annex A — non-secret (Tier-1) config (not exhaustive)

These are committed dev values, **not** secrets, listed so a reader
doesn't mistake them for credentials to protect:

- **Service wiring (URLs/endpoints):** `C8_GATEWAY_URL`, `ARANGO_URL`,
  `MINIO_ENDPOINT`, `OLLAMA_URL`, `C2_GITEA_BASE_URL`,
  `C4_GITEA_BASE_URL`, `C7_C12_WEBHOOK_URL`, `C3_C7_BASE_URL`, …
- **Non-root usernames / fixed names:** `ARANGO_USERNAME=ay_app`,
  `MINIO_ACCESS_KEY=ay_app`, `ARANGO_ROOT_USERNAME=root`,
  `MINIO_ROOT_USER=minioadmin`, `ARANGO_DB=platform`,
  `GITEA_ROOT_USERNAME=aywizz`.
- **Demo seed (well-known by design):** all `C2_DEMO_SEED_*`
  usernames/passwords (`superroot/dev-superroot`,
  `tenant-admin/dev-tenant`, `project-editor/dev-editor`,
  `project-viewer/dev-viewer`). Exposed on `/ux/config` **only** when
  `C2_UX_DEV_MODE_ENABLED=true`; both that flag and
  `C2_DEMO_SEED_ENABLED` **must** be `false` in production.

> A username such as `ARANGO_ROOT_USERNAME` is not itself a secret;
> its paired `*_PASSWORD` (in §1) is. Protect the passwords/keys, not
> the usernames/URLs.

---

*Related:* credential classes & bootstrap responsibility —
[`050-ARCHITECTURE-OVERVIEW.md`](050-ARCHITECTURE-OVERVIEW.md) §"Credential
classes"; env-file discipline — CLAUDE.md §4.6; LLM egress posture —
[`800-SPEC-LLM-ABSTRACTION.md`](800-SPEC-LLM-ABSTRACTION.md).
