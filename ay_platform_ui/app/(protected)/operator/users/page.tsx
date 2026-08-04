// =============================================================================
// File: page.tsx
// Version: 4
// Path: ay_platform_ui/app/(protected)/operator/users/page.tsx
// Description: User oversight (platform operator, E-100-002 v7). platform_manager
//              sees ALL tenants (with a tenant filter); admin / tenant_admin sees
//              its OWN tenant only (backend-scoped, tenant-aware chrome, no
//              filter). Deactivate / reactivate accounts; expand a row to see the
//              user's PROJECT ACCESS; each row shows the user's LLM cost
//              (today/week/month). Read + status only; user create/delete stays
//              with the tenant's own admin.
// =============================================================================

"use client";

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/app/auth-provider";
import { useReadyConfig } from "@/app/providers";
import { ApiClient, ApiError } from "@/lib/apiClient";
import type { ConsumptionCell, UserAdminView, UserProjectAccess } from "@/lib/types";

export default function UsersAdminPage() {
  const cfg = useReadyConfig();
  const { state: authState } = useAuth();
  const apiClient = useMemo(() => new ApiClient(cfg), [cfg]);

  const [users, setUsers] = useState<UserAdminView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [tenantFilter, setTenantFilter] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [access, setAccess] = useState<Record<string, UserProjectAccess[]>>({});
  const [cost, setCost] = useState<Record<string, Record<string, ConsumptionCell>>>({});
  const [currency, setCurrency] = useState("EUR");

  const money = useCallback(
    (n: number | undefined) => `${(n ?? 0).toFixed(2)} ${currency}`,
    [currency],
  );

  // platform_manager is the CROSS-TENANT operator (sees every tenant + filter);
  // admin / tenant_admin is the TENANT-SCOPED operator (own tenant only).
  const roles = useMemo(
    () => (authState.status === "authenticated" ? (authState.claims.roles ?? []) : []),
    [authState],
  );
  const isPlatformManager = roles.includes("platform_manager");
  const isTenantOperator = roles.includes("admin") || roles.includes("tenant_admin");
  const isOperator = isPlatformManager || isTenantOperator;
  const ownTenant = authState.status === "authenticated" ? (authState.claims.tenant_id ?? "") : "";

  const reload = useCallback(
    (tenantId?: string) => {
      apiClient
        .listUsersAdmin(tenantId || undefined)
        .then((r) => setUsers(r.items))
        .catch((err) =>
          setError(err instanceof ApiError ? `Load failed (${err.status})` : "Load failed."),
        );
      // Per-user LLM cost — backend scopes to the caller's tenant (admin) or
      // the whole platform (platform_manager).
      apiClient
        .listUserConsumption(tenantId || undefined)
        .then((rep) => {
          setCurrency(rep.currency);
          setCost(Object.fromEntries(rep.users.map((u) => [u.user_id, u.windows])));
        })
        .catch(() => setCost({}));
    },
    [apiClient],
  );

  useEffect(() => {
    if (isOperator) reload();
  }, [isOperator, reload]);

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

  const toggleAccess = useCallback(
    (userId: string) => {
      setExpanded((cur) => (cur === userId ? null : userId));
      if (access[userId]) return;
      apiClient
        .listUserProjectAccess(userId)
        .then((r) => setAccess((a) => ({ ...a, [userId]: r.items })))
        .catch((err) =>
          setError(err instanceof ApiError ? `Access load failed (${err.status})` : "Failed."),
        );
    },
    [apiClient, access],
  );

  if (!isOperator) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10">
        <p
          className="rounded border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm text-neutral-600"
          data-testid="users-forbidden"
        >
          User oversight is restricted to operators (platform_manager or admin).
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-10" data-testid="users-admin">
      <h2 className="text-lg font-semibold text-neutral-800">
        {isPlatformManager ? "Users (all tenants)" : `Users — ${ownTenant}`}
      </h2>
      <p className="mt-2 text-sm text-neutral-600">
        {isPlatformManager
          ? "Oversight across every tenant."
          : `Users in your tenant (${ownTenant}).`}{" "}
        You can deactivate / reactivate accounts and inspect each user&apos;s project access; user
        creation and deletion stay with the tenant&apos;s own admin.
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

      {isPlatformManager && (
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
      )}

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
              <th className="px-3 py-2">Global role</th>
              <th className="px-3 py-2">Project access</th>
              <th className="px-3 py-2" title="LLM cost today">
                Today
              </th>
              <th className="px-3 py-2" title="LLM cost this week">
                Week
              </th>
              <th className="px-3 py-2" title="LLM cost this month">
                Month
              </th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <Fragment key={u.user_id}>
                <tr className="border-b border-neutral-100" data-testid={`user-row-${u.user_id}`}>
                  <td className="px-3 py-2">
                    <div className="font-medium text-neutral-800">{u.username}</div>
                    {u.email && <div className="text-xs text-neutral-500">{u.email}</div>}
                  </td>
                  <td className="px-3 py-2 text-neutral-700">{u.tenant_id}</td>
                  <td className="px-3 py-2 text-xs text-neutral-600">
                    {u.roles.length ? u.roles.join(", ") : "—"}
                  </td>
                  <td className="px-3 py-2">
                    <button
                      type="button"
                      onClick={() => toggleAccess(u.user_id)}
                      className="rounded-md border border-neutral-200 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50"
                      data-testid={`user-access-toggle-${u.user_id}`}
                    >
                      {expanded === u.user_id ? "Hide" : "View"} projects
                    </button>
                  </td>
                  <td
                    className="px-3 py-2 text-neutral-700"
                    data-testid={`user-cost-day-${u.user_id}`}
                  >
                    {money(cost[u.user_id]?.day?.cost)}
                  </td>
                  <td className="px-3 py-2 text-neutral-700">
                    {money(cost[u.user_id]?.week?.cost)}
                  </td>
                  <td className="px-3 py-2 text-neutral-700">
                    {money(cost[u.user_id]?.month?.cost)}
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
                          run(
                            () => apiClient.deactivateUser(u.user_id),
                            `Deactivated ${u.username}.`,
                          )
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
                          run(
                            () => apiClient.reactivateUser(u.user_id),
                            `Reactivated ${u.username}.`,
                          )
                        }
                        className="rounded-md border border-emerald-200 px-2 py-1 text-xs text-emerald-700 hover:bg-emerald-50"
                        data-testid={`user-reactivate-${u.user_id}`}
                      >
                        Reactivate
                      </button>
                    )}
                  </td>
                </tr>
                {expanded === u.user_id && (
                  <tr
                    className="border-b border-neutral-100 bg-neutral-50"
                    data-testid={`user-access-${u.user_id}`}
                  >
                    <td className="px-3 py-2 text-xs text-neutral-600" colSpan={9}>
                      {access[u.user_id] === undefined ? (
                        "Loading project access…"
                      ) : access[u.user_id].length === 0 ? (
                        "No project access — this user is not a member of any project."
                      ) : (
                        <ul className="flex flex-wrap gap-2">
                          {access[u.user_id].map((a) => (
                            <li
                              key={`${a.project_id}:${a.role}`}
                              className="rounded border border-neutral-200 bg-white px-2 py-1"
                            >
                              <span className="font-medium text-neutral-800">
                                {a.project_name || a.project_id}
                              </span>{" "}
                              <span className="text-neutral-500">
                                — {a.role.replace("project_", "")}
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
