# Session 2026-04-28 — Phase D v1 plan : Chat-with-RAG dans C3

## Trigger

Phase D du plan v1 fonctionnel. À ce stade : C7 indexe (chunks +
embeddings réels Ollama), upload multipart fonctionne (Phase B), tenant
+ project + members existent (Phase A). Il manque la pièce qui ferme la
boucle utilisateur : envoyer un message dans une conversation et
recevoir une réponse LLM contextualisée par les sources du projet.

## Décisions actées

1. **Le RAG vit dans C3, pas dans C4.** C4 reste sur son pipeline 5-phase
   code-domain ; C3 — qui possède déjà la conversation lifecycle + le
   streaming SSE — wrappe directement retrieve C7 + LLM C8. Pas de
   nouveau composant ; pas de QA-plugin C4 dédié.
2. **In-process service injection plutôt que Remote HTTP**. Pour les
   tests + auth_matrix, ConversationService accepte `MemoryService` et
   `LLMGatewayClient` en injection directe. Le wiring K8s production
   (HTTP) sera fait avec `R-100-060` (manifests).
3. **Opt-in implicite** : `send_message_stream` passe en mode RAG quand
   (a) la conv a un `project_id`, (b) `MemoryService` est wiré, (c)
   `LLMGatewayClient` est wiré, (d) le request porte `X-Tenant-Id`.
   Sinon → fallback stub. Pas de query param explicite — le contrat
   est fonction de la configuration.

## Pipeline RAG livré

```
POST /api/v1/conversations/{id}/messages
   ↓
1. _require_access(conv, user)                    [C3]
2. append_message(USER, content)                  [C3 → Arango]
3. retrieve(query=content, top_k=5)               [C7]
4. _format_retrieved_chunks(hits)                 [C3 helper]
5. _recent_history_messages(last 6 turns)         [C3 → Arango]
6. ChatCompletionRequest(stream=True, messages=[
       SYSTEM(prompt + context),
       *history,
       USER(content),
   ])
7. async with c8.chat_completion_stream(...) as chunks:
      for chunk in chunks:
          yield SSE_format(extract_delta_content(chunk))
   yield "data: [DONE]\n\n"
8. append_message(ASSISTANT, full_reply)          [C3 → Arango]
```

## Code livré

### Modifié

- [`c3_conversation/service.py`](ay_platform_core/src/ay_platform_core/c3_conversation/service.py)
  v1→v2 :
  - `ConversationService.__init__` accepte `memory_service`,
    `llm_client`, `rag_top_k=5`, `rag_history_turns=6`.
  - `send_message_stream` accepte `tenant_id` ; route vers `_rag_stream`
    si tous les wires sont présents, sinon `_stub_stream`.
  - Nouveaux helpers : `_RAG_SYSTEM_PROMPT`,
    `_format_retrieved_chunks` (numéroté + truncation 800 chars +
    source/score), `_extract_delta_content` (parse OpenAI SSE
    chunks).
  - `_recent_history_messages` : récupère les N derniers turns sans
    inclure le user message just-saved.
- [`c3_conversation/router.py`](ay_platform_core/src/ay_platform_core/c3_conversation/router.py) :
  `send_message` lit `X-Tenant-Id` Header et le passe à
  `send_message_stream`.
- [`tests/e2e/auth_matrix/_stack.py`](ay_platform_core/tests/e2e/auth_matrix/_stack.py) :
  build order réorganisé — LLM client et c7_service construits avant
  c3_app pour permettre l'injection. `_build_c3` accepte les deux en
  kwargs optionnels.

### Nouveau

- [`tests/integration/c3_conversation/test_rag_chat_flow.py`](ay_platform_core/tests/integration/c3_conversation/test_rag_chat_flow.py)
  v1 — 3 tests :
  1. **`test_rag_flow_round_trip`** : pré-seed C7 avec un texte
     "Voyager 1 launched 1977", crée une conv liée au projet,
     envoie "When was Voyager 1 launched?". Asserte :
     - SSE termine par `[DONE]` ;
     - le scripted LLM a vu un prompt qui contient le chunk
       "September 5, 1977" (preuve que retrieve → augment a fonctionné) ;
     - la réponse assistant ("The Voyager 1 spacecraft was launched in
       1977.") est persistée dans la conversation.
  2. **`test_stub_fallback_when_llm_not_wired`** : ConversationService
     sans memory ni llm → réponse stub statique streamée.
  3. **`test_rag_skipped_when_conversation_has_no_project`** : RAG
     wires présents mais conv sans `project_id` → fallback stub, le
     LLM n'est PAS appelé (`scripted.prompts_seen` inchangé).

Le scripted LLM est inspectable (capture `body["messages"]` à chaque
appel) → tests peuvent asserter sur le prompt exact, pas juste le
status code.

## Validation

`run_tests.sh ci` : ruff OK, mypy OK, pytest **1136 verts en 124s**,
0 conteneur orphelin post-CI.

Sub-suite RAG dirigée :

```
tests/integration/c3_conversation/test_rag_chat_flow.py  3 passed
```

## Décisions différées (non bloquantes pour Phase D)

- **C3 → C7/C8 wiring K8s production** : sera `RemoteMemoryService` /
  `RemoteLLMClient` (httpx) au moment des manifests R-100-060. Les
  interfaces sont déjà compatibles (MemoryService a `retrieve(...)`,
  LLMGatewayClient a `chat_completion_stream(...)`) ; un Remote
  pattern à la C9 suffit. Code minimal, juste pas dans cette
  session.
- **Configuration RAG runtime** : `rag_top_k`, `rag_history_turns`
  sont aujourd'hui hard-codés au constructeur. Ajout futur via
  `ConversationConfig` quand un opérateur voudra tuner.
- **Citations dans la réponse** : le prompt formate les chunks
  numérotés `[1]`, `[2]` mais la réponse LLM ne référence pas
  forcément les numéros. Phase F + UX peuvent ajouter une
  post-processing pour extraire les citations.

## Lessons

- **Streaming SSE → SSE bridging** : C8 yield des chunks OpenAI
  (`{"choices":[{"delta":{"content":"..."}}]}`), C3 doit re-émettre
  comme SSE plat (`data: <token>\n\n`). Helper `_extract_delta_content`
  gère le cas où `choices` est vide (final usage event) ou `content`
  absent (premier event role-only). Pattern réutilisable pour tout
  re-stream OpenAI → client web.
- **Build order dans test stack** : quand un composant injecte un
  autre composant en service-direct, l'ordre des `_build_*` dans
  `build_stack` matter. Reorganized to construct deps first (LLM
  client + c7_service) before consumers (c3_app). Pattern : si
  cyclique, casse le cycle via Remote httpx + ASGITransport.
- **Opt-in RAG sans flag** : faire dépendre l'activation de la
  configuration (project_id présent + service wiré) plutôt que d'un
  query param évite le risque de "j'ai oublié `?rag=on`". Le
  fallback stub reste explicite et observable.

## Suite

État du plan v1 fonctionnel à fin Phase D :

- ✅ Phase A — Tenant + Project lifecycle
- ✅ Phase C — Embeddings réels (Ollama)
- ✅ Phase B — Upload + parsers
- ✅ **Phase D — Chat-with-RAG dans C3**
- ⏳ **Phase E** — Conversation → memory loop (~1 session) ← prochaine
- ⏳ Phase F — KG extraction F.1 (~1-2 sessions)

Phase E ferme le cycle de la mémoire : chaque échange user/assistant
est ré-injecté dans C7 comme `source_type=conversation`. Une question
de follow-up bénéficie alors du contexte conversationnel précédent en
plus des sources uploadées.

## Rollback

Branche `main` HEAD avant : commit post-Phase B. Rollback safe via
`git revert` :
- `c3_conversation/service.py` v2 → v1 : breaking change uniquement
  pour les callers qui utilisent les nouveaux kwargs (`memory_service`,
  `llm_client`, `tenant_id`) ; les anciens callers (`ConversationService(repo)`
  sans kwargs) restent valides — backward-compatible.
- `_stack.py` build order : reorganisé mais pas conflictuel.
- Test fichier nouveau, suppressible sans impact.
