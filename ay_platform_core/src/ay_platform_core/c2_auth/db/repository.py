# =============================================================================
# File: repository.py
# Version: 3
# Path: ay_platform_core/src/ay_platform_core/c2_auth/db/repository.py
# Description: ArangoDB access layer for C2-owned collections.
#              All public methods are async, wrapping python-arango
#              (synchronous) with asyncio.to_thread(). R-100-012.
#
#              v3: per-user `c2_user_preferences` (trigram + user prompt
#              override, keyed by `user_id`) and project-level
#              `system_prompt` field (override stored on the project
#              document). New helpers: `get_user_preferences` /
#              `upsert_user_preferences` and `update_project`.
#
#              v2: Tenant + Project + role-grant CRUD added (Phase A of
#              the v1 functional plan). New collection `c2_projects`.
#
# @relation implements:R-100-012
# =============================================================================

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from arango import ArangoClient  # type: ignore[attr-defined]
from arango.database import StandardDatabase

from ay_platform_core.c2_auth.models import SessionInfo, UserInternal

# C2-owned ArangoDB collections (prefixed to avoid cross-component collisions)
COLL_USERS = "c2_users"
COLL_TENANTS = "c2_tenants"
COLL_PROJECTS = "c2_projects"
COLL_ROLE_ASSIGNMENTS = "c2_role_assignments"
COLL_SESSIONS = "c2_sessions"
COLL_USER_PREFERENCES = "c2_user_preferences"
# Per-project secrets (Gitea service-account creds, future mirror
# tokens, …). Keyed by project_id. v1 stores plaintext ; Q-100-020
# tracks the prod upgrade to a KMS / vault.
COLL_PROJECT_SECRETS = "c2_project_secrets"


class AuthRepository:
    """Async data-access layer for C2 Auth Service.

    Wraps python-arango synchronous calls via asyncio.to_thread() so the
    FastAPI event loop is never blocked. The public interface is fully async;
    migrate to python-arango-async at this boundary when needed.
    """

    def __init__(self, db: StandardDatabase) -> None:
        self._db = db

    @classmethod
    def from_config(
        cls,
        url: str,
        db_name: str,
        username: str,
        password: str,
    ) -> AuthRepository:
        client = ArangoClient(hosts=url)
        db: StandardDatabase = client.db(db_name, username=username, password=password)
        return cls(db)

    # ---- Initialisation -----------------------------------------------------

    def _ensure_collections_sync(self) -> None:
        for name in (COLL_USERS, COLL_TENANTS, COLL_PROJECTS,
                     COLL_ROLE_ASSIGNMENTS, COLL_SESSIONS,
                     COLL_USER_PREFERENCES, COLL_PROJECT_SECRETS):
            if not self._db.has_collection(name):
                self._db.create_collection(name)

    async def ensure_collections(self) -> None:
        """Create C2-owned collections if absent. Safe to call multiple times."""
        await asyncio.to_thread(self._ensure_collections_sync)

    # ---- Users --------------------------------------------------------------

    def _get_user_by_username_sync(self, username: str) -> UserInternal | None:
        cursor = self._db.aql.execute(
            "FOR u IN @@col FILTER u.username == @username LIMIT 1 RETURN u",
            bind_vars={"@col": COLL_USERS, "username": username},
        )
        docs = list(cursor)  # type: ignore[arg-type]
        if not docs:
            return None
        return UserInternal.model_validate(docs[0])

    async def get_user_by_username(self, username: str) -> UserInternal | None:
        return await asyncio.to_thread(self._get_user_by_username_sync, username)

    def _get_user_by_id_sync(self, user_id: str) -> UserInternal | None:
        doc: dict[str, Any] | None = self._db.collection(COLL_USERS).get(user_id)  # type: ignore[assignment]
        if doc is None:
            return None
        return UserInternal.model_validate(doc)

    async def get_user_by_id(self, user_id: str) -> UserInternal | None:
        return await asyncio.to_thread(self._get_user_by_id_sync, user_id)

    def _insert_user_sync(self, user: UserInternal) -> None:
        doc = user.model_dump(mode="json")
        doc["_key"] = user.user_id
        self._db.collection(COLL_USERS).insert(doc)

    async def insert_user(self, user: UserInternal) -> None:
        await asyncio.to_thread(self._insert_user_sync, user)

    def _update_user_sync(self, user_id: str, patch: dict[str, Any]) -> None:
        self._db.collection(COLL_USERS).update({"_key": user_id, **patch})

    async def update_user(self, user_id: str, patch: dict[str, Any]) -> None:
        await asyncio.to_thread(self._update_user_sync, user_id, patch)

    def _increment_failed_attempts_sync(self, user_id: str) -> int:
        """Atomic AQL increment — avoids TOCTOU under concurrent login attempts."""
        result = self._db.aql.execute(
            """
            FOR u IN @@col
                FILTER u._key == @key
                UPDATE u WITH {failed_attempts: u.failed_attempts + 1} IN @@col
                RETURN NEW.failed_attempts
            """,
            bind_vars={"@col": COLL_USERS, "key": user_id},
        )
        counts = list(result)  # type: ignore[arg-type]
        return int(counts[0]) if counts else 0

    async def increment_failed_attempts(self, user_id: str) -> int:
        return await asyncio.to_thread(self._increment_failed_attempts_sync, user_id)

    def _reset_failed_attempts_sync(self, user_id: str) -> None:
        self._db.collection(COLL_USERS).update(
            {"_key": user_id, "failed_attempts": 0, "locked_until": None}
        )

    async def reset_failed_attempts(self, user_id: str) -> None:
        await asyncio.to_thread(self._reset_failed_attempts_sync, user_id)

    def _lock_user_sync(self, user_id: str, locked_until: datetime) -> None:
        self._db.collection(COLL_USERS).update(
            {"_key": user_id, "locked_until": locked_until.isoformat()}
        )

    async def lock_user(self, user_id: str, locked_until: datetime) -> None:
        await asyncio.to_thread(self._lock_user_sync, user_id, locked_until)

    # ---- Sessions -----------------------------------------------------------

    def _insert_session_sync(
        self,
        jti: str,
        user_id: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> None:
        self._db.collection(COLL_SESSIONS).insert(
            {
                "_key": jti,
                "user_id": user_id,
                "issued_at": issued_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "active": True,
            }
        )

    async def insert_session(
        self,
        jti: str,
        user_id: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> None:
        await asyncio.to_thread(self._insert_session_sync, jti, user_id, issued_at, expires_at)

    def _get_session_sync(self, jti: str) -> dict[str, Any] | None:
        result: dict[str, Any] | None = self._db.collection(COLL_SESSIONS).get(jti)  # type: ignore[assignment]
        return result

    async def get_session(self, jti: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_session_sync, jti)

    def _deactivate_session_sync(self, jti: str) -> None:
        self._db.collection(COLL_SESSIONS).update({"_key": jti, "active": False})

    async def deactivate_session(self, jti: str) -> None:
        await asyncio.to_thread(self._deactivate_session_sync, jti)

    def _list_active_sessions_sync(self) -> list[dict[str, Any]]:
        cursor = self._db.aql.execute(
            "FOR s IN @@col FILTER s.active == true SORT s.issued_at DESC RETURN s",
            bind_vars={"@col": COLL_SESSIONS},
        )
        return list(cursor)  # type: ignore[arg-type]

    async def list_active_sessions(self) -> list[SessionInfo]:
        raw = await asyncio.to_thread(self._list_active_sessions_sync)
        return [
            SessionInfo(
                session_id=s["_key"],
                user_id=s["user_id"],
                issued_at=datetime.fromisoformat(s["issued_at"]),
                expires_at=datetime.fromisoformat(s["expires_at"]),
                active=s["active"],
            )
            for s in raw
        ]

    # ---- Project scopes (RBAC) ----------------------------------------------

    def _get_project_scopes_sync(self, user_id: str) -> dict[str, list[str]]:
        cursor = self._db.aql.execute(
            "FOR r IN @@col FILTER r.user_id == @uid RETURN r",
            bind_vars={"@col": COLL_ROLE_ASSIGNMENTS, "uid": user_id},
        )
        scopes: dict[str, list[str]] = {}
        for row in cursor:  # type: ignore[union-attr]
            scopes.setdefault(row["project_id"], []).append(row["role"])
        return scopes

    async def get_project_scopes(self, user_id: str) -> dict[str, list[str]]:
        return await asyncio.to_thread(self._get_project_scopes_sync, user_id)

    def _grant_project_role_sync(
        self, user_id: str, project_id: str, role: str
    ) -> None:
        # `_key` = "{user_id}:{project_id}" so a re-grant overwrites cleanly.
        key = f"{user_id}:{project_id}"
        self._db.collection(COLL_ROLE_ASSIGNMENTS).insert(
            {"_key": key, "user_id": user_id, "project_id": project_id, "role": role},
            overwrite=True,
        )

    async def grant_project_role(
        self, user_id: str, project_id: str, role: str
    ) -> None:
        await asyncio.to_thread(
            self._grant_project_role_sync, user_id, project_id, role
        )

    def _revoke_project_role_sync(self, user_id: str, project_id: str) -> bool:
        key = f"{user_id}:{project_id}"
        coll = self._db.collection(COLL_ROLE_ASSIGNMENTS)
        if not coll.has(key):
            return False
        coll.delete(key)
        return True

    async def revoke_project_role(
        self, user_id: str, project_id: str
    ) -> bool:
        return await asyncio.to_thread(
            self._revoke_project_role_sync, user_id, project_id
        )

    # ---- Tenants ------------------------------------------------------------

    def _insert_tenant_sync(
        self, tenant_id: str, name: str, created_at: datetime
    ) -> None:
        self._db.collection(COLL_TENANTS).insert(
            {
                "_key": tenant_id,
                "name": name,
                "created_at": created_at.isoformat(),
            }
        )

    async def insert_tenant(
        self, tenant_id: str, name: str, created_at: datetime
    ) -> None:
        await asyncio.to_thread(self._insert_tenant_sync, tenant_id, name, created_at)

    def _get_tenant_sync(self, tenant_id: str) -> dict[str, Any] | None:
        doc: dict[str, Any] | None = self._db.collection(COLL_TENANTS).get(tenant_id)  # type: ignore[assignment]
        return doc

    async def get_tenant(self, tenant_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_tenant_sync, tenant_id)

    def _list_tenants_sync(self) -> list[dict[str, Any]]:
        cursor = self._db.aql.execute(
            "FOR t IN @@col SORT t._key ASC RETURN t",
            bind_vars={"@col": COLL_TENANTS},
        )
        return list(cursor)  # type: ignore[arg-type]

    async def list_tenants(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_tenants_sync)

    def _delete_tenant_sync(self, tenant_id: str) -> bool:
        coll = self._db.collection(COLL_TENANTS)
        if not coll.has(tenant_id):
            return False
        coll.delete(tenant_id)
        return True

    async def delete_tenant(self, tenant_id: str) -> bool:
        return await asyncio.to_thread(self._delete_tenant_sync, tenant_id)

    # ---- Projects -----------------------------------------------------------

    def _insert_project_sync(
        self,
        project_id: str,
        tenant_id: str,
        name: str,
        created_at: datetime,
        created_by: str,
        profile: str,
        git_repo_url: str | None,
    ) -> None:
        # `system_prompt` is intentionally omitted on insert — the absence
        # of the field is the unambiguous signal that no per-project
        # override has been set yet. PATCH /api/v1/projects/{pid} adds it.
        # `git_repo_url` (R-200-142) is set by the Gitea-provisioning
        # flow ; legacy creations (pre-Gitea) pass None and the field
        # is absent from the doc until a later provisioning pass.
        doc: dict[str, Any] = {
            "_key": project_id,
            "tenant_id": tenant_id,
            "name": name,
            "profile": profile,
            "created_at": created_at.isoformat(),
            "created_by": created_by,
        }
        if git_repo_url:
            doc["git_repo_url"] = git_repo_url
        self._db.collection(COLL_PROJECTS).insert(doc)

    async def insert_project(
        self,
        project_id: str,
        tenant_id: str,
        name: str,
        created_at: datetime,
        created_by: str,
        profile: str = "code",
        git_repo_url: str | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._insert_project_sync,
            project_id, tenant_id, name, created_at, created_by, profile,
            git_repo_url,
        )

    def _update_project_sync(self, project_id: str, patch: dict[str, Any]) -> None:
        # `_db.collection(...).update()` only writes the keys we hand it,
        # so other fields (created_at, created_by, profile, ...) stay put.
        # Removing a key requires AQL UNSET — used by the service layer
        # when system_prompt is cleared.
        self._db.collection(COLL_PROJECTS).update({"_key": project_id, **patch})

    async def update_project(self, project_id: str, patch: dict[str, Any]) -> None:
        await asyncio.to_thread(self._update_project_sync, project_id, patch)

    def _unset_project_field_sync(self, project_id: str, field: str) -> None:
        # Drop a single field from a project document so absence of the
        # key is the canonical signal that no override is set (allows
        # `system_prompt_is_default` to be derived from "key missing"
        # rather than "value == default", which is ambiguous when the
        # operator coincidentally typed the default).
        self._db.aql.execute(
            "FOR p IN @@col FILTER p._key == @key "
            "REPLACE p WITH UNSET(p, @field) IN @@col",
            bind_vars={"@col": COLL_PROJECTS, "key": project_id, "field": field},
        )

    async def unset_project_field(self, project_id: str, field: str) -> None:
        await asyncio.to_thread(self._unset_project_field_sync, project_id, field)

    def _get_project_sync(self, project_id: str) -> dict[str, Any] | None:
        doc: dict[str, Any] | None = self._db.collection(COLL_PROJECTS).get(project_id)  # type: ignore[assignment]
        return doc

    async def get_project(self, project_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_project_sync, project_id)

    def _list_projects_sync(self, tenant_id: str) -> list[dict[str, Any]]:
        cursor = self._db.aql.execute(
            "FOR p IN @@col FILTER p.tenant_id == @tid SORT p._key ASC RETURN p",
            bind_vars={"@col": COLL_PROJECTS, "tid": tenant_id},
        )
        return list(cursor)  # type: ignore[arg-type]

    async def list_projects(self, tenant_id: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_projects_sync, tenant_id)

    def _delete_project_sync(self, project_id: str) -> bool:
        coll = self._db.collection(COLL_PROJECTS)
        if not coll.has(project_id):
            return False
        coll.delete(project_id)
        # Cascade: revoke every role assignment for this project so stale
        # grants don't survive a delete-then-recreate.
        cursor = self._db.aql.execute(
            "FOR r IN @@col FILTER r.project_id == @pid RETURN r._key",
            bind_vars={"@col": COLL_ROLE_ASSIGNMENTS, "pid": project_id},
        )
        for key in list(cursor):  # type: ignore[arg-type]
            self._db.collection(COLL_ROLE_ASSIGNMENTS).delete(key)
        return True

    async def delete_project(self, project_id: str) -> bool:
        return await asyncio.to_thread(self._delete_project_sync, project_id)

    # ---- User preferences ---------------------------------------------------

    def _get_user_preferences_sync(self, user_id: str) -> dict[str, Any] | None:
        doc: dict[str, Any] | None = self._db.collection(COLL_USER_PREFERENCES).get(user_id)  # type: ignore[assignment]
        return doc

    async def get_user_preferences(self, user_id: str) -> dict[str, Any] | None:
        """Return the raw prefs document keyed by `user_id`, or None
        when nothing has been written for that user yet. The shape is
        intentionally loose so future fields can be added without a
        repository migration — the service layer normalises before
        returning to clients."""
        return await asyncio.to_thread(self._get_user_preferences_sync, user_id)

    def _upsert_user_preferences_sync(
        self, user_id: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge-upsert the prefs doc. Keys present in `patch` with a
        `None` value are DROPPED from the stored document (override
        cleared) ; non-None values overwrite."""
        coll = self._db.collection(COLL_USER_PREFERENCES)
        existing: dict[str, Any] = coll.get(user_id) or {"_key": user_id}  # type: ignore[assignment]
        merged = {k: v for k, v in existing.items() if not k.startswith("_") or k == "_key"}
        for key, value in patch.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        merged["_key"] = user_id
        if coll.has(user_id):
            # REPLACE so absent keys in `merged` actually disappear from
            # the stored doc (UPDATE would not remove fields).
            coll.replace(merged)
        else:
            coll.insert(merged)
        return merged

    async def upsert_user_preferences(
        self, user_id: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._upsert_user_preferences_sync, user_id, patch,
        )

    # ---- Project secrets (Gitea creds, future mirror tokens) ---------------

    def _upsert_project_secret_sync(
        self, project_id: str, payload: dict[str, Any],
    ) -> None:
        coll = self._db.collection(COLL_PROJECT_SECRETS)
        doc = {"_key": project_id, **payload}
        if coll.has(project_id):
            coll.replace(doc)
        else:
            coll.insert(doc)

    async def upsert_project_secret(
        self, project_id: str, payload: dict[str, Any],
    ) -> None:
        await asyncio.to_thread(
            self._upsert_project_secret_sync, project_id, payload,
        )

    def _get_project_secret_sync(self, project_id: str) -> dict[str, Any] | None:
        doc: dict[str, Any] | None = self._db.collection(  # type: ignore[assignment]
            COLL_PROJECT_SECRETS,
        ).get(project_id)
        return doc

    async def get_project_secret(
        self, project_id: str,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_project_secret_sync, project_id)

    def _delete_project_secret_sync(self, project_id: str) -> bool:
        coll = self._db.collection(COLL_PROJECT_SECRETS)
        if not coll.has(project_id):
            return False
        coll.delete(project_id)
        return True

    async def delete_project_secret(self, project_id: str) -> bool:
        return await asyncio.to_thread(self._delete_project_secret_sync, project_id)
