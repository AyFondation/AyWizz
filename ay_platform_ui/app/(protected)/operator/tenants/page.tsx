// =============================================================================
// File: page.tsx
// Version: 2
// Path: ay_platform_ui/app/(protected)/operator/tenants/page.tsx
// Description: Tenant management console (platform operator, platform_manager —
//              E-100-002 v3). List / create / delete tenants and
//              deactivate / reactivate them (a deactivated tenant's members
//              are refused login). v2 (E-100-002 v7): each tenant row shows its
//              LLM cost (today/week/month) + current disk storage (sum of its
//              projects). No tenant CONTENT is exposed here.
// =============================================================================

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/app/auth-provider";
import { useReadyConfig } from "@/app/providers";
import { ApiClient, ApiError } from "@/lib/apiClient";
import type { ConsumptionCell, TenantPublic } from "@/lib/types";

function fmtBytes(n: number | undefined): string {
  const b = n ?? 0;
  if (b < 1024) return `${b} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = b / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}

export default function TenantsAdminPage() {
  const cfg = useReadyConfig();
  const { state: authState } = useAuth();
  const apiClient = useMemo(() => new ApiClient(cfg), [cfg]);

  const [tenants, setTenants] = useState<TenantPublic[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [newId, setNewId] = useState("");
  const [newName, setNewName] = useState("");
  const [cost, setCost] = useState<Record<string, Record<string, ConsumptionCell>>>({});
  const [currency, setCurrency] = useState("EUR");
  const [storage, setStorage] = useState<Record<string, number>>({});

  const money = useCallback(
    (n: number | undefined) => `${(n ?? 0).toFixed(2)} ${currency}`,
    [currency],
  );

  const isTenantManager = useMemo(() => {
    if (authState.status !== "authenticated") return false;
    return (authState.claims.roles ?? []).includes("platform_manager");
  }, [authState]);

  const reload = useCallback(() => {
    apiClient
      .listTenants()
      .then((r) => setTenants(r.items))
      .catch((err) =>
        setError(err instanceof ApiError ? `Load failed (${err.status})` : "Load failed."),
      );
    apiClient
      .listTenantConsumption()
      .then((rep) => {
        setCurrency(rep.currency);
        setCost(Object.fromEntries(rep.tenants.map((t) => [t.tenant_id, t.windows])));
      })
      .catch(() => setCost({}));
    apiClient
      .listTenantStorage()
      .then((rep) => setStorage(Object.fromEntries(rep.tenants.map((t) => [t.tenant_id, t.bytes]))))
      .catch(() => setStorage({}));
  }, [apiClient]);

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
        reload();
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          setError("A tenant with that id already exists.");
        } else {
          setError(err instanceof ApiError ? `Failed (${err.status})` : "Failed.");
        }
      }
    },
    [reload],
  );

  if (!isTenantManager) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10">
        <p
          className="rounded border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm text-neutral-600"
          data-testid="tenants-forbidden"
        >
          Tenant management is restricted to platform administrators (platform_manager).
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-10" data-testid="tenants-admin">
      <h2 className="text-lg font-semibold text-neutral-800">Tenants</h2>
      <p className="mt-2 text-sm text-neutral-600">
        Platform tenants. Deactivating a tenant refuses login to all its members.
      </p>

      {error && (
        <p className="mt-3 text-sm text-red-700" role="alert" data-testid="tenants-error">
          {error}
        </p>
      )}
      {notice && (
        <p className="mt-3 text-sm text-emerald-700" role="status" data-testid="tenants-notice">
          {notice}
        </p>
      )}

      <div className="mt-4 flex flex-wrap gap-2" data-testid="tenants-create">
        <input
          className="w-48 rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
          placeholder="tenant id"
          value={newId}
          onChange={(e) => setNewId(e.target.value)}
          data-testid="tenants-create-id"
        />
        <input
          className="w-48 rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
          placeholder="display name"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          data-testid="tenants-create-name"
        />
        <button
          type="button"
          disabled={!newId.trim() || !newName.trim()}
          onClick={() => {
            run(
              () => apiClient.createTenant(newId.trim(), newName.trim()),
              `Created ${newId.trim()}.`,
            );
            setNewId("");
            setNewName("");
          }}
          className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          data-testid="tenants-create-submit"
        >
          Create tenant
        </button>
      </div>

      {tenants === null ? (
        <p className="mt-4 text-sm text-neutral-500">Loading tenants…</p>
      ) : (
        <table className="mt-4 w-full border-collapse text-sm" data-testid="tenants-table">
          <thead>
            <tr className="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500">
              <th className="px-3 py-2">Tenant</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2" title="LLM cost today">
                Today
              </th>
              <th className="px-3 py-2" title="LLM cost this week">
                Week
              </th>
              <th className="px-3 py-2" title="LLM cost this month">
                Month
              </th>
              <th className="px-3 py-2" title="Current disk usage (all projects)">
                Storage
              </th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {tenants.map((t) => (
              <tr
                key={t.tenant_id}
                className="border-b border-neutral-100"
                data-testid={`tenant-row-${t.tenant_id}`}
              >
                <td className="px-3 py-2">
                  <div className="font-medium text-neutral-800">{t.tenant_id}</div>
                  <div className="text-xs text-neutral-500">{t.name}</div>
                </td>
                <td className="px-3 py-2">
                  <span
                    className={t.active ? "text-emerald-700" : "text-red-700"}
                    data-testid={`tenant-status-${t.tenant_id}`}
                  >
                    {t.active ? "active" : "deactivated"}
                  </span>
                </td>
                <td
                  className="px-3 py-2 text-neutral-700"
                  data-testid={`tenant-cost-day-${t.tenant_id}`}
                >
                  {money(cost[t.tenant_id]?.day?.cost)}
                </td>
                <td className="px-3 py-2 text-neutral-700">
                  {money(cost[t.tenant_id]?.week?.cost)}
                </td>
                <td className="px-3 py-2 text-neutral-700">
                  {money(cost[t.tenant_id]?.month?.cost)}
                </td>
                <td
                  className="px-3 py-2 text-neutral-700"
                  data-testid={`tenant-storage-${t.tenant_id}`}
                >
                  {t.tenant_id in storage ? fmtBytes(storage[t.tenant_id]) : "—"}
                </td>
                <td className="px-3 py-2">
                  <div className="flex gap-2">
                    {t.active ? (
                      <button
                        type="button"
                        onClick={() =>
                          run(
                            () => apiClient.deactivateTenant(t.tenant_id),
                            `Deactivated ${t.tenant_id}.`,
                          )
                        }
                        className="rounded-md border border-amber-200 px-2 py-1 text-xs text-amber-700 hover:bg-amber-50"
                        data-testid={`tenant-deactivate-${t.tenant_id}`}
                      >
                        Deactivate
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() =>
                          run(
                            () => apiClient.reactivateTenant(t.tenant_id),
                            `Reactivated ${t.tenant_id}.`,
                          )
                        }
                        className="rounded-md border border-emerald-200 px-2 py-1 text-xs text-emerald-700 hover:bg-emerald-50"
                        data-testid={`tenant-reactivate-${t.tenant_id}`}
                      >
                        Reactivate
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() =>
                        run(() => apiClient.deleteTenant(t.tenant_id), `Deleted ${t.tenant_id}.`)
                      }
                      className="rounded-md border border-red-200 px-2 py-1 text-xs text-red-700 hover:bg-red-50"
                      data-testid={`tenant-delete-${t.tenant_id}`}
                    >
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
