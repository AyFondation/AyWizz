# Session 2026-04-27 — Phase C v1 plan : Ollama embedder par défaut

## Trigger

Phase C du plan v1 fonctionnel. Objectif : que la RAG ait une qualité
réelle (retrieval sémantique, pas bag-of-words). Le hash déterministe
est inutile pour un user qui pose une question — il faut des vecteurs
sémantiques pour matcher des chunks pertinents.

## Constat à l'audit

L'écart était plus petit que prévu :

- `.env.test` est **déjà** sur `C7_EMBEDDING_ADAPTER=ollama` /
  `embedding_model_id=all-minilm` / `dimension=384`.
- `tests/integration/c7_memory/test_real_embedder.py` existe et valide
  end-to-end le retrieval réel (top-1 = expected source sur un mini-
  corpus cat-vs-rocket).
- Le seul écart restant : `.env.example` pointait encore sur
  `deterministic-hash` (production default = adapter test-only). Donc
  un déploiement prod aujourd'hui aurait shippé du hash.

## Décision (semantic env change, CLAUDE.md §4.6)

**Switch `.env.example` C7_EMBEDDING_*** :

```diff
-C7_EMBEDDING_ADAPTER=deterministic-hash
-C7_EMBEDDING_DIMENSION=128
-C7_EMBEDDING_MODEL_ID=deterministic-hash-v1
+C7_EMBEDDING_ADAPTER=ollama
+C7_EMBEDDING_DIMENSION=384
+C7_EMBEDDING_MODEL_ID=all-minilm
```

**Rationale** : production-grade RAG exige des vecteurs sémantiques.
Ollama avec `all-minilm` (384-dim, ~46 MB local) est :

- déjà dans la stack compose (l'image est pull au lancement) ;
- déjà testé en integration via `test_real_embedder.py` ;
- gratuit (pas d'API provider) ;
- déterministe pour un même texte (vector identique sur 2 calls).

`deterministic-hash` reste accessible via override env pour les unit
tests rapides.

## Section commentaire ajoutée

`.env.example` C7 a maintenant un commentaire bloc qui explicite :
- Le défaut de production = `ollama all-minilm 384`.
- Le `deterministic-hash` est test-only (pas de retrieval sémantique).
- Le switch d'adapter est une **architectural decision** (§4.6) qui
  doit tracer SESSION-STATE §3.

## Validation

`ay_platform_core/scripts/run_tests.sh ci` :

```
==> Running ruff check     → ruff: OK
==> Running mypy           → mypy: OK
==> Running pytest         → pytest: OK (1121 passed in 123s)
==> All stages OK
```

Plus une exécution dirigée des tests `slow` real-embedder :

```
tests/integration/c7_memory/test_real_embedder.py
  test_ollama_embedder_returns_consistent_vectors          PASSED
  test_ollama_embedder_distinguishes_topics                PASSED
  test_ingest_and_retrieve_with_real_embedder              PASSED
3 passed in 25.48s
```

→ `top-1 = src-cat` quand on query "Where do pet cats usually sleep?"
contre un corpus {cat, rocket}. Retrieval sémantique réel valide.

Conteneurs orphelins post-CI : 0.

## Files modifiés

- `.env.example` (C7 section, semantic switch documenté).

Aucun changement code — l'infrastructure d'adapter pluggable existait
déjà. Phase C était **principalement de la dette de configuration**.

## Lessons

- **Production defaults vs test defaults** : les deux files
  (`.env.example` et `.env.test`) doivent être audités séparément.
  `.env.test` peut tracker plus serré l'intention production que
  `.env.example` lui-même quand le repo a évolué — c'est ce qui
  s'était passé ici.
- **Pluggable adapter pattern** : C7 a un sélecteur via env var
  pour son embedder. Une fois en place, switcher la prod défaut est
  une edit d'un fichier. Pattern à reproduire pour les autres choix
  pluggables (LLM provider, vectordb backend, etc.).

## Suite

Phase B (upload + parsers PDF/MD/HTML/DOCX) — c'est elle qui
fournira un vrai corpus à embedder. Sans Phase B, seul l'endpoint
`POST /api/v1/memory/projects/{p}/sources` (texte pre-parsé) marche.
Le user upload réel passe par Phase B.

## Rollback

Branche `main` HEAD avant : commit post-Phase A. Rollback safe via
`git revert` — single-file change. Si retour temporaire au hash voulu,
override via env var `C7_EMBEDDING_ADAPTER=deterministic-hash` (sans
toucher `.env.example`).
