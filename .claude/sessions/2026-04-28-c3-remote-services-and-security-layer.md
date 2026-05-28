# Session 2026-04-28 → 2026-04-29 — C3 RemoteServices + AuthGuardMiddleware

## Trigger

Suite de l'audit du journey utilisateur (login → projet → upload →
chat-with-RAG → cross-tenant). 5 gaps identifiés, ordre validé :
chat-with-RAG K8s → file download → bootstrap tenant_manager → auto
KG extraction → file tree (deferred) → cross-tenant (deferred).

Cette session livre **gap #1 — chat-with-RAG K8s** (Rounds 1+2) puis,
suite à une question architecture de l'utilisateur sur la sécurité,
livre aussi un **layer de vérification systématique** (Round 3 —
AuthGuardMiddleware).

## Décisions actées

### Round 1+2 — RemoteServices

1. **`RemoteMemoryService`** (`c7_memory/remote.py`) — implémente
   `retrieve()` (POST `/api/v1/memory/retrieve`) avec propagation
   forward-auth headers (`X-User-Id`, `X-Tenant-Id`, `X-User-Roles`).
   `ingest_conversation_turn()` stubbé (NotImplementedError) — wrappé
   par `contextlib.suppress(Exception)` côté C3, donc Phase E
   (conversation memory loop) silencieusement désactivée en K8s sans
   casser le chat. Lift de ce stub = future revision avec endpoint
   dédié `POST /api/v1/memory/projects/{p}/conversations/{c}/turns`.

2. **`MemoryService.retrieve` + `ingest_conversation_turn` acceptent
   `**_forward_auth_kwargs`** — backward-compat ; le in-process
   ignore (déjà à confiance dans son `tenant_id` arg) ; le Remote en
   a besoin pour les headers HTTP. Une seule signature, deux
   implémentations interchangeables.

3. **`ConversationService` accepte `MemoryService | RemoteMemoryService
   | None`** — Union type, duck-typed sur les deux méthodes utilisées.
   `send_message_stream` accepte `user_roles: str = "project_editor"`
   et le passe à `_rag_stream` puis `memory.retrieve()`.

4. **C3 router** extrait `X-User-Roles` via `Header(default=
   "project_editor", alias="X-User-Roles")` et le passe au service.

5. **C3 `main.py` (v3)** wire `RemoteMemoryService(C3_C7_BASE_URL)` +
   `LLMGatewayClient(ClientSettings())` quand `C3_C7_BASE_URL` ET
   `C8_GATEWAY_URL` sont tous deux non-vides. Sinon fallback stub
   (legacy v2). Lifespan ferme proprement les httpx clients.

6. **mock_llm K8s** dans `infra/k8s/base/_mock_llm/` (Deployment +
   Service, image partagée, COMPONENT_MODULE=_mock_llm). Préfixe
   underscore = test-only — l'overlay `system-test/` opt-in via
   `resources: [- ../../base/_mock_llm]`. Dev/prod overlays ne
   l'incluent PAS (R-100-121).

7. **Env files** :
   - `.env.example` + `.env.test` ajoutent `C3_C7_BASE_URL=` et
     `C3_C8_BEARER_TOKEN=` (vides → stub mode).
   - `overlays/dev/.env` idem (vides).
   - `overlays/system-test/.env` les set à
     `http://c7-memory.aywizz.svc.cluster.local:8000` + `mock-no-auth`
     pour activer chat-with-RAG dans les system_k8s tests.
   - **Évité collision env-var** : `c8_gateway_url` retiré de
     `ConversationConfig` (lu par `ClientSettings.gateway_url`
     existant) ; `C3_C8_BEARER_TOKEN` (env_prefix `c3_`) au lieu
     de `C8_BEARER_TOKEN` (qui aurait collidé si un autre
     composant l'introduisait).

### Round 3 — Security layer

8. **`AuthGuardMiddleware`** (`observability/auth_guard.py`) ASGI
   middleware defense-in-depth qui retourne 401 immédiat si une
   request reach un path NON-EXEMPT sans `X-User-Id` non-vide.
   Layer 1 (edge) = Traefik forward-auth-c2 ; Layer 2 (componant) =
   ce middleware si l'edge est misconfig OU bypass intra-cluster.

9. **Per-component exempt lists** :
   - C2 : `["/health", "/auth/config", "/auth/login", "/auth/token",
     "/auth/verify"]` — la surface auth publique.
   - C3, C4, C5 : `["/health"]` (default).
   - C6 : `["/health", "/api/v1/validation/health"]`.
   - C7 : `["/health", "/api/v1/memory/health"]`.
   - C9 : `["/health", "/api/v1/mcp/health"]`.

10. **Order matters** dans Starlette — `add_middleware(A); add_middleware
    (B)` → B wraps A wraps app, donc B runs FIRST. Pour que le log
    de `auth_guard_block` ait le `trace_id`, j'ajoute AuthGuardMiddleware
    AVANT TraceContextMiddleware (innermost vs outermost). Vérifié
    par les tests.

11. **Sémantique "is there an authenticated user at all?"** —
    autorisation fine (role / tenant / project membership) reste dans
    chaque router via `_require_role(...)`. Pas de god-object central :
    chaque composant possède son model et fait l'autorisation
    contextuelle.

## Fichiers livrés

**Round 1** — RemoteMemoryService :
- `ay_platform_core/src/ay_platform_core/c7_memory/remote.py` (NEW v1)
- `ay_platform_core/tests/unit/c7_memory/test_remote_service.py` (NEW v1, 9 tests)
- `ay_platform_core/tests/integration/c7_memory/test_remote_service.py` (NEW v1, 2 tests)

**Round 2** — C3 wiring + mock_llm + env files :
- `ay_platform_core/src/ay_platform_core/c7_memory/service.py` v2 (kwargs swallow)
- `ay_platform_core/src/ay_platform_core/c3_conversation/service.py` (Union type, user_roles propagation)
- `ay_platform_core/src/ay_platform_core/c3_conversation/router.py` (X-User-Roles extraction)
- `ay_platform_core/src/ay_platform_core/c3_conversation/main.py` v3 (Remote+LLM wiring)
- `infra/k8s/base/_mock_llm/{deployment,service,kustomization}.yaml` (NEW)
- `infra/k8s/overlays/system-test/{kustomization.yaml, .env, .env.secret}` (mock_llm + RAG vars)
- `infra/k8s/overlays/dev/.env`, `.env.example`, `tests/.env.test` (3 nouvelles vars)
- `pyproject.toml` markers (déjà fait dans précédente session)

**Round 3** — Security layer :
- `ay_platform_core/src/ay_platform_core/observability/auth_guard.py` (NEW v1)
- `ay_platform_core/tests/unit/observability/test_auth_guard.py` (NEW v1, 7 tests)
- `ay_platform_core/src/ay_platform_core/c{2,3,4,5,6,7,9}_*/main.py` (chacun ajoute AuthGuardMiddleware avec son exempt list)

**Spec sync** :
- `requirements/060-IMPLEMENTATION-STATUS.md` régénéré (259 R-*).

## Tests CI

- Round 1 : 9 unit + 2 integration nouveaux. **1170 verts**.
- Round 2 : pas de nouveau test (régression check). **1172 verts**.
- Round 3 : 7 unit nouveaux. **1179 verts**.
- `run_tests.sh ci` : ruff OK / mypy OK / pytest OK partout.

## Trajectoire de mise au point

| Itération | Échec | Cause | Fix |
|---|---|---|---|
| 1 | ruff RUF100 | `# noqa: SLF001` non activé | retiré le noqa |
| 2 | coherence env | `C8_GATEWAY_URL` collision (ConversationConfig.c8_gateway_url ↔ ClientSettings.gateway_url) | retiré le field, utilise `ClientSettings()` directement |
| 3 | coherence env | `C8_BEARER_TOKEN` orphan (lu par aucun field) | retiré du .env, ajouté `C3_C8_BEARER_TOKEN` (env_prefix c3_) |
| 4 | mypy arg-type | `**guard_kwargs: object` non-iterable | typé `Any` |
| 5 | green | — | — |

## Ce qui reste post-session

1. **Smoke tests `test_v1_contract_pin.py`** — j'ai exempté
   `/api/v1/<comp>/health` dans les guards C6/C7 pour ne pas casser
   ces smoke tests. Long terme, ces tests devraient inclure les
   forward-auth headers (cohérence avec K8s prod où ils sont
   gardés par Traefik). Pas critique aujourd'hui.

2. **Conversation memory loop (Phase E) en K8s** désactivée silencieusement
   (RemoteMemoryService.ingest_conversation_turn raise
   NotImplementedError + `contextlib.suppress`). Restoration =
   ajouter endpoint `POST /api/v1/memory/projects/{p}/conversations/
   {c}/turns` à C7.

3. **Tests système K8s `test_basic_smoke.py`** ne couvrent PAS encore
   le chat-with-RAG flow. Quand le push CI tournera, on saura si
   les manifests + RemoteServices sont bien wirés. À étendre avec
   un test chat-end-to-end dans une future session.

4. **CI réelle non testée** — kind n'est pas dans le devcontainer ;
   tout passe en CI au push GitHub. Premier signal réel viendra
   du workflow `ci-k8s-validate.yml` job L4.

5. **NetworkPolicy / mTLS** mentionnés dans la réponse à la question
   sécurité — pas implémentés. Strictement parlant, le trust
   inter-pod (C3→C7 propage X-User-Roles tel quel) repose sur
   l'intégrité du code C3 + l'isolation réseau cluster. À durcir
   en prod avec NetworkPolicy / Istio mTLS.

## Suite proposée (gaps restants journey utilisateur)

2. **File download** (~1h) — `GET /api/v1/memory/projects/{p}/sources/
   {sid}/blob` pour visualisation/téléchargement par le frontend.
3. **Bootstrap tenant_manager** (~30 min) — créer le super-root
   au démarrage de C2.
4. **Auto KG extraction on upload** (~1-2h) — Job async ou trigger
   inline post-upload.

Suffisamment de matière server-side pour démarrer la partie UX
après ces 3 items.
