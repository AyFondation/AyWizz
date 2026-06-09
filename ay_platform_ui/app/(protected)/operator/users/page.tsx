// =============================================================================
// File: page.tsx
// Version: 1
// Path: ay_platform_ui/app/(protected)/admin/users/page.tsx
// Description: Cross-tenant user oversight (platform operator, tenant_manager —
//              E-100-002 v3). List users across ALL tenants, filter by tenant,
//              and deactivate / reactivate them. Read + account-status only;
//              user create/delete stays with the tenant's own admin.
// =============================================================================

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/app/auth-provider";
import { useReadyConfig } from "@/app/providers";
import { ApiClient, ApiError } from "@/lib/apiClient";
import type { UserAdminView } from "@/lib/types";

export default function UsersAdminPage() {
  const cfg = useReadyConfig();
  const { state: authState } = useAuth();
  const apiClient = useMemo(() => new ApiClient(cfg), [cfg]);

  const [users, setUsers] = useState<UserAdminView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [tenantFilter, setTenantFilter] = useState("");

  const isTenantManager = useMemo(() => {
    if (authState.status !== "authenticated") return false;
    return (authState.claims.roles ?? []).includes("tenant_manager");
  }, [authState]);

  const reload = useCallback(
    (tenantId?: string) => {
      apiClient
        .listUsersAdmin(tenantId || undefined)
        .then((r) => setUsers(r.items))
        .catch((err) =>
          setError(err instanceof ApiError ? `Load failed (${err.status})` : "Load failed."),
        );
    },
    [apiClient],
  );

  useEffect(() => {
    if (isTenantManager) reload();
  }, [isTenantManager, reload]);

  const run = useCallback(
    async (fn: () => Promise<unknown>, ok: string) => {
      setError(null);
      setNotice(null);
      try {
        await fn();
        setNotice(ok);
        reload(tenantFilter);
      } catch (err) {
        setError(err instanceof ApiError ? `Failed (${err.status})` : "Failed.");
      }
    },
    [reload, tenantFilter],
  );

  if (!isTenantManager) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10">
        <p
          className="rounded border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm text-neutral-600"
          data-testid="users-forbidden"
        >
          User oversight is restricted to platform administrators (tenant_manager).
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-10" data-testid="users-admin">
      <h2 className="text-lg font-semibold text-neutral-800">Users (all tenants)</h2>
      <p className="mt-2 text-sm text-neutral-600">
        Oversight across every tenant. You can deactivate / reactivate accounts; user creation and
        deletion stay with each tenant&apos;s own admin.
      </p>

      {error && (
        <p className="mt-3 text-sm text-red-700" role="alert" data-testid="users-error">
          {error}
        </p>
      )}
      {notice && (
        <p className="mt-3 text-sm text-emerald-700" role="status" data-testid="users-notice">
          {notice}
        </p>
      )}

      <div className="mt-4 flex gap-2" data-testid="users-filter">
        <input
          className="w-56 rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
          placeholder="filter by tenant id (blank = all)"
          value={tenantFilter}
          onChange={(e) => setTenantFilter(e.target.value)}
          data-testid="users-filter-input"
        />
        <button
          type="button"
          onClick={() => reload(tenantFilter)}
          className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-50"
          data-testid="users-filter-apply"
        >
          Apply
        </button>
      </div>

      {users === null ? (
        <p className="mt-4 text-sm text-neutral-500">Loading users…</p>
      ) : users.length === 0 ? (
        <p className="mt-4 text-sm text-neutral-500" data-testid="users-empty">
          No users found.
        </p>
      ) : (
        <table className="mt-4 w-full border-collapse text-sm" data-testid="users-table">
          <thead>
            <tr className="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500">
              <th className="px-3 py-2">User</th>
              <th className="px-3 py-2">Tenant</th>
              <th className="px-3 py-2">Roles</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr
                key={u.user_id}
                className="border-b border-neutral-100"
                data-testid={`user-row-${u.user_id}`}
              >
                <td className="px-3 py-2">
                  <div className="font-medium text-neutral-800">{u.username}</div>
                  {u.email && <div className="text-xs text-neutral-500">{u.email}</div>}
                </td>
                <td className="px-3 py-2 text-neutral-700">{u.tenant_id}</td>
                <td className="px-3 py-2 text-xs text-neutral-600">
                  {u.roles.length ? u.roles.join(", ") : "—"}
                </td>
                <td className="px-3 py-2">
                  <span
                    className={u.status === "active" ? "text-emerald-700" : "text-red-700"}
                    data-testid={`user-status-${u.user_id}`}
                  >
                    {u.status}
                  </span>
                </td>
                <td className="px-3 py-2">
                  {u.status === "active" ? (
                    <button
                      type="button"
                      onClick={() =>
                        run(() => apiClient.deactivateUser(u.user_id), `Deactivated ${u.username}.`)
                      }
                      className="rounded-md border border-amber-200 px-2 py-1 text-xs text-amber-700 hover:bg-amber-50"
                      data-testid={`user-deactivate-${u.user_id}`}
                    >
                      Deactivate
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() =>
                        run(() => apiClient.reactivateUser(u.user_id), `Reactivated ${u.username}.`)
                      }
                      className="rounded-md border border-emerald-200 px-2 py-1 text-xs text-emerald-700 hover:bg-emerald-50"
                      data-testid={`user-reactivate-${u.user_id}`}
                    >
                      Reactivate
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
