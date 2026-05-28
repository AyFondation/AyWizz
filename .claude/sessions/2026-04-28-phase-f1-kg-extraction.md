# Session 2026-04-28 — Phase F.1 v1 plan : Knowledge graph extraction

## Trigger

Phase F du plan v1 fonctionnel — la dernière, scope minimal F.1 (par
discussion : F.2 hybrid retrieval déféré v1.5). À ce stade, le journey
v1 était déjà fonctionnel end-to-end (A+B+C+D+E livrés). F.1 ajoute le
"au mieux" demandé par l'utilisateur : extraction LLM-based d'entités
+ relations sur les sources uploadées, persistées dans une vraie graphe
(Arango edge collection).

## Décisions actées

1. **Endpoint dédié `POST /sources/{sid}/extract-kg`** plutôt que
   inline dans `ingest_source`. Raison : extraction LLM est lente
   (~secondes/source), bloquante pendant l'upload c'est mauvais.
   Endpoint séparé = opt-in, async-friendly, testable.
2. **Schema Arango** : 2 collections.
   - `memory_kg_entities` (vertex) — composite key
     `{tenant}-{project}-{type}-{name}` (sanitisé). Multi-source
     mention converge sur le même vertex (provenance dans
     `source_ids: list[str]`).
   - `memory_kg_relations` (edge) — `_from`/`_to` pointent vers les
     entity vertices ; key
     `{tenant}-{project}-{subj}__{rel}__{obj}`.
3. **Pas de hybrid retrieve dans cette session**. Le graphe est
   "présent et inspectable" via les collections ; F.2 (graph
   traversal au moment du retrieve) reste à faire en v1.5.
4. **C7 LLM client wiring** : nouvelle injection optionnelle
   `llm_client: LLMGatewayClient | None` dans `MemoryService`.
   Endpoint répond 503 si non-wiré ; auth-matrix tests passent
   (insufficient role → 403, accepted role → 503/200 selon wiring).

## Code livré

### Nouveaux modules

- [`c7_memory/kg/`](ay_platform_core/src/ay_platform_core/c7_memory/kg/)
  package : `__init__.py`, `repository.py`, `extractor.py`.
- [`extractor.py`](ay_platform_core/src/ay_platform_core/c7_memory/kg/extractor.py) :
  - `_SYSTEM_PROMPT` strict-JSON format avec exemples.
  - `_strip_code_fence` tolère les ```json fences.
  - `_parse_response` lenient sur entrées malformées (skip), strict
    sur shape (raise `KGExtractionError`).
  - `extract_entities_and_relations(...)` async, accepte `LLMGatewayClient`,
    truncate à 6000 chars.
- [`repository.py`](ay_platform_core/src/ay_platform_core/c7_memory/kg/repository.py) :
  - `_sanitize_key_segment` — Arango _key allowed chars
    (`[A-Za-z0-9_-]`, lowercase). Convertit "Marie Curie" →
    `marie_curie`.
  - `KGRepository.persist(...)` — upsert entities (merge
    `source_ids` provenance), insert edges (skip si déjà présent).
    Idempotent.
  - `list_entities_for_source` / `list_relations_for_source` pour
    inspection (admin tooling + tests).

### Nouveaux models (Pydantic)

- [`KGEntity`](ay_platform_core/src/ay_platform_core/c7_memory/models.py),
  `KGRelation` (subject + relation + object), `KGExtractionResult`
  (response shape).

### Modifié

- [`c7_memory/service.py`](ay_platform_core/src/ay_platform_core/c7_memory/service.py) :
  - `MemoryService.__init__` accepte `kg_repo`, `llm_client`.
  - **Nouvelle méthode** `extract_kg(...)` : 503 si non-wiré, 404 si
    source inconnue, 502 sur LLM malformé. Reconstruit le texte source
    depuis les chunks Arango (cheap pour v1 sources ≤ MB).
- [`c7_memory/router.py`](ay_platform_core/src/ay_platform_core/c7_memory/router.py) :
  endpoint `POST .../sources/{source_id}/extract-kg` (role gate
  identique à upload : `project_editor`/`project_owner`/`admin`).
- [`c7_memory/main.py`](ay_platform_core/src/ay_platform_core/c7_memory/main.py)
  v3→v4 : construction `KGRepository` + `LLMGatewayClient`, lifespan
  appelle `kg_repo.ensure_collections()`.
- [`tests/e2e/auth_matrix/_stack.py`](ay_platform_core/tests/e2e/auth_matrix/_stack.py) :
  `_build_c7` accepte `llm_client` ; passé depuis le scripted LLM
  partagé du stack auth_matrix.
- [`_catalog.py`](ay_platform_core/tests/e2e/auth_matrix/_catalog.py) :
  +1 endpoint catalogué.

### Tests

[`tests/integration/c7_memory/test_kg_extraction.py`](ay_platform_core/tests/integration/c7_memory/test_kg_extraction.py)
v1 — 5 tests :

1. **`test_extract_kg_persists_entities_and_relations`** : ingest une
   source sur Marie Curie, scripted LLM retourne 3 entities + 2
   relations (discovered, taught_at). Assert : 200 + persist Arango
   correct (3 entities, 2 relations, names + relation types matchent).
2. **`test_extract_kg_idempotent_on_re_run`** : 2 appels successifs →
   pas de duplicats (entities + relations stables sur composite keys).
3. **`test_extract_kg_returns_404_for_unknown_source`** : source_id
   inconnu → 404, pas de side-effect.
4. **`test_extract_kg_returns_502_on_malformed_llm_response`** : LLM
   retourne du non-JSON → 502 BAD_GATEWAY (pas silencieux).
5. **`test_extract_kg_returns_503_when_llm_not_wired`** : C7 sans
   `llm_client` injection → 503 (graceful degrade).

Auto-paramétrés (anonymous + role + isolation matrix) couvrent aussi
le nouvel endpoint.

## Validation

`run_tests.sh ci` : ruff OK, mypy OK, pytest **1147 verts en 150s**
(+9 vs Phase E : 5 dirigés KG + 4 auto-paramétrés sur le nouvel
endpoint). 0 conteneur orphelin post-CI.

## v1 PLAN COMPLET ✅

| Phase | Statut | Tests |
|---|---|---|
| A — Tenant + Project lifecycle | ✅ | 6 dirigés + auth_matrix |
| C — Embeddings réels Ollama | ✅ | 3 slow real_embedder |
| B — Upload + parsers | ✅ | 7 dirigés (1/parser) |
| D — Chat-with-RAG | ✅ | 3 dirigés (round-trip + 2 fallbacks) |
| E — Conversation → memory loop | ✅ | 2 dirigés (multi-turn) |
| F.1 — KG extraction | ✅ | 5 dirigés (+ idempotence + erreurs) |

**Le journey v1 est complet et testé** :

1. tenant_manager crée tenant ✓
2. admin (du tenant) crée projet + grant project_owner ✓
3. project_editor upload PDF/MD/HTML/DOCX → blob MinIO + chunks
   Arango + embeddings Ollama all-minilm ✓
4. user pose une question dans une conversation liée au projet →
   retrieve C7 (sources + conversations) → augment prompt → C8
   streaming → réponse contextualisée ✓
5. follow-up question → bénéficie du contexte des turns précédents ✓
6. **F.1** : `POST .../extract-kg` sur n'importe quelle source →
   peuple `memory_kg_entities` + `memory_kg_relations` (graphe
   inspectable, hybrid retrieval F.2 différé v1.5) ✓

## Lessons

- **Arango _key sanitization** : `_key` n'accepte ni espaces ni
  certains chars spéciaux. Pattern `[A-Za-z0-9_-]` lower-common-
  denominator + replace-with-underscore évite les surprises sur
  des entités nommées humaines ("Marie Curie", noms d'organisations,
  etc.). Helper `_sanitize_key_segment` réutilisable.
- **Composite keys pour idempotence** : encoder `(tenant, project,
  name, type)` dans le `_key` permet l'upsert sans index unique
  séparé. Re-run = no-op au lieu de duplication. Pattern utilisé
  pour les entities ET les edges.
- **Lenient parser, strict shape** : pour les outputs LLM
  structurés, accepter `extra="forbid"` au niveau model + skip-
  malformed au niveau list (try/except continue). Compromis :
  les tests assertent sur les counts attendus, mais une réponse
  partiellement corrompue produit quand même un sous-ensemble
  utile au lieu de 502 entier.

## Suite (post v1)

- **F.2 hybrid retrieval** (v1.5) : enrichir
  `MemoryService.retrieve` avec graph traversal — pour chaque chunk
  top-K, lookup les entities mentionnées, traverse 1-hop, ajoute
  les chunks d'entities reliées. Re-rank.
- **C3 → C7/C8 wiring K8s production** : `RemoteMemoryService` /
  `RemoteLLMClient` httpx (pattern à la C9). Manifests R-100-060.
- **Devcontainer rebuild** pour `testcontainers/ryuk:0.5.x`.
- **R-100-060 — production K8s manifests**.
- **Q-100-016** trace propagation dans C15 Jobs (avec C15 sub-agent
  runtime, pas dans v1).

## Rollback

Branche `main` HEAD avant : commit post-Phase E. Rollback safe via
`git revert` :
- 2 nouveaux modules + 1 fichier tests + extensions models/service/
  router. Tous additifs.
- Pas de breaking change : `MemoryService(...)` sans `kg_repo` /
  `llm_client` reste valide (defaults None).
