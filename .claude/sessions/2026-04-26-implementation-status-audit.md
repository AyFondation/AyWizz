# Session 2026-04-26 — Spec ↔ implem audit script + 060-IMPLEMENTATION-STATUS.md

## Trigger

Item #1 du SESSION-STATE §5 : "Audit spec ↔ implémentation ligne-par-ligne". Le doc `050-ARCHITECTURE-OVERVIEW.md §9` donne une vue agrégée mais ligne-par-ligne nécessite un croisement systématique entre les R-* déclarés en spec et les `@relation implements:` markers dans le code.

## Implémentation

### Script `audit_implementation_status.py`

Nouveau script `ay_platform_core/scripts/checks/audit_implementation_status.py` :

- **Parse** tous les blocs `yaml ... ```` dans `requirements/*-SPEC*.md` et extrait `id`, `version`, `status`.
- **Scan multi-target** des markers `@relation implements:` / `@relation validates:` :
  - `src/` (Python)
  - `infra/` (YAML, Dockerfile, shell)
  - `.github/workflows/` (CI YAML)
  - `tests/docker-compose.yml` (test stack infra-of-test)
  - `tests/` (Python — pour markers `validates:` et `implements:` posés sur les tests eux-mêmes)
- **Multi-id markers** : `@relation implements:R-100-001 R-100-002 R-100-003` indexe les 3.
- **Status** dérivé : `tested` (impl + validates), `implemented` (impl seul), `test-only` (validates seul), `divergent` (status=approved, aucun marker), `not-yet` (status=draft, aucun marker — attendu pour v2).
- **Output** : markdown table par spec + summary global. CLI `--write <path>` pour générer le doc, `--fail-on-divergent` pour CI gate.

Le script est ré-exécutable à volonté ; le doc `060-IMPLEMENTATION-STATUS.md` est marqué `generated-by: …` pour le rappeler.

### Markers manquants ajoutés

L'audit initial trouvait **7 divergents** R-100-* (status=approved, aucun marker). Diagnostic : ces R-* sont implémentés en infra/CI/test, hors `src/`. Deux corrections :

1. **Étendre le scan** : ajout de `infra/`, `.github/workflows/`, `tests/docker-compose.yml` aux roots ; gestion multi-id par marker.
2. **Ajouter les 5 markers manquants** :
   - `tests/coherence/test_env_completeness.py` → `R-100-113` (le test EST l'implémentation de l'invariant cohérence env).
   - `_mock_llm/main.py` → `R-100-116`.
   - `tests/docker-compose.yml` étendu → `R-100-118`, `R-100-119`, `R-100-120`, `R-100-121`, `R-100-122` (en plus des `R-100-039 R-100-100 R-100-015 R-100-114 R-100-115 R-100-117` qui y étaient déjà).
   - `.github/workflows/ci-tests.yml` → `R-100-123`.

Résultat : **0 divergent** sur tous les R-* approved.

### Doc `060-IMPLEMENTATION-STATUS.md`

Auto-généré par le script. Contenu :

- **Summary table** : par spec (100 / 200 / 300 / 400 / 700 / 800), comptes par status.
- **Per-spec tables** : chaque R-* avec ID, version, status spec, status overall, fichiers implémenteurs (3 max + counter), fichiers validateurs.

Bilan 258 R-* :

| Spec | Total | tested | implemented | test-only | divergent | not-yet |
|---|---|---|---|---|---|---|
| 100-SPEC | 80 | 2 | 28 | 5 | 0 | 45 |
| 200-SPEC | 29 | 0 | 19 | 0 | 0 | 10 |
| 300-SPEC | 52 | 0 | 29 | 5 | 0 | 18 |
| 400-SPEC | 30 | 0 | 14 | 0 | 0 | 16 |
| 700-SPEC | 20 | 0 | 20 | 0 | 0 | 0 |
| 800-SPEC | 47 | 0 | 12 | 0 | 0 | 35 |
| **Total** | **258** | **2** | **122** | **10** | **0** | **124** |

Lecture :
- 124 R-* implémentés (avec marker), 0 divergent (parfait — chaque approved a un marker).
- 124 not-yet : `status: draft` sans marker, attendu (specs en cours de population, pas encore implémentés).
- 10 test-only : cas suspects (test cite un R-* mais aucun src/infra ne l'implémente). À investiguer en session ultérieure — soit le test est en avance sur l'implémentation (anti-pattern §10.2 #4 — tests doivent valider du comportement existant), soit l'impl a été écrite sans poser le marker.

### Intégration CLAUDE.md / 050-OVERVIEW

- **CLAUDE.md v17 → v18** §3 navigation map : ajout `060-IMPLEMENTATION-STATUS.md` avec note "Re-generate via `python ay_platform_core/scripts/checks/audit_implementation_status.py …`".
- **050-ARCHITECTURE-OVERVIEW.md v4 → v5** §8 "Where to look next" : pointer vers 060 pour la vue par-requirement.

## Validation

- 672 unit/contract/coherence tests verts (inchangé — refactor purement infra).
- Audit script exécuté avec succès, doc 060 généré, 0 divergent.

## Spec / governance deltas

- `requirements/060-IMPLEMENTATION-STATUS.md` (nouveau, auto-généré v1).
- `ay_platform_core/scripts/checks/audit_implementation_status.py` (nouveau).
- `ay_platform_core/tests/coherence/test_env_completeness.py` (marker R-100-113).
- `ay_platform_core/src/ay_platform_core/_mock_llm/main.py` (marker R-100-116).
- `ay_platform_core/tests/docker-compose.yml` (markers R-100-118/119/120/121/122 ajoutés).
- `.github/workflows/ci-tests.yml` (marker R-100-123).
- `CLAUDE.md` v17 → v18 (§3 navigation map).
- `requirements/050-ARCHITECTURE-OVERVIEW.md` v4 → v5 (§8 link).
- `.claude/SESSION-STATE.md` (date + §6).

## Lessons (candidats `/capture-lesson`)

- **Multi-id markers sur une ligne** : `@relation implements:R-A R-B R-C` est plus compact qu'une ligne par R-* ; le pattern est répandu dans le compose. Tout parser de markers DOIT extraire tous les IDs après le `:`, pas juste le premier.
- **Markers en infra/CI** : un R-* peut être implémenté en YAML (compose, K8s), Dockerfile, shell, GH Actions. Restreindre le scan au `src/` Python rate ces implementations. Le scan multi-target résout le problème.
- **0 divergent comme métrique de qualité** : "tout R-* approved a au moins un marker" est une invariante facile à mesurer et à enforcer (`--fail-on-divergent` flag, futur CI gate).
- **Auto-généré + journal** : le doc 060 est régénéré, pas édité à la main. Au moindre PR qui ajoute/supprime un R-* ou un marker, on re-génère et on commit. Pourrait être un step CI `audit_implementation_status.py --fail-on-divergent` pour bloquer les PR qui laissent un R-* approved sans implem.

## Suite

- **Investigation des 10 test-only** : tests qui référencent un R-* sans implémentation correspondante. Probablement des markers stale (impl supprimée) ou des tests d'avenir (anti-pattern). Session courte (~30 min).
- **Q-100-015** (K8s Loki/ES adapter) — préalable aux manifests prod K8s.
- **Q-100-016** (trace propagation in C15 Jobs) — quand C15 sub-agent runtime sera abordé.
- **Production K8s manifests** (R-100-060) — Helm/raw YAML avec `resources.limits/requests` (R-100-119), Secrets séparés admin/app (R-100-118 v2), NetworkPolicies, HPA.

## Rollback

Branche `main` HEAD avant cette session : commit le plus récent post-test-debt-resolution. Aucun changement runtime ; tout est doc + script audit + 5 ajouts de markers (commentaires). Rollback safe via `git revert <commit>` après commit.
