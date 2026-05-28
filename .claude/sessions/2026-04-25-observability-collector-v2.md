# Session 2026-04-25 — `_observability` v2: Docker events subscription

## Trigger

Limitation observée à la session précédente (workflow envelope synthesiser) : `/services` listait seulement 7 services (les containers up à `_obs.start()` time). Tous les composants Python c2..c9 démarraient APRÈS `_obs` dans le compose stack et n'étaient donc jamais capturés. Conséquence : `/workflows/<trace_id>` retournait 404 sur les traces traversant les composants Python.

L'utilisateur a validé la suite §5 — collector v2 = ~30 min de travail, gain énorme sur l'usability live.

## Implémentation

### `_observability/collector.py` v1 → v2

Nouveautés :

- **`_monitored: set[str]` + `_monitored_lock: threading.Lock`** — set des container IDs déjà streaming. Guards la race entre l'initial scan et l'events watcher (les deux peuvent tomber sur le même container quasi-simultanément au boot du collector).
- **`_attach_to(container) -> bool`** — méthode idempotente. Returns `True` si un thread de stream a été spawné, `False` si le container était déjà monitoré. Centralise toute la logique d'attachement.
- **`_watch_events()`** — thread daemon qui consomme `self._client.events(decode=True, filters={"type": "container", "event": "start"})`. Server-side filter pour ne recevoir que les events relevants. Pour chaque event : `_handle_event` extrait le name + ID, vérifie le préfixe (`ay-`), récupère le Container via `containers.get(id)`, appelle `_attach_to`.
- **`_handle_event(event)`** — extrait, filtre, dispatch. Gère le cas où le container disparaît entre l'event et le `get` (rare mais possible — log warning, continue).

`start()` enchaîne maintenant : initial scan → `_attach_to` pour chaque match → spawn `_watch_events` thread. Le thread est ajouté à `self._threads` pour visibility.

### Tests unitaires

`tests/unit/_observability/test_collector.py` — **9 tests** qui couvrent ce qui est testable sans Docker live :

- **`TestAttachIdempotency`** (3 tests) : premier attach spawn, second skipped, distincts containers spawn chacun.
- **`TestEventDispatch`** (6 tests) : start event matching prefix attaches ; non-prefix ignored ; non-start action (e.g. `die`) ignored ; non-container event (e.g. `image/pull`) ignored ; `containers.get()` failure swallowed (warning seulement, pas de propagation) ; même container vu deux fois (init scan + duplicate event) → un seul thread.

Stratégie de test : `monkeypatch` sur `_stream_one` pour qu'il enregistre dans une liste au lieu de démarrer un vrai stream. `monkeypatch` sur `threading.Thread` avec un `_SyncThread` qui exécute le target immédiatement. Permet de tester la logique de dispatch sans concurrence ni Docker.

## Validation runtime

Stack relancé. Avant v2 :

```
/services → ["c12-workflow","minio-init","mock-llm","obs","ollama","ollama-seed"]
            (6 services — les init/backend up before _obs)
```

Après v2 :

```
/services → ["arangodb-init","c1-gateway","c12-workflow","c12-workflow-seed",
             "c2-auth","c3-conversation","c4-orchestrator","c5-requirements",
             "c6-validation","c7-memory","c9-mcp","minio-init","mock-llm",
             "obs","ollama","ollama-seed"]
            (16 services — TOUS les composants Python + init containers + backends)
```

Test `/workflows/<trace_id>` sur un seed `traceparent=00-cafebabe...-aaaaaaaaaaaaaaaa-01` envoyé à `/auth/config` (servi par c2_auth) :

```json
{
  "trace_id": "cafebabecafebabecafebabecafebabe",
  "started_at": "2026-04-25T17:15:17.220760+00:00",
  "ended_at": "2026-04-25T17:15:17.272136+00:00",
  "duration_ms": 51.376,
  "root_span_id": "c9f2ee92f1532e21",
  "spans": [{
    "trace_id": "cafebabecafebabecafebabecafebabe",
    "span_id": "c9f2ee92f1532e21",
    "parent_span_id": "aaaaaaaaaaaaaaaa",
    "component": "c2_auth",
    "operation": "GET /auth/config",
    "status_code": 200,
    "duration_ms": 51.376,
    ...
  }],
  "summary": {
    "components_touched": ["c2_auth"],
    "total_spans": 1,
    "errors": 0,
    "warnings": 0,
    "verdict": "ok"
  }
}
```

L'enveloppe est complète, le span de c2_auth est capturé en temps réel — c'est exactement ce qui ne marchait pas avant cette session.

## Spec / governance deltas

- `requirements/050-ARCHITECTURE-OVERVIEW.md` v3 → v4 (§9 update : Test-tier observability passe de "MVP implemented; v2 deferred" → "implemented (v2)").
- `ay_platform_core/src/ay_platform_core/_observability/collector.py` v1 → v2.
- `ay_platform_core/tests/unit/_observability/test_collector.py` (nouveau, 9 tests).
- `.claude/SESSION-STATE.md` (date + §5 + §6).

## Validation chiffrée

- 672 unit/contract/coherence + 21 integration = **693 tests verts** (+9 unit collector cette session).
- Stack `e2e_stack.sh up` : `_obs` capture tous les containers `ay-*`, qu'ils soient up à `start()` ou démarrés après.

## Lessons (candidats `/capture-lesson`)

- **Docker events filter server-side** (`filters={"type": ..., "event": ...}`) : préfère ça à un filtre client-side. Économise la bande passante du socket et simplifie le code (pas besoin de double-check Type/Action).
- **Race init-scan + events**: pattern classique des collecteurs en streaming. La protection : un set + lock dédiés à l'idempotence du `attach_to`. Les events ne doivent jamais provoquer un état dupliqué.
- **`_handle_event` doit être absolument résilient** — un seul event mal formé ne doit jamais tuer le watcher. Capter `Exception` au niveau `_watch_events` autour de `_handle_event(event)` est défensif mais nécessaire (le daemon Docker peut renvoyer des shapes inattendues entre versions).
- **Tester un collecteur sans Docker** : monkey-patch `_stream_one` + `threading.Thread`. Permet de tester la logique de dispatch (filtrage, idempotence, error swallowing) en isolation. Le streaming réel reste testé par le live stack smoke.

## Rollback

Branche `main` HEAD avant les sessions de la journée : commit `f402b71` (`pre-alpha-002`). Rollback global : `git reset --hard f402b71` + `git clean -fd`. À utiliser UNIQUEMENT sur instruction explicite.
