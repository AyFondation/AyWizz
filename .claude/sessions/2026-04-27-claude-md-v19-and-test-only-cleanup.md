# Session 2026-04-27 — CLAUDE.md v19 §12 + test-only cleanup

## Trigger

Suite §5 du SESSION-STATE après le CI cleanup d'hier. Deux items prioritaires :

1. **Discipline `run_tests.sh ci`** — codifier ce que la session précédente a découvert : `pytest` direct ne lance pas ruff/mypy, donc des erreurs de lint/typecheck pouvaient passer inaperçues jusqu'au CI.
2. **Investigation des 10 "test-only"** identifiés par l'audit `060-IMPLEMENTATION-STATUS.md` — séparer les vraies dettes des cas légitimes.

## CLAUDE.md v18 → v19 — Nouvelle §12

Section `Pre-commit / pre-claim Verification Discipline` ajoutée. Quatre sous-sections :

- **§12.1 — Authoritative test command** : `ay_platform_core/scripts/run_tests.sh ci` est l'unique commande qui valide la santé de la codebase. `pytest` direct reste OK pour le debugging itératif d'un test, jamais comme closing check.
- **§12.2 — Quand exécuter `run_tests.sh ci`** : avant tout claim "session complete / tests verts", avant update de `SESSION-STATE.md` §1 ou §5, avant écriture d'une journal entry "tests verts", avant production d'un message de commit. Closing discipline implicite — le user n'a pas à le demander.
- **§12.3 — Quand `run_tests.sh ci` échoue** : taxonomie A/B/C/D (cf §10.3). Ruff fails = `# noqa` avec commentaire ou fix. Mypy fails = pareil avec `# type: ignore`. Pytest fails = §10. **Aucun suppress sans commentaire justifiant**.
- **§12.4 — Couplage avec §10/§11** : §10 = test correctness, §11 = coverage quality, §12 = le gate qui surface les deux avant push. Ne relâche pas la discipline §10/§11.

L'auto-application : j'ai exécuté `run_tests.sh ci` avant de fermer cette session — "All stages OK".

## Investigation des 10 test-only

Catégorisation systématique :

| ID | Catégorie | Action |
|---|---|---|
| R-100-001 (SRP) | A — Meta-rule architecturale | Légitime — pas de fichier unique implémenteur |
| R-100-002 (footprint) | A — Meta-rule architecturale | Légitime |
| R-100-080 (uploads) | B — Marker manquant | **FIX** : marker dans `c7_memory/router.py` + workflow JSON |
| R-100-081 (C12→C7 pipeline) | B — Marker manquant | **FIX** : idem |
| R-100-113 (env coherence test) | C — Test-as-implementation | Légitime — le test EST l'enforcer |
| R-300-080..083 (C5 import endpoint) | D — WIP stub | Légitime — `status: draft`, impl est 501 stub validé par tests |
| R-300-100 | D — WIP stub | Légitime |

**Quick wins (catégorie B)** :

- `ay_platform_core/src/ay_platform_core/c7_memory/router.py` : ajout `@relation implements:R-100-080 R-100-081` (le router C7 expose POST /api/v1/memory/projects/<p>/sources qui est le côté serveur du contrat C12 → C7).
- `infra/c12_workflow/workflows/ingest_text_source.json` : ajout `@relation implements:R-100-080 R-100-081` au champ `_comment` (le workflow n8n est le côté C12 du contrat — la lib n'a pas de syntax commentaire JSON, le `_comment` est la convention).
- `audit_implementation_status.py` : extension du scan INFRA aux `*.json` (les workflows n8n).

**Documentation (catégories A, C, D)** :

Le legend du doc 060 a été étendu pour expliciter les 3 cas légitimes de "test-only" (meta-rule, test-as-impl, WIP stub). Le 4e cas — marker stale après suppression d'impl — reste celui qu'un audit réel doit catcher.

## Bilan post-fix

| Spec | Total | tested | implemented | test-only | divergent | not-yet |
|---|---|---|---|---|---|---|
| 100-SPEC | 80 | **4** (+2) | 28 | **3** (-2) | 0 | 45 |
| Total | 258 | **4** (+2) | 122 | **8** (-2) | 0 | 124 |

R-100-080/081 sont passés de `test-only` à `tested` (impl marker + validates marker).

## Validation

`ay_platform_core/scripts/run_tests.sh ci` :

```
==> Running ruff check     → ruff: OK
==> Running mypy           → mypy: OK
==> Running pytest         → pytest: OK
==> All stages OK
```

Pipeline complet vert. CLAUDE.md v19 self-applied : la session se ferme sur un `run_tests.sh ci` validé.

## Spec / governance deltas

- `CLAUDE.md` v18 → v19 (nouvelle §12 Pre-commit / pre-claim Verification Discipline).
- `ay_platform_core/scripts/checks/audit_implementation_status.py` (extension scan `*.json` + legend doc enrichi).
- `ay_platform_core/src/ay_platform_core/c7_memory/router.py` (markers R-100-080/081).
- `infra/c12_workflow/workflows/ingest_text_source.json` (markers R-100-080/081 dans `_comment`).
- `requirements/060-IMPLEMENTATION-STATUS.md` (regenerated, legend enrichi).
- `.claude/SESSION-STATE.md` (date + §6).

## Lessons (candidats `/capture-lesson`)

- **Architectural meta-rules** ne sont pas implémentables par un seul fichier. R-100-001 (SRP "every component has exactly one responsibility") et R-100-002 (footprint cap) sont validés par la STRUCTURE entière + l'observation runtime — pas par un fichier unique. L'audit `test-only` est la bonne classification pour ces cas.
- **Test-as-implementation** : certains R-* sont implémentés PAR un test (R-100-113 — l'invariant "env file ↔ Settings fields" est PAR DESIGN enforced par un test de cohérence, jamais par du code production). Marquer `@relation implements:` dans le test est correct ; le statut `test-only` reflète bien la réalité (pas d'impl src/, l'enforcer EST un test).
- **WIP stubs** marqué `status: draft` — un endpoint qui retourne 501 + un test qui vérifie le 501 = `test-only`. C'est attendu jusqu'à ce que le draft devienne approved.
- **Self-applied discipline** : §12 est appliquée immédiatement à cette session. C'est le test ultime de sa praticabilité.

## Suite

- **Q-100-015** (K8s Loki/ES adapter) — préalable aux manifests prod K8s.
- **Q-100-016** (trace propagation dans C15 Jobs) — avec C15 sub-agent runtime.
- **Production K8s manifests** (R-100-060) : Helm/raw YAML par composant.
- **Trim SESSION-STATE.md** — touche la limite 150 lignes ; archivage des entrées 2026-04-22/23 en bloc unique au prochain ajout d'entrée.

## Rollback

Branche `main` HEAD avant cette session : commit le plus récent post-CI cleanup. Rollback safe via `git revert` — pas de changement runtime, juste discipline doc + 2 markers + 1 fichier d'audit étendu.
