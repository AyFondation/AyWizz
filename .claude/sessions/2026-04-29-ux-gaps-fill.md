# Session 2026-04-29 — Gap-fill UX (file download + tenant_manager + auto KG)

## Trigger

Suite de la session précédente sur RemoteServices/AuthGuard. Les 3
gaps UX restants identifiés à l'audit du journey utilisateur :

1. ✅ Chat-with-RAG K8s — fait précédente session.
2. **File download** — gap #5.
3. **Bootstrap tenant_manager** — gap #1.
4. **Auto KG extraction** — gap #3.
5. ⏸️ File tree — différé (post-MVP).
6. ⏸️ Cross-tenant promotion — différé (spec amendment requis).

Cette session livre les 3 du milieu en série.

## Décisions actées

### File download (gap #5)

1. **Nouvel endpoint `GET /api/v1/memory/projects/{p}/sources/{sid}/blob`**.
   Auth = AUTHENTICATED + Scope.PROJECT (même que `GET sources/{sid}`
   metadata). Retourne `Response(content=blob, media_type=mime_type,
   headers={"Content-Disposition": 'attachment; filename="..."'})`.
   v1 : full bytes en mémoire (capped à `C7_MAX_UPLOAD_BYTES`=50MiB).
   Streaming chunks reportés à v1.5+ si uploads > 50MiB.

2. **404 plutôt que 500** quand la source row existe mais le blob
   est absent (ingest JSON-only path n'écrit pas en MinIO). Detail
   message explicite ("source has no downloadable blob (ingested
   without upload)").

3. **503 quand storage non wiré** (cohérent avec endpoint upload).

### Bootstrap tenant_manager (gap #1)

4. **`_ensure_local_tenant_manager()`** parallèle à `_ensure_local_admin()`.
   Opt-in : ne fire QUE si `auth_mode=local` ET les deux fields
   `local_tenant_manager_username/password` sont non-vides. Single-
   tenant deployments laissent vides → admin alone suffit.

5. **Configs** : `local_tenant_manager_username` (default "")  +
   `local_tenant_manager_password` (default ""). Idempotent (skip si
   user déjà présent).

6. **E-100-002 v2 séparation des duties préservée** : TENANT_MANAGER
   est **content-blind** (tenant lifecycle ONLY) ; ADMIN gère le
   contenu tenant-scoped. Deux users distincts (`alice` admin +
   `platform-admin` tenant_manager), pas de role superposition.

7. **Env files dev/system-test** : tenant_manager activé par défaut
   avec `seed-tm-password`. `.env.example` reste vide (opt-out
   single-tenant).

### Auto KG extraction (gap #3)

8. **`MemoryService.ingest_uploaded_source()` étendu** : à la fin
   du flow (après `_index_parsed_source` réussi), si `auto_extract_
   kg_on_upload=True` ET `kg_repo` ET `llm_client` sont wirés,
   appelle `extract_kg(...)` en `contextlib.suppress(Exception)`.
   Best-effort : un échec KG SHALL NOT casser l'upload (chunks
   déjà persistés).

9. **Synchronous, pas async-job** en v1 : extract_kg est appelé en
   ligne dans la même request. Latence upload +5-30s pour la
   prompt LLM. Acceptable pour v1 ; alternative async via
   `asyncio.create_task` ou NATS reportée si latence devient
   bloquante.

10. **Flag config** `C7_AUTO_EXTRACT_KG_ON_UPLOAD: bool = True`.
    Permet désactivation par environnement (rate-limit LLM, latence
    critique, etc.). Désactivé dans `system-test` overlay (mock LLM
    pas wiré côté C7 K8s manifests aujourd'hui).

## Fichiers livrés

**File download** :
- `c7_memory/router.py` v1 (nouvel endpoint `/blob` + import Response)
- `c7_memory/service.py` v2 (méthode `download_source()` 503/404 typés)
- `tests/e2e/auth_matrix/_catalog.py` (nouvelle EndpointSpec, 73 endpoints total)
- `tests/integration/c7_memory/test_blob_download.py` (NEW v1, 4 tests)

**Bootstrap tenant_manager** :
- `c2_auth/config.py` : 2 nouveaux Field `local_tenant_manager_*`
- `c2_auth/main.py` : helper `_ensure_local_tenant_manager()` + lifespan call
- `tests/integration/c2_auth/test_local_tenant_manager_bootstrap.py` (NEW v1, 6 tests)

**Auto KG extraction** :
- `c7_memory/config.py` v3 : nouveau Field `auto_extract_kg_on_upload`
- `c7_memory/service.py` v2 : import contextlib + try/suppress en fin de `ingest_uploaded_source`
- `tests/integration/c7_memory/test_auto_kg_extraction.py` (NEW v1, 3 tests)

**Env files** (4 fichiers : `.env.example`, `tests/.env.test`,
`overlays/dev/.env`+`.env.secret`, `overlays/system-test/.env`+
`.env.secret`) : ajouts `C2_LOCAL_TENANT_MANAGER_*` +
`C7_AUTO_EXTRACT_KG_ON_UPLOAD` aux endroits cohérents.

**Spec sync** : `requirements/060-IMPLEMENTATION-STATUS.md` régénéré.

## Tests CI

- File download : +4 tests intégration + 1 test catalog auto-paramétré.
- Bootstrap tenant_manager : +6 tests intégration.
- Auto KG : +3 tests intégration.
- CI Python : 1184 → **1196 verts** (1184+12).
- run_tests.sh ci : ruff OK / mypy OK / pytest OK.

## Trajectoire de mise au point

| Itération | Échec | Fix |
|---|---|---|
| 1 | Upload helper missing `mime_type` form field | aligned with existing `_upload` |
| 2 | RUF100 + mypy unused-ignore sur fixture arg | retiré le noqa + type ignore |
| 3 | green | — |

## Ce qui reste pour le journey UX

✅ tous les gaps "haute priorité" du plan sont livrés. Server-side
mature pour démarrer l'UX :

- Login avec super-root (tenant_manager) ✅
- Login avec admin tenant ✅
- CRUD tenant + project + member (déjà existant) ✅
- Upload doc → indexation Arango vectordb + KG auto ✅
- Chat-with-RAG (RemoteServices wired) ✅
- File listing + download ✅

**Différé post-MVP** :
- File tree / arborescence — sources stockées plates aujourd'hui.
- Cross-tenant promotion — spec amendment requis (TENANT_MANAGER
  content-blind à redéfinir).

**Différé infra** :
- Prod overlay K8s (External Secrets Operator, HPA, NetworkPolicy).
- LiteLLM proxy K8s (mock_llm en prod-tier).
- `ingest_conversation_turn` HTTP endpoint pour Phase E en K8s.

## Suite proposée

Probablement le bon moment pour démarrer **`ay_platform_ui/`** Next.js
frontend — le server backend est mature suffisamment. Sinon prod
overlay K8s.
