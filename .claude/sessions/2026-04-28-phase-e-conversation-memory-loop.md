# Session 2026-04-28 — Phase E v1 plan : Conversation → memory loop

## Trigger

Phase E du plan v1 fonctionnel. Phase D livrait le RAG one-shot (un
message → retrieve sources → réponse LLM). Phase E ferme la boucle :
chaque échange user/assistant est ré-injecté dans C7 sous l'index
`CONVERSATIONS`, donc une question de follow-up bénéficie du contexte
des turns précédents en plus des sources uploadées.

## Décisions actées

1. **Nouvel `IndexKind.CONVERSATIONS`** plutôt qu'un champ
   `source_type` sur Source. Raison : le pattern fédéré existant
   gère déjà N indexes (REQUIREMENTS + EXTERNAL_SOURCES) ; ajouter
   un 3e index réutilise l'infra de retrieve sans couches d'abstraction
   supplémentaires. Une retrieve C3 cible désormais
   `[EXTERNAL_SOURCES, CONVERSATIONS]` ; les autres composants peuvent
   rester sur `EXTERNAL_SOURCES` seul (pas de breaking change).
2. **One source row per turn** (key = `conv:{conv_id}:{turn_id}`).
   Plus simple qu'un single row qui s'agrège ; évite des
   races / concurrent updates. Source row apparaît dans les
   listings admin avec `uploaded_by=conv:{conv_id}` pour audit.
3. **Best-effort ingestion** : `try/except suppress(Exception)`
   autour de `ingest_conversation_turn`. La SSE a déjà émis `[DONE]`
   et la réponse user-facing est complète quand cette ingestion
   tente — un échec embedder/quota ne SHALL PAS propager.
4. **Quota enforcement** : conversation turns consomment le quota
   projet comme une upload normale. Pas d'exemption — sinon une
   conversation longue peut épuiser le projet à l'insu de l'opérateur.

## Code livré

### Modifié

- [`c7_memory/models.py`](ay_platform_core/src/ay_platform_core/c7_memory/models.py) :
  `IndexKind.CONVERSATIONS = "conversations"` ajouté à l'enum.
- [`c7_memory/service.py`](ay_platform_core/src/ay_platform_core/c7_memory/service.py) :
  - `_index_parsed_source` accepte `index_kind: IndexKind =
    IndexKind.EXTERNAL_SOURCES` paramètre (rétrocompatible).
  - **Nouvelle méthode** `ingest_conversation_turn(...)` : prend
    `tenant_id`, `project_id`, `conversation_id`, `turn_id`,
    `user_message`, `assistant_reply`, `actor_id`. Concatène
    "User: ...\n\nAssistant: ..." et passe par le pipeline shared
    avec `index_kind=CONVERSATIONS`. Quota check inclus.
- [`c3_conversation/service.py`](ay_platform_core/src/ay_platform_core/c3_conversation/service.py) :
  - `_rag_stream` retrieve désormais
    `[IndexKind.EXTERNAL_SOURCES, IndexKind.CONVERSATIONS]`.
  - Après `append_message(ASSISTANT, full_reply)`, appel
    `memory.ingest_conversation_turn(...)` enveloppé dans
    `contextlib.suppress(Exception)`. `turn_id` = id du message
    assistant persisté.

### Tests

[`test_rag_chat_flow.py`](ay_platform_core/tests/integration/c3_conversation/test_rag_chat_flow.py)
v1 → v2, +2 tests Phase E :

5. **`test_conversation_memory_loop_indexes_turns_in_c7`** : after
   one turn, scan Arango `memory_chunks` directement →
   `index='conversations'` rows présentes contenant le user message
   "Voyager 1" + l'assistant reply "1977".
6. **`test_followup_retrieves_prior_turn_context`** : multi-turn :
   - Turn 1 : "Tell me about Marrakesh." → assistant scripted reply
     "Marrakesh is a Moroccan city famous for its medina."
   - Turn 2 : "What is Marrakesh famous for?" → le PROMPT envoyé au
     LLM contient "medina" (preuve que le chunk de turn 1 a été
     retrieved depuis l'index CONVERSATIONS).

Les 5 tests existants restent verts : RAG round-trip, stub fallback,
skip-no-project, conversation-memory-loop, follow-up-retrieves-prior.

## Validation

`run_tests.sh ci` : ruff OK, mypy OK, pytest **1138 verts en 145s**
(+2 tests Phase E vs Phase D). 0 conteneur orphelin post-CI.

Sub-suite RAG dirigée :

```
tests/integration/c3_conversation/test_rag_chat_flow.py  5 passed
  - test_rag_flow_round_trip
  - test_stub_fallback_when_llm_not_wired
  - test_rag_skipped_when_conversation_has_no_project
  - test_conversation_memory_loop_indexes_turns_in_c7
  - test_followup_retrieves_prior_turn_context
```

## Lessons

- **Federated retrieval scaling well** : ajouter un nouvel
  `IndexKind` n'a coûté que 3 modifs dans le repo + service (et 0
  dans le code retrieve qui itérait déjà sur la liste reçue).
  Pattern qui se prête à F (KG entities) : un futur
  `IndexKind.KG_ENTITIES` réutilisera la même infra.
- **Best-effort write avec contextlib.suppress** : pour les
  side-effects post-stream (memory ingestion, telemetry, billing
  hooks), `contextlib.suppress(Exception)` exprime mieux l'intent
  que `try/except: pass` et passe ruff SIM105.
- **Multi-turn test fixture** : re-scripter `scripted.reply_tokens`
  entre les turns + re-cleaner `prompts_seen` permet d'isoler
  l'assertion sur la N-ième prompt. Pattern réutilisable pour
  tester n'importe quel state machine multi-step.

## État du plan v1 fonctionnel

- ✅ A — Tenant + Project lifecycle
- ✅ C — Embeddings réels (Ollama)
- ✅ B — Upload + parsers
- ✅ D — Chat-with-RAG
- ✅ **E — Conversation → memory loop**
- ⏳ **F** — Knowledge graph extraction F.1 (~1-2 sessions) ← prochaine

À ce stade, le journey utilisateur cible est **fonctionnel
end-to-end** :
1. tenant_manager crée tenant ✓
2. admin crée projet ✓
3. project_owner upload PDF/MD/HTML/DOCX ✓
4. user pose une question → réponse LLM contextualisée par les
   sources du projet ✓
5. user pose un follow-up → bénéficie du contexte conversation
   précédent ✓

F est le bonus "au mieux" du plan : extraction d'entités/relations
LLM-based pour peupler un knowledge graph (F.1) ; le hybrid retrieval
(F.2) reste différé v1.5.

## Rollback

Branche `main` HEAD avant : commit post-Phase D. Rollback safe via
`git revert` :
- IndexKind.CONVERSATIONS additif — aucun consommateur existant ne
  query cette valeur.
- `_index_parsed_source` paramètre par défaut `EXTERNAL_SOURCES` =
  comportement antérieur.
- C3 retrieve désormais sur 2 indexes — si ROLLBACK partiel, la
  retrieve renverra simplement 0 hits sur CONVERSATIONS, transparent.
