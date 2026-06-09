<!-- =============================================================================
File: 2026-06-04-rag-followups-and-llm-governance-plan.md
Version: 1
Path: .claude/sessions/2026-06-04-rag-followups-and-llm-governance-plan.md
Description: Continuation of the 2026-06-03 RAG/cost session. Shipped three
             chunk-transparency follow-ups (A1 image-into-text, B run-stats
             observability + B-ii authoritative $ cost via C8, C document
             summary on standard), then DESIGNED a multi-tenant LLM-governance
             feature (registry + tenant catalog + model_quality + HMI-editable
             encrypted API keys) and shipped its first brick — the application
             SecretCipher (AES-256-GCM, env master key). R-400-226 / R-800-070+.
============================================================================= -->

# Session — RAG follow-ups (A1/B/C) + LLM-governance design & crypto brick (2026-06-04)

## Context
Direct continuation of `2026-06-03-rag-quality-and-cost-optimization.md`. The
operator probed the chunk-detail UI and surfaced three gaps, then opened a new
track: tenant-level LLM governance + secure key storage.

## 1. Chunk-transparency follow-ups (A1 / B / C) — shipped

- **A1 — image captions folded INTO the text** : the PDF/DOCX extractors emit
  no positional `IMAGE_CONTENT` anchor, so captions used to live only in the
  images artifact and never reached the summaries. The facade was REORDERED —
  image captioning now runs BEFORE chunking and the captions are appended as a
  `## Figures` section to `enriched_text` (`_augment_text_with_images`). The
  chunks, the map-reduce summary AND the enriched-text artifact now incorporate
  the image-extracted content. (A2 — exact per-position anchoring via extractor
  placeholders — deferred.)
- **B — per-phase observability** : `state.record_phase(...)` + `00_metadata/
  run_stats.json` (duration + LLM calls + tokens per phase: extraction /
  image_caption / chunking / summarize / decontextualize / densify / embedding
  + global totals + images_captioned). Rendered in the UI source detail
  (per-phase table). The $ cost is sourced from C8 (note), not duplicated.
- **B-ii — authoritative $ cost via C8** : C13 now sends `X-Run-Id` /
  `X-Source-Id` / `X-Tenant-Id` / `X-Project-Id` / `X-Agent-Name` HEADERS on
  every LLM call (`OpenAIAdapter(default_headers=…)` ← `LLMFactory(headers=…)` ←
  facade `_llm_headers`). The mounted cost-forwarder already captured request
  headers, so `CallTags` gained `source_id`/`run_id` (`_extract_tags` picks
  them) and each `llm_calls` row is now attributed. C7
  `repository.source_enrichment_cost` SUMs `llm_calls.cost_usd` by
  `tags.source_id` → `SourceDiagnostics.enrichment_cost_usd` → shown as
  "Enrichment cost (C8)". Snapshot-priced at record time (the price is frozen
  when the call is logged).
- **C — document summary on `standard`** : `global_summary` per chunk =
  `dense_summary` (high) ELSE `refine_summary` (the map-reduce REDUCE output) —
  so the document-level summary surfaces even without densification.

## 2. LLM governance — DESIGN + locked decisions (code-first, spec-after)
The operator wants to stop hardcoding token prices and govern LLMs per tenant.
Agreed 3-layer model: **platform registry** (models + encrypted API keys +
provider cost + capabilities + default model_quality) → **tenant catalog**
(curated subset + optional chargeback rate-card, NO keys) → **project** (picks
`model_quality` low/med/high → resolved to a model).

**Locked decisions :**
- LLM list managed at the APPLICATION level (NO per-tenant BYO-key) → API keys
  are PLATFORM secrets, managed by a platform admin (`tenant_manager`).
- `model_quality` is GLOBAL per project (not per role).
- NO KMS — Master Key via ENV var (Vault-backable via K8s), ciphertext in DB
  (single application key in v1 ; per-tenant DEK is a future extension).
- Option B — API keys editable via HMI, encrypted in DB, **C8 injects the
  decrypted key per call** (pending the litellm spike, see §4).
- v1 encrypted-secret scope = LLM API keys ONLY (SSO/Gitea later).
- Passwords stay Argon2id one-way in C2 (never encrypted) ; a future PEPPER may
  reuse the Master Key. SSO-first recommended for password-averse tenants.
- NAMING : new `model_quality` (low/med/high) is DISTINCT from the existing
  `EnrichmentConfig.quality_tier` (minimal/standard/high = enrichment DEPTH).

## 3. SecretCipher — first brick SHIPPED
`ay_platform_core/crypto/secret_cipher.py` (+ `crypto/__init__.py`) :
**AES-256-GCM** over an ENV-provided keyring (`SecretCipher.from_env()` reads
`AY_SECRET_MASTER_KEY[S]`), `encrypt/decrypt(plaintext, *, aad)` with the AAD
binding a ciphertext to its context (`llm_registry:<alias>:api_key`),
self-describing rotation-friendly token `ay.1.<key_id>.<nonce>.<ct>`,
`masked_suffix()` for write-only HMI display. Uses `cryptography` 44.x (already
present, no new dep). **13 unit tests** (round-trip / AAD / tamper / rotation /
env-parse / masking) — ruff + mypy clean. This is the `local-master-key` impl ;
the token format is the seam a KMS / per-tenant DEK would later extend.

## 4. Remaining (handoff brief written, ready to paste into a fresh session)
Ordered increments : **(1) litellm per-call-key SPIKE** (gating — verify the
proxy supports a per-request upstream-key override ; decides B vs B′ fallback) →
(2) platform registry collection + admin endpoints + seed from
`litellm-config.yaml` → (3) key injection → (4) tenant catalog + `(tenant,
model_quality)→model` resolution (capability gating, fallback) → (5)
`EnrichmentConfig.model_quality` (replaces `image_analyzer_model`) → (6) HMI 3
surfaces + RBAC + auth-matrix (§13) → (7) cost rate-card in the receiver → (8)
spec (`800-SPEC` + `999-SYNTHESIS`). Operator must set `AY_SECRET_MASTER_KEY` in
`.env.secret` (Tier-2).

## Verification
- Backend `run_tests.sh ci` : **All stages OK** (ruff + mypy + **1748 tests**).
- C13 : **551** unit+integration green. UI `npm run ci` : **373** + coverage 4/4.
- Crypto : **13** green.

## Decisions (beyond specs)
- Image captions folded into the text before chunking (A1) ; A2 positional
  anchoring deferred.
- Per-run observability artifact `run_stats.json` (timing + tokens) ; $ cost
  sourced from C8, not recomputed in C13.
- LLM governance : app-level registry, `model_quality` global, env master key
  (no KMS), HMI-editable keys with per-call injection (option B), code-first.

## Follow-ups
- The litellm per-call upstream-key override is UNVERIFIED — first task.
- Restart the api-tier (c7 + c8-cost-receiver) for the B-ii code to go live ;
  re-upload a source (standard/high) to see durations/tokens AND $ (old runs
  predate the run_id tagging → no retroactive cost).

## References
- Prior : `2026-06-03-rag-quality-and-cost-optimization.md`.
- CLAUDE.md §4.6 (env tiers — master key in `.env.secret`), §13 (auth-matrix),
  §5.7 (shell), §8.1/§9 (this entry). R-100-034 (Argon2id, C2).
