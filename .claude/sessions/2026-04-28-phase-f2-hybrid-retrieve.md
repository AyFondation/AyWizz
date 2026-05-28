# Session 2026-04-28 — Plan v1.5 Phase F.2 : KG hybrid retrieval

## Trigger

Suite directe au plan v1 complet. F.2 était la seule phase reportée à
la v1.5 lors de F.1 ("graph traversal au retrieve"). Le graphe peuplé
par F.1 devient consommable pour enrichir le ranking de
`MemoryService.retrieve`.

Plan validé par l'utilisateur : combinaison **A+B**.

## Décisions actées

1. **Algorithme A+B combiné** plutôt que A seul ou B seul.
   - **A (pool widening)** : si le scan vector capte les top-K seeds
     mais que le graphe pointe vers des sources hors-scan, on FETCH
     ces chunks manquants directement (`fetch_chunks_for_source_ids`)
     et on les ajoute au pool noté.
   - **B (boost ranking)** : les chunks dont le source_id est
     graph-related à un seed se voient appliquer un multiplicateur
     `kg_expansion_boost` (default 1.3) sur leur score cosine.
   - Sans A, B est inactif quand le scan_cap mord (rien à booster).
   - Sans B, A est invisible en petit corpus (toutes les sources sont
     déjà dans le scan).
   - Combiné : observable à toute échelle.
2. **Paramètres** (`MemoryConfig` v3) :
   - `kg_expansion_depth=1` (1-hop ANY direction).
   - `kg_expansion_boost=1.3` (modeste, le graphe nudge sans dominer).
   - `kg_expansion_neighbour_cap=20` (borne le coût de la fetch
     supplémentaire).
3. **Toujours actif quand le graphe existe**, pas de flag d'opt-in.
   Si `kg_repo` n'est pas wiré OU si le graphe est vide pour le
   projet (find_neighbour_source_ids retourne []), aucun coût
   supplémentaire vs v1.
4. **Seeds NON boostés** : les chunks des sources seed sont déjà au
   top par cosine — les booster en plus mascarade le signal vector.
   Seules les sources voisines (résultat strict de la traversal) sont
   boostées.
5. **`retrieval_scan_cap` floor lowered** : `ge=100 → ge=2` dans le
   validateur Pydantic. Justification : les tests F.2 ont besoin
   d'exercer le path "fetch extras" en isolation (sinon il faut
   ingérer 100+ chunks par test). En prod ce floor est sans effet
   (jamais < 1000).

## Fichiers livrés

- `c7_memory/config.py` v2→v3 — 3 nouveaux Field :
  `kg_expansion_depth`, `kg_expansion_boost`,
  `kg_expansion_neighbour_cap`. Floor sur `retrieval_scan_cap`
  abaissé de 100 à 2.
- `c7_memory/kg/repository.py` v1→v2 — `find_neighbor_source_ids`
  (AQL : seeds par INTERSECTION sur `source_ids`, traversal 1..depth
  ANY direction sur `memory_kg_relations`, exclusion des seed
  vertices via filter `_key NOT IN seed_keys`, retourne
  `DISTINCT sid` à plat).
- `c7_memory/db/repository.py` v1→v2 — `fetch_chunks_for_source_ids`
  (même gating tenant/project/index/model que `scan_chunks` mais
  filtré sur `c.source_id IN @source_ids`, sans scan_cap — borné
  par cardinalité du caller).
- `c7_memory/service.py` v1→v2 — `retrieve` invoque le helper privé
  `_apply_kg_expansion` quand `kg_repo` est wiré ET `scored` non vide.
  Le helper :
  1. extrait seed_source_ids des top-K initiaux ;
  2. AQL traversal → neighbour_source_ids ;
  3. cap à `kg_expansion_neighbour_cap` ;
  4. soustrait les déjà-vus → fetch les extras → score cosine ;
  5. applique le boost à tous les chunks dont source_id ∈ neighbours ;
  6. re-trie, retourne le scored mis à jour.
- `tests/integration/c7_memory/test_kg_hybrid_retrieve.py` (NEW v1) —
  3 tests :
  - `test_retrieve_pure_vector_when_graph_is_empty` : kg_repo wiré
    + graphe vide → comportement identique au v1 (gamma 0.5 surface
    avant beta 0.41).
  - `test_retrieve_graph_boost_surfaces_neighbour_chunk` : graphe
    populé avec edge rocket→apple, beta boostée
    0.41 x 2.0 = 0.82 > gamma 0.5 → top-2 = {alpha, beta} (boost=2.0
    en config test pour effet observable).
  - `test_retrieve_pulls_in_chunks_beyond_scan_cap` : `scan_cap=2`
    cut off gamma. Sans KG, gamma absent du top-K. Avec KG (boost
    désactivé à 1.0 pour isoler proposition A), gamma est fetched
    via `fetch_chunks_for_source_ids` et apparaît dans le top-K.
- `.env.example` + `tests/.env.test` — 3 nouvelles vars
  `C7_KG_EXPANSION_*` ajoutées (sinon coherence test
  `test_env_completeness` casse).

## Tests CI

- 1156 → **1159 verts** (3 nouveaux tests F.2).
- `run_tests.sh ci` : ruff OK / mypy OK / pytest 1159 passed in 134s.

## Trajectoire de mise au point

| Itération | Échec | Cause | Fix |
|---|---|---|---|
| 1 | test 3 ValidationError | `retrieval_scan_cap` validateur `ge=100` bloquait `scan_cap=2` du test | floor abaissé à `ge=2` |
| 2 | ruff RUF002/003 | `×` (MULTIPLICATION SIGN) dans docstring/commentaires | remplacé par `x` |
| 3 | mypy dict-item | bind_vars dict avec types mixtes inférés trop strict | annotation explicite `dict[str, Any]` |
| 4 | coherence env_completeness | 3 vars manquantes dans `.env.example` + `.env.test` | ajoutées |
| 5 | green | — | — |

## Ce qui reste

F.2 en place et pinné. Les 3 leviers (`depth`, `boost`,
`neighbour_cap`) sont env-tunables. Suite immédiate du plan
post-v1 : Devcontainer Ryuk → K8s manifests (avec questions à
l'utilisateur). Frontend Next.js séparé.
