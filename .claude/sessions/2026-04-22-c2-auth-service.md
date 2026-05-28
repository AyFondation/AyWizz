# Session — C2 Auth Service implementation

**Date:** 2026-04-22
**Duration:** 2 sessions (split by context compaction)
**Outcome:** C2 Auth Service fully implemented and all canonical reports green.

---

## What was done

### Étape 1, premier composant — C2 Auth Service

Implemented the full C2 Auth Service from scratch, including:

**Source files created:**
- `src/ay_platform_core/c2_auth/__init__.py` — package, re-exports
- `src/ay_platform_core/c2_auth/config.py` — `AuthConfig(BaseSettings)`, pydantic-settings with env-var aliases
- `src/ay_platform_core/c2_auth/models.py` — `JWTClaims` (E-100-001), `RBACGlobalRole`/`RBACProjectRole` (E-100-002), `UserPublic`, `UserInternal`, `LoginRequest`, `TokenResponse`, `UserCreateRequest`, `UserUpdateRequest`, `ResetPasswordRequest`, `SessionInfo`, `AuthConfigResponse`
- `src/ay_platform_core/c2_auth/db/repository.py` — `AuthRepository` (async wrappers over python-arango via `asyncio.to_thread()`)
- `src/ay_platform_core/c2_auth/modes/base.py` — `AuthMode(ABC)`
- `src/ay_platform_core/c2_auth/modes/none_mode.py` — john.doe stub; guards production/staging
- `src/ay_platform_core/c2_auth/modes/local_mode.py` — argon2id + ArangoDB account lock (AQL atomic)
- `src/ay_platform_core/c2_auth/modes/sso_mode.py` — HTTP 501 stub (oauth2-proxy not deployed)
- `src/ay_platform_core/c2_auth/service.py` — `AuthService` facade + `get_service()` FastAPI dependency
- `src/ay_platform_core/c2_auth/router.py` — `APIRouter`, 12 endpoints under `/auth`

**Tests created (109 total):**
- `tests/unit/c2_auth/` — 5 files: jwt_issuance, jwt_verification, local_mode, none_mode, rbac_models
- `tests/contract/c2_auth/` — 3 files: endpoint_contracts, jwt_schema, rbac_schema
- `tests/integration/c2_auth/` — 3 files: local_login_flow, account_lock, none_mode_integration
- `tests/fixtures/contract_registry.py` — 4 C2 contracts registered (JWTClaims, LoginRequest, TokenResponse, UserPublic)

**Infrastructure changes:**
- `pyproject.toml` — added 6 auth dependencies (fastapi, python-multipart, uvicorn, pyjwt[crypto], cryptography, argon2-cffi)
- `tests/conftest.py` — added TESTCONTAINERS_HOST_OVERRIDE for Docker-in-Docker devcontainer

---

## Key decisions made

| ID | Decision |
|---|---|
| A-1 | `pyjwt[crypto]` + `cryptography`. pyjwt does NOT validate `jti`; manual check against `c2_sessions` in `verify_token()`. |
| A-2 | `argon2-cffi` direct (not passlib — maintenance mode). |
| A-3 | SSO mode = HTTP 501 stub; no integration tests. oauth2-proxy not deployed. |
| A-4 | Account lock in ArangoDB (not in-memory). Resilient to restart + horizontal scaling. |
| A-5 | Collection names: `c2_users`, `c2_tenants`, `c2_role_assignments`, `c2_sessions`. |
| A-6 | `verify_token()` checks `c2_sessions` on every call (O(1) primary key read). |
| A-7 | `asyncio.to_thread()` wraps python-arango. Public interface is async; migration to python-arango-async will be transparent. |

---

## Obstacles encountered and solved

| Problem | Root cause | Fix |
|---|---|---|
| ArangoDB testcontainer unreachable (172.17.0.1) | Docker-in-Docker: container can't reach Docker bridge gateway | `TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal` when `REMOTE_CONTAINERS=true` |
| Python 3.12 vs 3.13 version mismatch | Devcontainer runs 3.12.13; pyproject requires ≥3.13 | `pip install --ignore-requires-python` |
| `test_verify_without_token_returns_403` → got 401 | FastAPI 0.136+ HTTPBearer returns 401 (RFC 7235 correct) | Test corrected to expect 401 |
| `AuthConfig(auth_mode=...)` mypy errors | pydantic-settings generates `__init__` using aliases; field names rejected by mypy | Use `AuthConfig.model_validate({...})` everywhere in tests |
| `from arango import ArangoClient` → `attr-defined` | python-arango doesn't declare explicit `__all__` | `# type: ignore[attr-defined]` |
| `list(cursor)` → `arg-type` (arango AQL) | `aql.execute()` returns `Cursor|AsyncJob|BatchJob|None`; mypy doesn't narrow | `# type: ignore[arg-type]` at each call site |
| ruff B904 — raise in except without `from` | Missing `from None` / `from exc` in several except blocks | Added to local_mode.py and service.py |
| ruff PLC0415 — imports not at top-level | Lazy imports inside test methods and fixtures | Moved all imports to file-level |
| ruff RUF012 — mutable class attribute default | `EXPECTED_*` class attrs in contract test classes | Annotated with `ClassVar[set[str]]` |

---

## Final report metrics

```
ruff:  OK
mypy:  OK (0 errors, 39 files checked)
pytest: 109 passed in 12.78s
coverage: 81% overall (100% models/config/modes, 90% repository, 70% router, 63% service)
```

Low router/service coverage is expected: user-management and session-admin endpoints
require authenticated requests; covered via integration tests for the critical paths only.
