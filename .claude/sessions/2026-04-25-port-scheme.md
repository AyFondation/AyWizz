# Session 2026-04-25 — Host-published port scheme (R-100-122)

## Trigger

Demande utilisateur : "il serait bien de maitriser les ports servant aux différents composants à communiquer entre eux et surtout il faudrait éviter d'utiliser des ports très connus comme le port 80, le port 443, 8080 pour éviter des collisions, peut-être en se basant sur le numero des composants, 96100 96200 96300, et si on pouvait le paramétrer : 96000 ça serait super."

## Précisions / décisions techniques

1. **Distinction host-vs-internal** : les collisions sur 80/443/8080 ne se produisent QUE sur les ports publiés sur le host. Inside le Docker network, chaque container a son propre namespace réseau ; deux services qui listen sur 0.0.0.0:8000 ne se collisionnent pas. Donc le schéma s'applique **uniquement aux ports host-publiés**, pas aux internal listen ports (8000 reste convention uvicorn).

2. **96000 invalide** : le port max TCP/UDP est 65535. Repropos é 56000 (range haut, peu usité). Validé par l'utilisateur.

3. **Option (A) explicit per-service** retenue : Compose ne fait pas d'arithmétique nativement (`${PORT_BASE} + 80` ne marche pas en interpolation YAML), donc déclarer chaque slot explicitement comme var séparée est plus propre que dériver du PORT_BASE par script externe. Override fin-grain possible si une var spécifique collide.

## Schéma R-100-122

`PORT_BASE = 56000` (paramétrable via env file).

| Offset | Service | Default | Range owner |
|---|---|---|---|
| `+0` | C1 Traefik public | 56000 | production |
| `+80` | C1 Traefik dashboard | 56080 | dev/test only |
| `+n*100` (n=1..9) | Cn direct (debug override) | 56100..56900 | dev/test only |
| `+1000` | C10 MinIO API | 57000 | reserved |
| `+1001` | C10 MinIO console | 57001 | reserved |
| `+1100` | C11 ArangoDB | 57100 | reserved |
| `+1200` | C12 n8n | 57200 | reserved |
| `+1300` | Ollama (C7 helper) | 57300 | reserved |
| `+9800` | `_mock_llm` admin | 59800 | dev/test only |
| `+9900` | `_observability` | 59900 | dev/test only |

**Naming non-numérotés** :

- Backend deps sans Cn (Ollama, futurs Redis/NATS) → `+1000..+1999`, slot contigu documenté.
- Test sidecars (préfixe `_`) → `+9000..+9999`. Convention : les 3 derniers digits mirorent le slot prod si le sidecar était la version prod. `_mock_llm` = "test C8" → +9800 (mirror du slot +800 de C8).

## Implémentation

### Modifications

- **`.env.test` v3 → v4** : ajout PORT_C1_PUBLIC, PORT_C1_DASHBOARD, PORT_MOCK_LLM, PORT_OBSERVABILITY dans le bloc (a) infra-bootstrap.
- **`.env.example` v3 → v4** : idem.
- **`docker-compose.yml` v6 → v7** :
  - `"80:80"` → `"${PORT_C1_PUBLIC}:80"`
  - `"8080:8080"` → `"${PORT_C1_DASHBOARD}:8080"`
  - `"8001:8000"` → `"${PORT_MOCK_LLM}:8000"`
  - `"8002:8000"` → `"${PORT_OBSERVABILITY}:8000"`
  - `WEBHOOK_URL: "http://localhost/"` → `"http://localhost:${PORT_C1_PUBLIC}/"` (n8n stamp).
- **`e2e_stack.sh` v3 → v4** : `STACK_BASE_URL` default `:56000`, message d'accueil enrichi (4 URLs avec annotations R-100-122).
- **`seed_e2e.py`** : `DEFAULT_BASE_URL = "http://localhost:56000"`, help mis à jour.
- **`tests/system/conftest.py` v1 → v2** : `STACK_BASE_URL` default `:56000`, `MOCK_LLM_ADMIN_URL` default `:59800`.
- **`tests/coherence/test_env_completeness.py`** : whitelist `_INFRA_BOOTSTRAP_VARS` étendue avec les 4 PORT_* (consommés par Compose, pas par Pydantic).

### Spec

- **R-100-115 v1 → v2** : "exactly one production-grade public port" (préservé) + autorisation explicite des slots test-tier dans la range BASE+9000..9999 + référence à R-100-121 pour l'interdiction prod.
- **R-100-122 nouveau** : table complète + règle de naming pour non-numérotés + rationale (collision-avoidance + déterminisme du slot par numéro de composant).
- **100-SPEC v9 → v10**.
- **050-OVERVIEW v3 → v4** : nouvelle section §7 "Default host ports" avec table.

## Validation runtime

`docker ps` : 14 services healthy avec les nouveaux ports.

Smoke tests host (depuis devcontainer via `host.docker.internal`) :

```
http://host.docker.internal:56000/auth/config             -> 200
http://host.docker.internal:56000/api/v1/conversations/health -> 401  (forward-auth gate, expected)
http://host.docker.internal:56080/api/version            -> 200  (Traefik dashboard)
http://host.docker.internal:59800/health                 -> 200  (mock_llm)
http://host.docker.internal:59900/health                 -> 200  (_observability)
http://host.docker.internal/auth/config                  -> ERR  (port 80 unbound)
http://host.docker.internal:8080/api/version             -> ERR  (port 8080 unbound)
```

Les anciens ports (80, 8080) ne sont plus bound — collision-proof contre tout autre service local sur ces ports.

## Tests

- 672 unit/contract/coherence + 21 integration = **693 verts** (inchangé — refactor purement d'infra, aucune logique Python touchée).

## Lessons (candidats `/capture-lesson`)

- **Compose YAML n'a pas d'arithmétique** : `${PORT_BASE} + 80` n'existe pas. Si on veut un schéma déterministe avec une seule var de base, il faut soit (a) déclarer chaque slot explicitement comme var indépendante (option retenue), soit (b) générer le `.env` via un script preprocess. (a) est plus simple, plus auditable, et permet l'override d'un seul slot.
- **Range valide TCP/UDP** : 0..65535. Toujours vérifier avant de proposer une base — le 96000 initial était hors-range.
- **Range 49152-65535** est éphémère (IANA) ; éviter pour des services persistants. **40000-49151** est libre dans la range "user/registered" non-claimed → 56000 est dans cette zone, mémorisable, peu collisionnable.
- **Tests system + system-of-system** : quand on change le port Traefik, il faut traverser `seed_e2e.py`, `e2e_stack.sh`, `tests/system/conftest.py`, et même `docker-compose.yml` n8n `WEBHOOK_URL`. Tout référenceur du port public doit être recensé. Grep par `:80\b\|localhost\b` aide.

## Rollback

Branche `main` HEAD avant les sessions de la journée : commit le plus récent du user (post-commit). Rollback ciblé sur ce schéma par `git revert <commit>` après commit. Aucun changement de logique métier — refactor purement infra.
