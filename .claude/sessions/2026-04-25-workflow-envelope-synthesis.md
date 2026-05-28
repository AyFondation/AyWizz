# Session 2026-04-25 — Workflow envelope synthesiser (phase 3, Q-100-014)

## Trigger

Suite directe des phases 1+2 (structured logging + W3C trace propagation, livrés plus tôt dans la journée). L'utilisateur valide la phase 3 ("ok go") et soulève en parallèle une question architecturale critique : **est-ce que l'agrégation marche en K8s avec scaling horizontal ?**

La question redéfinit le design : l'algorithme doit être portable entre stockage local (`LogRingBuffer`) et stockage K8s (Loki, ES, …).

## Réponse à la question K8s — résumé

**Oui, le mécanisme marche tel quel en K8s** parce que :

- `traceparent` est un header HTTP — préservé par Ingress / load balancers / service mesh.
- Chaque pod émet ses propres logs JSON sur stdout, mais **tous les pods d'une même requête portent la même `trace_id`**.
- R-100-006 (no sticky sessions) garantit qu'on n'a pas de couplage requête-pod.

**Ce qui ne tient pas en K8s** :

- Le test-tier `_observability` lui-même : il consomme le socket Docker, ne fonctionne pas avec containerd ; et de toute façon R-100-121 lui interdit le déploiement en staging/production.
- L'équivalent K8s = Loki/Promtail (baseline open-source) ou ELK / Grafana Cloud Logs.

**Conséquence sur le design** :

- `_observability/synthesis.py` = **fonctions pures** sans dépendance au stockage.
- Adapter local : `_observability/main.py` pull depuis `LogRingBuffer`.
- Adapter K8s (futur, Q-100-015) : pull depuis Loki API → mêmes fonctions de synthèse.

J'ai ajouté **Q-100-015** (K8s log store adapter) et **Q-100-016** (trace propagation dans C15 Jobs via `env: TRACEPARENT=...` dans le PodSpec) à la spec.

## Implémentation phase 3

### `_observability/synthesis.py` (nouveau, ~190 lignes)

Fonctions pures, storage-agnostic :

- `parse_span_summary(json_line: str) -> Span | None` — parse une ligne JSON ; retourne `Span` si `event=span_summary`, `None` sinon. Lenient : malformed JSON, missing fields, invalid timestamps → `None` au lieu d'exception.
- `parse_lines(lines: Iterable[str]) -> list[Span]` — itère sur un stream de lignes en filtrant silencieusement les non-summaries.
- `group_by_trace(spans: Iterable[Span]) -> dict[str, list[Span]]` — group par `trace_id`. Ignore les `trace_id` vides.
- `synthesise_workflow(spans: list[Span]) -> dict` — l'algorithme central. Toutes les spans doivent partager le même `trace_id` (caller's responsibility, valide via `group_by_trace` d'abord). Renvoie l'enveloppe JSON :
  - `trace_id`, `started_at`, `ended_at`, `duration_ms`
  - `root_span_id` — span sans parent (ou earliest si aucun parentless)
  - `spans[]` — chronologique, chaque span avec `operation` calculée (`method + path`)
  - `summary` : `components_touched`, `total_spans`, `errors` (status≥500), `warnings` (400≤status<500), `verdict` (ok/warn/error)
- `list_recent_traces(spans, limit) -> list[dict]` — résumés compacts triés par `ended_at` desc, pour le `/workflows?recent=N`.

Dataclass `Span(trace_id, span_id, parent_span_id, component, method, path, status_code, duration_ms, sampled, started_at)` avec proprietés `operation` et `ended_at` calculées.

### Endpoints `_observability/main.py`

```
GET /workflows/{trace_id}  → enveloppe complète, ou 400 (malformed) / 404 (unknown)
GET /workflows?recent=N    → liste triée des N traces récentes (default 10, max 200)
```

Les deux récupèrent toutes les entries du `LogRingBuffer` via `tail(limit=100_000)`, parsent les JSON pour extraire les `event=span_summary`, et passent le résultat aux fonctions pures de synthesis.

`app.state.log_buffer = buffer` exposé pour permettre aux tests de pré-seeder le buffer sans démarrer le collector réel.

### Tests

- `tests/unit/_observability/test_synthesis.py` — **19 tests** :
  - `parse_span_summary` : ligne valide, autre event ignoré, no event field, JSON malformé, non-objet, timestamp invalide, started_at = timestamp - duration_ms, parse_lines silently filters.
  - `group_by_trace` : groupage correct, trace_id vide ignoré.
  - `synthesise_workflow` : 2-span chain (root + child), verdict ok/warn (4xx)/error (5xx), root fallback (no parentless), empty input raises, mixed traces raises.
  - `list_recent_traces` : sort ended_at desc, respect limit, empty input.
- `tests/integration/observability/test_workflow_endpoint.py` — **6 tests** :
  - Pre-seeded buffer with 2 traces (one multi-span ok, one single-span 503).
  - `GET /workflows/<known>` → enveloppe correcte, root_span_id, components_touched, verdict.
  - `GET /workflows/<unknown>` → 404 avec message explicite.
  - `GET /workflows/short` → 400 (validation).
  - `GET /workflows?recent=10` → liste triée par ended_at desc, format compact.
  - `GET /workflows?recent=1` → respect limit.
  - `GET /workflows` → default limit 10.
  
Les tests integration utilisent `monkeypatch` pour no-oper `LogCollector.start/stop` (le devcontainer-of-devcontainer-of-Docker n'est pas trivial).

### Validation runtime

Stack relancé. Test :

```python
# Send a request with a known trace_id to _obs (which monitors itself).
GET /digest with traceparent=00-feedfacefeedfacefeedfacefeedface-1111111111111111-01

# Then query the workflow envelope.
GET /workflows/feedfacefeedfacefeedfacefeedface
→ 200, JSON envelope with trace_id, root_span_id, 1 span (component=_observability,
  operation="GET /digest", duration_ms=0.372), verdict=ok.
```

Limitation observée (déjà connue) : le collector ne voit que les containers up à son `start()`, donc les c2..c9 démarrés après `_obs` ne sont pas dans `/services` ni dans le buffer. C'est `_observability` v2 (Q-100-015 / §5.2 SESSION-STATE) qui réglera ça via Docker events subscription. Pas un défaut de la phase 3.

## Spec / governance deltas

- `requirements/100-SPEC-ARCHITECTURE.md` v8 → v9 (Q-100-014 closed, Q-100-015 + Q-100-016 added).
- `requirements/050-ARCHITECTURE-OVERVIEW.md` v2 → v3 (§9 update : Q-100-014 implemented, Q-100-015/016 listed).
- `ay_platform_core/src/ay_platform_core/_observability/synthesis.py` (nouveau).
- `ay_platform_core/src/ay_platform_core/_observability/main.py` (extended : import synthesis, expose `app.state.log_buffer`, new `/workflows` routes).
- `tests/unit/_observability/test_synthesis.py` (nouveau, 19 tests).
- `tests/integration/observability/test_workflow_endpoint.py` (nouveau, 6 tests).
- `.claude/SESSION-STATE.md` (date + §5 + §6).

## Validation

- 663 unit/contract/coherence + 21 integration = **684 tests verts** (+25 cette session : 19 synthesis unit + 6 endpoint integration).
- Stack `e2e_stack.sh up` : tous services healthy (Python services pas dans `_obs` /digest, mais ils tournent).
- Live workflow synthesis validé sur une requête seedée `GET /digest`.

## Lessons (candidats `/capture-lesson`)

- **Storage-agnostic algorithms** quand on touche à la production K8s : la même fonction de synthèse sert le local (ring buffer) et le prod (Loki/ES). Il suffit de garder la signature pure (`spans -> envelope`) et de séparer l'ingestion. Aucun travail de portage à la migration.
- **Le `traceparent` header est universel HTTP** — pas de magie K8s nécessaire pour la propagation. Tout load balancer correct le préserve. La complexité K8s n'est pas dans la propagation mais dans la **collecte** des logs émis par N pods.
- **Les Kubernetes Jobs ephemères (C15) ne sont pas auto-instrumentés**. Quand C4 dispatchera un sub-agent comme un Job, il faudra explicitement injecter `TRACEPARENT` dans les env vars du PodSpec. Sinon le sub-agent démarre une nouvelle trace, et l'enveloppe perd la phase de génération entière.
- **`monkeypatch` sur `LogCollector.start/stop`** dans les tests integration est suffisant pour neutraliser le Docker socket. Pas besoin de mocker DockerClient ni de refactor création/injection.

## Rollback

Branche `main` HEAD avant les sessions de la journée : commit `f402b71` (`pre-alpha-002`). Rollback global : `git reset --hard f402b71` + `git clean -fd`. À utiliser UNIQUEMENT sur instruction explicite.
