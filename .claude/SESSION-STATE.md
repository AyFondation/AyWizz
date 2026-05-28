<!-- =============================================================================
File: SESSION-STATE.md
Version: 63
Path: .claude/SESSION-STATE.md
Description: Current project state. Single source of truth for "where are we".
             Updated in place at the end of each significant session.
             Read by Claude Code at session start to restore context.

Discipline: this file SHALL NOT exceed 150 lines.
            When approaching the limit, archive the outdated portions into
            a new .claude/sessions/YYYY-MM-DD-<slug>.md entry and trim here.

Autonomous write policy: per CLAUDE.md §9.1, Claude MAY write this
            file autonomously only for trivial deltas (date bump,
            §6 archive append, cosmetic fixes). All other changes
            require explicit user validation of the diff.
============================================================================= -->

# Project State — ay_monorepo

**Last updated:** 2026-05-28 (**D-020 sessions 1→7 ALL DONE — chantier clos.** Session 7 (cleanup + régression) : suppression physique `c7_memory.service.ingest_uploaded_source` + `reprocess_source` + routes `POST /sources/upload` + `POST /sources/{sid}/reprocess` + 2 EndpointSpec auth_matrix (`065-TEST-MATRIX.md` 103→**101 endpoints**). Auto-KG hook préservé en migration sur `ingest_chunks_from_extractor` pour conserver R-400-200 KG-on-ingest. Suppression physique des 2 workflows legacy (`ingest_text_source.json` + `chunk_and_track.json` — configmap k8s 3→1 workflow). Helper conftest `_ingest_text_via_chunks` (chunker whitespace inline + ChunkRich builder) qui mutualise la fixture pour les 4 tests int rewrités (`auto_kg_extraction` `_upload_text` via `/ingest-chunks` POST, `artifact_rebuild` via helper, `structural_extraction` + `blob_download` via storage direct PUT — le pattern n8n). Suppression des 2 tests legacy (`test_upload_pipeline.py` + `test_processing_version.py`). Nouveau `infra/c13_extractor/docs/deployment.md` (build/env/k8s/MinIO/n8n/smoke/troubleshooting). **`run_tests.sh d020-s7 tests/unit/c7_memory --no-cov` → All stages OK** (ruff + mypy + pytest verts). pyproject audit : aucune dep à drop (session 2 strip avait déjà rangé). **C13 D-020 complet et déployable end-to-end** : upload → n8n `extract_and_ingest` → C13 `/analyze` → MinIO artifacts → C7 `/ingest-chunks` → Arango.**).

---

## 1. Current stage

**Étape 1 — Backbone components: DONE.** (C1..C9, C12 shipped + deployable via `e2e_stack.sh dev`.)

**Étape 2a — UX chat polish: DONE.** (Full record in `sessions/2026-05-12-ux-chat-finalisation.md`.)

**Étape 2b — Project artifacts + DocGen : IN PROGRESS (near V1 close).** Pass 1/2.1/2.2 + Generate-E2E + Phase 2.C chat-direct DocGen + Increment 3 (tab-nav state) = DONE. **DocGen versioning + UI tranche = DONE** (2026-05-21) : live-docs per-AI-response version (turn-tagged Gitea commits → `vN`), version-history viewer (`?ref=<sha>` + per-file `git/commits?path`), drag-and-drop tree relocation, expandable chain-of-thought tool detail, versioned "open in working area" links below the response, full-width mouse-resizable 3-pane working area persisted in prefs. Remaining for V1 close — see §5.

---

## 2. Components status

| Component | Status | Notes |
|---|---|---|
| C1 Gateway | **done** | Traefik v3, `routers.yml`. |
| C2 Auth | **done** | prefs (trigram/user_prompt/user_color) + project system_prompt. |
| C3 Conversation | **done** | RAG + chat-direct DocGen tool-loop. SSE unified `event: inline` (now carries per-tool `arguments` + resulting `version`); per-response `X-Turn-Id` for version batching; persisted `MessagePublic.events`. |
| C4 Orchestrator | **done / artifacts in progress** | Run state machine + code plugin + document CRUD (`live-docs` run). Per-file `ArtifactNode.version` + `read_document_at_ref` + `git/commits?path` (R-200-147 history). |
| C5 Requirements | **done (v1.5)** | CRUD + tailoring + history + reindex + export. |
| C6 Validation | **done (v1.5)** | 9 checks (8 real, #3 stub). Graded verdicts T1 + opt-in T3 judge (R-700-032) + quality-regression (R-700-033). |
| C7 Memory | **done** | Federated retrieval, Ollama embedder, hybrid KG. |
| C8 LLM Gateway | **done** | Client + **LiteLLM proxy deployable** (compose + K8s base `c8_gateway`) + client-side per-agent routing (Claude by quality/cost) + cost receiver (`c8_llm/main.py`, COMPONENT_MODULE=c8_llm) → Arango `llm_calls`. Single key `C8_GATEWAY_API_KEY`. |
| C9 MCP | **done** | 8 tools. |
| C12 Workflow | **deployed** | n8n via Traefik. |
| **UX (Next.js)** | **done (chat+DocGen)** | Profiles code/docgen. Working area 3-pane + Documents tree. `<InlineLog>` unified renderer. |

---

## 3. Active decisions (beyond specs)

- **Architecture** : Python 3.13 src layout. Monorepo. B1 (single `ay-api:local` × N via `COMPONENT_MODULE`). Single Arango `platform`. LiteLLM=C8 HTTP-only (R-800-011).
- **Governance** : CLAUDE.md v16 + `.claude/settings.json` v14. §10 test-debug / §11 coverage / §5.7 shell / §4.6 env-tiers / §5.2 sed-ban.
- **Validation philosophy** (2026-05-19, §5.3 v16) : human-in-the-loop applies at **decision gates** (architecture, plans, todos, semantic env changes per §4.6, new specs, contract changes), NOT at **execution gates** for read-only / test / lint / build / analysis commands. The `settings.json` allow-list scope reflects this distinction and expands over time as new lecture-only / test-only commands prove safe and frequent. Composed shell (`&&`, `2>&1 | tail`, heredoc-with-write) remains §5.7-banned regardless of whether the underlying tools are allowlisted — the matcher sees the chain as one unit.
- **Catalog-driven CI** : `tests/e2e/auth_matrix/_catalog.py` SOT ; coherence pins route↔catalog↔coverage.
- **UX architecture** : runtime-config 2 tiers ; `(protected)/` auth gate ; build-stamp footer ; session-revoked redirect.
- **Artifacts UX** (2026-05-12) : transparent backend (MinIO/Gitea proxied) ; profile-aware section ; MinIO `orchestrator/c4-artifacts/{tenant}/{project}/{run}/{path}`.
- **D-015 DocGen v1 = chat-direct** (2026-05-16) : `create/update/read/list/delete_document` tools mutate the artifact surface from C3 chat. v2 = synthesis-v4/OpenHands (future). ADR in `999-SYNTHESIS.md`.
- **Live-docs versioning = batched per AI response** (2026-05-21, R-200-147) : C3 mints one `response_turn_id` per turn → forwarded as `X-Turn-Id` → C4 embeds `[turn:<id>]` in the Gitea commit message. `ArtifactNode.version` = count of DISTINCT turn ids in a file's history (N writes in one response = one bump). History viewer reads content at a SHA via Gitea `contents?ref=` (`read_document_at_ref`) ; MinIO keeps only latest. Stateless (derived from Gitea, no new store). Inline log is pure chain-of-thought ; modified-doc deep-links moved below the response with `(vN)`.
- **Unified inline-event pipeline** (2026-05-19) : `StageRecord`+`ToolCallRecord` → one `InlineEvent` (discriminated `kind`) ; one persisted `MessagePublic.events` audit ledger (read-time shim projects legacy v3 `stages`, no data migration) ; one SSE channel `event: inline` ; one `<InlineLog>` formatter-registry (add a kind = add a formatter). Registered-contract change (§8.4) ; tests adapted (§10.4).
- **C8 LiteLLM proxy + per-agent routing + cost tracking DONE** (2026-05-22, V2 #1, R-800-001/070) : off-the-shelf LiteLLM, client-side `agent_routes` → Claude tiers. Single `C8_GATEWAY_API_KEY`. Cost forwarder → Arango `llm_calls`.
- **V2 #2 OpenHands `generate` POC adapter** (2026-05-22) : gated `pipeline/generate_engine.py::OpenHandsGenerateEngine` (opt-in `C4_GENERATE_ENGINE=openhands`, default `in_process` UNCHANGED), OpenHands V1 SDK via C8/LiteLLM. Gate B blocks by design (Q1 unresolved). R-200-029..038 PROPOSED, no `@relation` claimed.
- **V2 #3 = Graphiti-STYLE memory, ArangoDB-native** (2026-05-22) : Implements D-016 pattern natively in Arango (NOT the lib — would break D-002). Sequence A→B→C. B (L2/L3, R-400-205/206) D-010-gated ; C (bi-temporal) was §8.1 spec gap → D-019. KG records carry `layer`/`ontology_version` so B/C additive.
- **V2 #3 Block A — D-016 v1 subset DONE** (2026-05-22→23, R-400-200..203, E-400-006) : closed ontology + L1 structural extractor + hybrid retrieval (BM25+dense+RRF) + chunk contextualisation. Journals 2026-05-22/23.
- **Eval harness + C bi-temporal + D-017 C6 slice DONE** (2026-05-23, R-400-209 / R-700-032 / R-700-033) : tests/eval/ recall@3=1.0 on golden → D-010 closed (B not justified). Bi-temporal KG (valid + transaction time, append-only, as-of queries). T3 LLM-judge opt-in + quality-regression anti-feedback. #3 `interface-signature-drift` deferred.
- **Dev stack = real LLM ; CI = mock/Ollama** (2026-05-26) : `e2e_stack.sh dev` (manual user tests) now brings up the LiteLLM proxy (`--profile litellm`, v7) → C8→Claude production reality ; `up`/`full`/pytest (CI) stay mock_llm + Ollama (zero provider cost). Two runtime defects fixed (uncaught by CI — container-only) : (1) C7 loaded the tree-sitter parser AT IMPORT → the `--no-create-home` container user had no writable grammar cache → boot crash cascading to C3 ; now LAZY (`functools.cache`, `code_extractor.py` v2). (2) litellm crashed on first real boot — it resolves a `callbacks` entry as a FILE next to the config, not via PYTHONPATH → now bind-mount `/app/cost_forwarder.py` (override v11). Runtime sequence diagram `051-RUNTIME-execution-flow.svg` added (RAG chat turn + DocGen tool-loop, colour = acting component) ; `050-WORKFLOW` blueprint now partially stale (A.b/A.c/eval done). End-to-end chat still depends on `.env.secret` keys being valid.
- **Platform LIVE on local Kubernetes DONE** (2026-05-27, docker-desktop) : first k8s bring-up, 8 latent manifest bugs fixed (namespace conflict, c4-orchestrator SA location, minio root user, litellm OOM+callback mount, Traefik --ping+nodes RBAC, IngressRoute drift). LoadBalancer-on-Traefik dev overlay → `localhost:56000`. New tooling `e2e_stack.sh build` (v8) + `run.sh --crds` (v2). 15 pods Ready, 19 routers. Journal `2026-05-27-k8s-local-deployment.md`.
- **Documents file-manager feature DONE** (2026-05-28) : R-500-010 v2 — shared `LiveDocsManager` (~330 LOC) wired into BOTH Documents tab + Working area (3 gaps : toolbar `+ New file/folder`, empty-state, inline Edit/Save). apiClient `getDocumentText`/`createDocument`/`updateDocument` + 5 unit + 3 integration tests. 131/131 vitest green ; lint + typecheck verts.
- **D-020 AyExtractor adopted as dependency component C13** (2026-05-28, spec-only session 1/7) : prior internal work `ay_extractor/` becomes the platform's source extraction + chunking engine, deployed as new dependency component **C13** (vendored monorepo, local wheel build, HTTP-only surface `POST /analyze` + `GET /status/{run_id}` + `GET /healthz`, zero `import` coupling with `ay_platform_core/`, LLM routing via C8 by env switch `OPENAI_BASE_URL`). **Re-partitions D-013** : R-100-081 v1→v2 — C12 (n8n) owns trigger + orchestration, **C13 owns extract+chunk+write MinIO artifacts** (markdown + jsonl + `run_manifest.json` stamped with `ayextractor_version` + per-agent `llm_assignments` + `prompt_hashes` for byte-exact reproducibility + run diff), C7 owns embed + index only. **v1 scope** = Phase 1+2 only ; Phase 3 (concepts/triplets/Leiden/profiles/synthesis) deferred to Q-200-022. **LLM-frugal** : `quality_tier ∈ {minimal | standard | high}` per project (default `minimal` = zero LLM cost when no images), image_analyzer mandatory (only when images), decontextualizer/Refine/Chain of Density opt-in. **Strip mandatory** : 10+ AyExtractor modules physically removed (`rag/{retriever,vector_store,graph_store,enricher,indexer,embeddings}`, `consolidator/`, `batch/`, `graph/`, Phase 3 agents, alternative cache backends, alternative LLM adapters, local/s3 writers). New endpoint C7 `POST /memory/projects/{pid}/sources/{sid}/ingest-chunks` accepts ChunkRich (text + decontextualised? + context_summary? + global_summary? + section_path + char offsets + references + tokens + extraction_run_id). Implementation: 6 sessions (strip → adapters → HTTP wrapper → C7 endpoint → n8n workflow → cleanup). 339 requirements indexed by `060-IMPLEMENTATION-STATUS.md`.
- **Increment 3a/3b — cross-nav UI store + provider SSE loop** (2026-05-19, DONE) : `WorkspaceProvider` per-project Tier-1 store (per-conv composer drafts, sessionStorage v2 normalised). Provider-owned SSE runtime (`useConvRuntime` via `useSyncExternalStore`) so a stream re-renders ONLY the active chat ; live generation survives unmount. Wired on ChatSidebar + Conversations `[cid]`. Full detail in `sessions/2026-05-19-increment-3-...`.

---

## 4. Open questions

- **600-SPEC** scaffold (code-domain quality engine).
- **C5** : import 501, ReqIF/point-in-time v2. **C7** ML adapters optional extras. **C6** : #3 `interface-signature-drift` stub deferred (load-bearing probe) ; T3 judge D-011 cross-family = open gap (needs a 2nd provider family).
- **Q-100-016/017/018** : trace into K8s Jobs ; workflow-synthesis sampling/retention ; dashboard layer.
- **Q-100-019** : Turbopack incompat → `next dev --webpack`.
- **Q-100-020** : Gitea service-account credential storage → KMS/vault at prod.
- **Q-100-021 (RESOLVED 2026-05-22)** : per-agent C8 routing — DONE via the deployed LiteLLM proxy + client-side `agent_routes` (Claude by quality/cost). See §3.
- **Q-100-022** : per-agent C8 routing is **STATIC** — each pipeline role maps to a fixed Claude tier (`agent_routes`, by quality/cost a priori : architect→Opus, planner/implementer/reviewers→Sonnet, sub-agent→Haiku). **Dynamic, per-task** model selection (the orchestrator picks the tier from the task's analysed difficulty/need at runtime) is NOT implemented. The hook exists (`payload.model` wins over the route in `_resolve_model` ; `AgentOverride` is open-schema). Improvement, not a defect — design it when role-routing proves insufficient.
- **Q-100-023** : `overlays/prod` still references `../../base` wholesale → the same `c4-workers` Namespace conflict latent there. Proper fix before prod : move `c4_workers` out of base into a standalone opt-in layer applied separately when `C4_DISPATCHER=k8s`. Cost : small refactor (~3 files).
- **Q-200-022** : Phase 3 AyExtractor agents (concept_extractor / community_summarizer / profile_generator / synthesizer / critic) adoption timing + placement (kept inside C13 or merged into C7's existing `kg/` per D-016). Out of v1 scope ; gated by production demand + ROI evidence.
- **Q-200-023** : AyExtractor cache fingerprint backend — port the JSON cache store to MinIO (current AyExtractor design) vs. promote it to an ArangoDB collection alongside `memory_chunks` ? File-based is simpler ; ArangoDB integrates better with the platform's lifecycle. v1 defers cache (rely on R-100-085 sha256 file-level dedup).
- **Q-200-024** : AyExtractor versioning model — keep vendored monorepo + local wheel build (D-020 default), OR split to a separate Git repo with pip-install-from-URL, OR publish to PyPI. v1 = vendored ; v2 reconsider if AyExtractor evolves on a different cadence than the platform.
- **Q-200-025** : C13 integration test strategy — testcontainers (real MinIO + C8 mock for LLM calls) + e2e on the full C12→C13→C7 chain, OR unit-only with fake adapters ? Decided session 3 once the strip + adapter work settles.

---

## 5. Next planned action

Doing the three V2 features in order (operator-confirmed) : **#1 LiteLLM ✓ → #2 OpenHands ✓ → #3 Graphiti-style memory (in progress)**.

**V2 #2 (OpenHands `generate` POC adapter) = DONE** (2026-05-22, see §3). Operator runs the actual Q13 POC (cluster + provider budget, out of sandbox) ; the §8.1 spec amendment (R-200-029..038 into 200-SPEC) follows POC success.

**V2 #3 (Graphiti-style memory, ArangoDB-native)** — Block A (D-016 v1 subset) ✓ + eval harness (D-017 slice) ✓ + C bi-temporal (D-019) ✓ (2026-05-23, CI 1630 green ; see §3). Clean pause point.

Remaining V2 #3 (all gated / queued — not "just go") :
- **B (L2/L3 + iterative traversal, R-400-205/206)** — **D-010-gated** ; the eval evidence says v1 retrieval is sufficient on the golden set, so B is **NOT justified**. Revisit only with a production-scale eval showing a gap.
- **Golden-set expansion** — to probe D-010 for real (production-scale corpus + distractors), if/when wanted.
- **Full D-017** — C6 slice DONE (T1 + T2 #8 + T3 judge R-700-032 + regression R-700-033) ; #3 de-stub + T3 D-011 cross-family enablement (2nd provider) remain ; 600/800 eval slices still spec-queued.
- **C follow-ons** — bi-temporal for the open-domain extractor + chunk valid-time.

**Immediate next** : **D-020 closed — pas de session immédiate planifiée.** Pour reprendre, candidats opérationnels : (a) **D-020.5 batch API** `urgency=background` (Anthropic Batch API → -50% Phase 2 LLM, latence 1-24h) — spec déjà écrite (D-020 v2 §C1), implémentation différée. (b) **D-020.5 resume-from-phase** R-400-225 — artifact diff orchestration pour reprendre Phase 2 sans re-extract Phase 1. (c) **Activation de l'overlay k8s C13 dans `overlays/dev`** (le manifest base existe mais pas encore référencé) + smoke test in-cluster du workflow `extract_and_ingest` complet. (d) **Q-100-023** : refactor `c4_workers` hors de `base/` pour débloquer `overlays/prod`. (e) **Phase 3 KG de C13** (Q-200-022) — concepts/triplets/Leiden communities/profiles — quand un ROI concret le justifie.

**V1 remainder still open (parallel track)** : C6 stub #3 ; prod HTTPS + **K8s prod overlay (Q-100-023 : refactor c4_workers out of base)** + K8sDispatcher wired + CI GitHub Actions. (K8s LOCAL DEV done 2026-05-27 ; LiteLLM deploy done.)

**Reserve (intellectual honesty)** : the Anthropic API is **paid** — every routed turn bills tokens (Haiku keeps it cheap). `.env.secret` is git-ignored. Ollama remains the offline/free fallback (revert `C8_GATEWAY_URL`).

---

## 6. Sessions archive

Latest entries (most recent first):
- `.claude/sessions/2026-05-28-d020-session7-cleanup.md` — **D-020 session 7/7 FINAL** : suppression physique `ingest_uploaded_source` + `reprocess_source` + routes upload/reprocess + EndpointSpec auth_matrix (103→101 endpoints) + 2 workflows legacy + 2 tests legacy. Auto-KG hook migré vers `ingest_chunks_from_extractor`. 4 tests int rewires via helper conftest. Doc ops `infra/c13_extractor/docs/deployment.md`. **All stages OK** (ruff + mypy + pytest). **D-020 chantier complet — chaîne C12→C13→C7 opérationnelle end-to-end.**
- `.claude/sessions/2026-05-28-d020-session6-n8n-workflow.md` — **D-020 session 6/7** : nouveau workflow n8n `extract_and_ingest.json` v1 (13 nodes — webhook → MinIO PUT raw → C13 /analyze → poll /status loop → MinIO GET chunks + manifest → C7 /ingest-chunks). 2 legacy workflows désactivés (`active: false` + DEPRECATED _comment). C13 `/status/{run_id}` MinIO-backed fallback via query params optionnels. ConfigMap k8s régénéré (3 workflows). Next = session 7 cleanup final.
- `.claude/sessions/2026-05-28-d020-session5-c7-ingest-chunks.md` — **D-020 session 5/7** : C7 nouveau endpoint `/ingest-chunks` (R-400-223 v2 pure-INSERT), modèles ChunkRich + ChunkIngestRequest, méthode service `ingest_chunks_from_extractor`. `upload_source` marqué deprecated. Auth matrix mise à jour + 065-TEST-MATRIX régénéré (103 endpoints). Run_id mismatch HTTP↔facade résolu via `Metadata.run_id`. 2 tests skip-marked rewrités (settings v4 + pipeline v5). Nouveau `test_ingest_chunks.py` (5 tests verts via run_tests.sh `All stages OK`).
- `.claude/sessions/2026-05-28-d020-session4-http-wrapper.md` — **D-020 session 4/7** : HTTP wrapper FastAPI (`api/http.py` POST /analyze + GET /status + /healthz, BackgroundTasks), facade.analyze v3 wired R-400-220 v2 MinIO write (manifest + chunks.jsonl + embeddings.jsonl + status.json), `storage/minio_layout.py` clés via RunPrefix, Metadata v3 + AnalysisResult v3 (artifact keys exposed). Infra : Dockerfile multi-stage + k8s manifests c13_extractor (deployment + service + kustomization) + compose dev. Smoke imports OK + 3 routes + Pydantic validation OK. **Différés** session 5+ : Batch API urgency=background, resume-from-phase R-400-225.
- `.claude/sessions/2026-05-28-d020-session3-refactor.md` — **D-020 session 3/7** : refactor LLM→lib + nouveaux adapters. OpenAI adapter v2 (base_url C8), `llm/embeddings_client.py` (B1), `extraction/reference_extractor.py` (lib-based), `storage/minio_writer.py` (boto3). writer_factory v4 actif sur minio. Settings v3 (minio_*, embedding_*). Tests dangling : 4 réécrits, 4 substitués, 2 module-skip avec TODO session 4/5. compileall green sur src + tests. Next = session 4 HTTP wrapper.
- `.claude/sessions/2026-05-28-d020-session2-strip.md` — **D-020 session 2/7 + spec amendments v2** : (1) 999/100/400/800 specs bumped v2 absorbing 5 optimisations (Haiku screener, prompt-cache markers, image dedup, embeddings@C13, urgency=batch). (2) Strip executed : -9589 LOC ay_extractor/src (60% reduction), 80 .py files survive. Survivors patched, pyproject trimmed, compileall green. Next = session 3 (refactor LLM→lib + minio_writer + embeddings_client).
- `.claude/sessions/2026-05-28-d020-ayextractor-c13-spec.md` — **D-020 AyExtractor adopted as dependency C13** (spec-only session 1/7) + file-manager UI build closure. Synthesis v6→v7 + 100/400/800 specs bumped + R-100-081 v2 + R-100-125 + R-400-020/021/022 v2 + R-400-220..225 + R-800-130..133. Re-partitions D-013 (C12 trigger / C13 extract+chunk / C7 embed+index). Code-isolated component, LLM-frugal default, MinIO-as-contract. Implementation in 6 next sessions. **File-manager UI tranche** (LiveDocsManager v2 + Working area v10 + 8 new tests, 131/131 vitest green) closed in same session.
- `.claude/sessions/2026-05-27-k8s-local-deployment.md` — **Platform on local k8s (docker-desktop)**. First real bring-up surfaced + fixed 8 manifest bugs (namespace conflict, SA, minio user, litellm OOM + callback mount, Traefik `--ping` + RBAC `nodes`, IngressRoute drift). LoadBalancer-on-Traefik for `localhost:56000`, demo flags via configmap literals. New tooling `e2e_stack.sh build` + `run.sh --crds`. Documents file-manager : apiClient socle + R-500-010 v2 ; UI build DEFERRED. Q-100-023 traced (prod base conflict).
- `.claude/sessions/2026-05-26-dev-stack-litellm-and-runtime-diagram.md` — **Dev stack rebuild for manual tests**. Fixed C7 boot crash (tree-sitter import→lazy) ; `e2e_stack.sh` v7 (dev includes the LiteLLM proxy) + override v11 (cost-forwarder mount fix) ; policy dev=real-LLM / CI=mock+Ollama ; runtime diagram `051` added. 17 containers healthy ; CI 1665 green.
- `.claude/sessions/2026-05-23-d017-c6-judge-and-regression.md` — **D-017 C6 slice : T3 LLM-judge (R-700-032) + quality-regression (R-700-033)**. T3 opt-in/best-effort via C8 `c6-judge` (D-011 cross-family = open gap, Anthropic-only) ; regression-on-completion (per-entity new-blocking + score drop, anti-feedback, best-effort). #3 de-stub deferred (load-bearing probe). CI 1665 green (87.53%).
- `.claude/sessions/2026-05-23-v2-3-eval-and-bitemporal.md` — **V2 #3 : retrieval eval harness (D-017 slice) + C bi-temporal (D-019)**. Eval : `tests/eval/` metrics + held-out golden set + dense-vs-hybrid runner (real Ollama) ; finding — v1 dense already recall@3=1.0 across query kinds → **D-010 stays closed, B not justified**. C : added D-019 + R-400-209 (spec) ; bi-temporal KG columns + `supersede_relation` (append-only) + `relations_as_of` (valid_at / known_as_of) ; ArangoDB-native (no graphiti lib, preserves D-002). CI 1630 green (87.35%). Remaining V2 #3 is gated/queued (B on production eval, full D-017 spec-first).
- `.claude/sessions/2026-05-23-v2-3-ab-ac-hybrid-contextualisation.md` — **V2 #3 Block A : A.b hybrid retrieval + A.c contextualisation (D-016 v1 subset complete)**. A.b : ArangoSearch BM25 view + `lexical_search` + RRF fusion (`retrieval/fusion.py`), `hybrid` default (R-400-202), stale-view delete guard. A.c : `contextualizer.py` (C8 `c7-contextualizer`→Haiku, doc as cache-friendly prefix, embeds contextualised text, best-effort/no-op without LLM). §10.4 D : federated-retrieval test split (hybrid=merge, dense-service=index-weight order, since RRF dilutes weights). Image-workflow analysis done (the eval/D-017 gap is the recurring theme) + `050-WORKFLOW` SVG redrawn as a 2-band blueprint. CI 1614 green (87.32%). Next : eval harness (D-017).
- `.claude/sessions/2026-05-22-v2-3-aa-structural-extraction.md` — **V2 #3-A.a : schema-guided L1 structural extractor (D-016 v1 subset)**. Closed ontology E-400-006 (`kg/ontology.py`) + two extractors → one ontology : requirements-corpus (deterministic, no dep, whitespace-agnostic) + code-AST Python (tree-sitter, NEW base dep ; Rust binding = method accessors → nodes typed `Any`). `extract-structural?kind=requirements|code` ; `code` reads raw bytes (Python indentation). `persist_structural` (+layer/ontology_version, exact insert count). Operator rebuilt the VM with latest-stable deps → fixed 2 refresh regressions (nats-py typing ; K8s e2e ConfigException→skip). Decided: Graphiti-style ArangoDB-native (not the lib, breaks D-002) ; B is D-010-gated, C is a §8.1 spec gap. CI 1593 green (87.25%).
- `.claude/sessions/2026-05-22-openhands-generate-poc.md` — **V2 #2 : OpenHands `generate`-phase POC adapter**. Gated seam (`OpenHandsGenerateEngine`, `C4_GENERATE_ENGINE=openhands`) running the OpenHands V1 SDK via C8/LiteLLM ; `openhands` OPTIONAL extra (sdk+tools 1.23) in the new `Dockerfile.c15-runner` only. Runner-injected (sole `openhands.*` importer) → §10.2-clean tests. FINISHED→DONE+files ; no fabricated gate_b_evidence (Gate B blocks by design = Q1 finding) ; provider=Anthropic-direct ; R-200-029 not ratified → no @relation. CI 1562 green (87.03%). Runbook for the operator-run Phase 1/2.
- `.claude/sessions/2026-05-22-c8-litellm-proxy-and-cost-tracking.md` — **V2 #1 : C8 LiteLLM proxy deployable + per-agent routing + cost tracking**. Off-the-shelf proxy (compose profil `litellm` + K8s base `c8_gateway` + generated ConfigMaps). Client-side routing → Claude by quality/cost. **Single `C8_GATEWAY_API_KEY`** consolidates `C3_C8_BEARER_TOKEN`/`SUB_AGENT_*`/`C4_K8S_SUB_AGENT_*` + hardcoded bearer (`ClientSettings.effective_bearer`). Cost forwarder (mounted §4.5) → cost receiver (`c8_llm/main.py`) → Arango `llm_calls`. settings.json v15 (`Read(**/.env)` removed, secrets stay denied). CI 1545 green (87.06%). Operator : `.env.secret` keys + the pre-existing c4-workers namespace conflict.
- `.claude/sessions/2026-05-21-docgen-versioning-and-ui-tranche.md` — **DocGen versioning + 6-feature UI tranche + V1/V2 boundary review**. Live-docs per-AI-response `(vN)` (turn-tagged commits, `ArtifactNode.version`), version-history viewer (`read_document_at_ref` + `git/commits?path`), drag-and-drop tree, expandable chain-of-thought tool detail (`done_event.arguments`), versioned "open in working area (vN)" links below the response (`DocumentRef.version`), full-width resizable persisted 3-pane working area. Contract additions : ArtifactNode/DocumentRef/InlineEvent. Backend CI 1528 green (86.96%). V1 functional remainder agreed (C6 stubs / LiteLLM deploy / prod) ; next = V2 scoping.
- `.claude/sessions/2026-05-19-validation-philosophy-and-npx.md` — Validation philosophy §3 + CLAUDE.md v16 §5.3. `settings.json` v14 allowlists npx biome/tsc/eslint/prettier.
- `.claude/sessions/2026-05-19-increment-3-cross-nav-state-and-sse-ownership.md` — Increment 3 (3a+3b) DONE : WorkspaceProvider Tier-1 store + provider-owned SSE loop.
- `.claude/sessions/2026-05-19-docgen-2c-and-llm-provider-migration.md` — Phase 2.C DocGen e2e + unified inline pipeline + LLM provider migration (Anthropic OpenAI-compat).
- `.claude/sessions/2026-05-12-ux-chat-finalisation.md` + `2026-04-29-ux-*.md` — UX chat finalisation, validation, Phase 4a auth shell, bootstrap, `/blob` download, tenant_manager.
- _Earlier 2026-04-22..28_ : git log + `sessions/`. Backbone (C1..C9,C12), CI/CD, observability, auth-matrix, Plan v1 A→F, RemoteMemoryService + AuthGuard.

---

## 7. Maintenance rules

- This file SHALL remain ≤ 150 lines.
- Claude SHALL propose an update at end of any session introducing a decision, completing a stage, or changing §5.
- User validates before each write (no silent edits) except trivial deltas per `CLAUDE.md` §9.1.
