// =============================================================================
// File: page.tsx
// Version: 6
// Path: ay_platform_ui/app/(protected)/operator/projects/page.tsx
// Description: Project governance console (operator — E-100-002 v7).
//              platform_manager cross-tenant; admin/tenant_admin own tenant
//              (backend-scoped). Metadata-only: list projects, manage lifecycle
//              status, read + edit the ACL (grant / deactivate access), and see
//              per-project LLM COST across day..year (v7 cost dashboards, from
//              c8 quota). Exposes NO project CONTENT — content-blind.
// =============================================================================

"use client";

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/app/auth-provider";
import { useReadyConfig } from "@/app/providers";
import { ApiClient, ApiError } from "@/lib/apiClient";
import type {
  ConsumptionCell,
  Project,
  ProjectMember,
  ProjectStatus,
  ProjectStorageSeries,
  QuotaWindowStatus,
  RBACProjectRole,
} from "@/lib/types";

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

const COST_WINDOWS: { key: string; label: string }[] = [
  { key: "day", label: "Today" },
  { key: "week", label: "Week" },
  { key: "month", label: "Month" },
  { key: "quarter", label: "Quarter" },
  { key: "semester", label: "Semester" },
  { key: "year", label: "Year" },
];

const STATUS_COLOR: Record<ProjectStatus, string> = {
  active: "text-emerald-700",
  inactive: "text-red-700",
  archived: "text-amber-700",
};

const PROJECT_ROLES: RBACProjectRole[] = ["project_owner", "project_editor", "project_viewer"];

export default function ProjectsGovernancePage() {
  const cfg = useReadyConfig();
  const { state: authState } = useAuth();
  const apiClient = useMemo(() => new ApiClient(cfg), [cfg]);

  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [members, setMembers] = useState<Record<string, ProjectMember[]>>({});
  const [consumption, setConsumption] = useState<Record<string, QuotaWindowStatus[]>>({});
  const [cost, setCost] = useState<Record<string, Record<string, ConsumptionCell>>>({});
  const [currency, setCurrency] = useState("EUR");
  const [storage, setStorage] = useState<Record<string, number>>({});
  const [series, setSeries] = useState<Record<string, ProjectStorageSeries>>({});
  const [grantUser, setGrantUser] = useState("");
  const [grantRole, setGrantRole] = useState<RBACProjectRole>("project_editor");

  const money = useCallback(
    (n: number | undefined) => `${(n ?? 0).toFixed(2)} ${currency}`,
    [currency],
  );

  // Operator gate (E-100-002 v7): platform_manager (cross-tenant) OR the
  // tenant operator admin/tenant_admin (backend scopes results + actions to
  // its own tenant). Named `isTenantManager` for legacy continuity.
  const isTenantManager = useMemo(() => {
    if (authState.status !== "authenticated") return false;
    const roles = authState.claims.roles ?? [];
    return (
      roles.includes("platform_manager") ||
      roles.includes("admin") ||
      roles.includes("tenant_admin")
    );
  }, [authState]);

  // The storage metering trigger is platform_manager-only (mirrors the CronJob).
  const isPlatformManager = useMemo(() => {
    if (authState.status !== "authenticated") return false;
    return (authState.claims.roles ?? []).includes("platform_manager");
  }, [authState]);

  const reload = useCallback(() => {
    apiClient
      .listAllProjects()
      .then((r) => setProjects(r.items))
      .catch((err) =>
        setError(err instanceof ApiError ? `Load failed (${err.status})` : "Load failed."),
      );
    // Per-project cost (day..year) — backend scopes to the caller's tenant
    // (admin) or the whole platform (platform_manager).
    apiClient
      .listProjectConsumption()
      .then((rep) => {
        setCurrency(rep.currency);
        setCost(Object.fromEntries(rep.projects.map((p) => [p.project_id, p.windows])));
      })
      .catch(() => setCost({}));
    // Current per-project disk occupation (v7). 503 when metering is
    // unconfigured — degrade quietly to "—".
    apiClient
      .listProjectStorage()
      .then((rep) =>
        setStorage(Object.fromEntries(rep.projects.map((p) => [p.project_id, p.bytes]))),
      )
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
        setError(err instanceof ApiError ? `Failed (${err.status})` : "Failed.");
      }
    },
    [reload],
  );

  const snapshotNow = useCallback(async () => {
    setError(null);
    setNotice(null);
    try {
      const r = await apiClient.triggerStorageSnapshot();
      setNotice(`Storage snapshot: ${r.snapshots_written} project(s) measured.`);
      reload();
    } catch (err) {
      setError(err instanceof ApiError ? `Snapshot failed (${err.status})` : "Snapshot failed.");
    }
  }, [apiClient, reload]);

  const loadMembers = useCallback(
    (projectId: string) => {
      apiClient
        .getProjectMembers(projectId)
        .then((r) => setMembers((m) => ({ ...m, [projectId]: r.members })))
        .catch((err) =>
          setError(
            err instanceof ApiError ? `ACL load failed (${err.status})` : "ACL load failed.",
          ),
        );
    },
    [apiClient],
  );

  const loadConsumption = useCallback(
    (projectId: string, tenantId: string) => {
      apiClient
        .getQuotaStatus(tenantId, projectId)
        .then((r) => {
          // Keep only the PROJECT level of each window — the project's own
          // consumption, not the tenant/global aggregate.
          const windows = r.windows
            .map((w) => ({
              ...w,
              levels: w.levels.filter((l) => l.level === "project"),
            }))
            .filter((w) => w.levels.length > 0);
          setConsumption((c) => ({ ...c, [projectId]: windows }));
        })
        .catch(() => setConsumption((c) => ({ ...c, [projectId]: [] })));
    },
    [apiClient],
  );

  const loadSeries = useCallback(
    (projectId: string, tenantId: string) => {
      apiClient
        .getProjectStorageSeries(projectId, tenantId, "month")
        .then((s) => setSeries((m) => ({ ...m, [projectId]: s })))
        .catch(() => {});
    },
    [apiClient],
  );

  const toggleMembers = useCallback(
    (p: Project) => {
      setExpanded((cur) => {
        const next = cur === p.project_id ? null : p.project_id;
        if (next) {
          if (!members[next]) loadMembers(next);
          if (!consumption[next]) loadConsumption(next, p.tenant_id);
          if (!series[next]) loadSeries(next, p.tenant_id);
        }
        return next;
      });
      setGrantUser("");
      setGrantRole("project_editor");
    },
    [members, consumption, series, loadMembers, loadConsumption, loadSeries],
  );

  const grant = useCallback(
    async (projectId: string) => {
      const uid = grantUser.trim();
      if (!uid) return;
      setError(null);
      setNotice(null);
      try {
        const r = await apiClient.grantProjectAccess(projectId, uid, grantRole);
        setMembers((m) => ({ ...m, [projectId]: r.members }));
        setNotice(`Granted ${grantRole} to ${uid}.`);
        setGrantUser("");
      } catch (err) {
        setError(err instanceof ApiError ? `Grant failed (${err.status})` : "Grant failed.");
      }
    },
    [apiClient, grantUser, grantRole],
  );

  const revoke = useCallback(
    async (projectId: string, userId: string) => {
      setError(null);
      setNotice(null);
      try {
        const r = await apiClient.revokeProjectAccess(projectId, userId);
        setMembers((m) => ({ ...m, [projectId]: r.members }));
        setNotice(`Revoked ${userId}.`);
      } catch (err) {
        setError(err instanceof ApiError ? `Revoke failed (${err.status})` : "Revoke failed.");
      }
    },
    [apiClient],
  );

  if (!isTenantManager) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10">
        <p
          className="rounded border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm text-neutral-600"
          data-testid="projects-forbidden"
        >
          Project governance is restricted to operators (platform_manager or admin).
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-10" data-testid="projects-admin">
      <h2 className="text-lg font-semibold text-neutral-800">Projects</h2>
      <p className="mt-2 text-sm text-neutral-600">
        Projects you govern (all tenants for a platform operator; your tenant for an admin).
        Deactivating or archiving a project blocks its members from its content, and each row shows
        the project&apos;s LLM cost — governance only, no content is shown here.
      </p>

      {isPlatformManager && (
        <button
          type="button"
          onClick={snapshotNow}
          className="mt-3 rounded-md border border-neutral-300 px-3 py-1.5 text-xs font-medium text-neutral-700 hover:bg-neutral-50"
          data-testid="storage-snapshot-now"
          title="Measure every project's disk usage now and record a storage snapshot (also runs periodically)."
        >
          Snapshot storage now
        </button>
      )}

      {error && (
        <p className="mt-3 text-sm text-red-700" role="alert" data-testid="projects-error">
          {error}
        </p>
      )}
      {notice && (
        <p className="mt-3 text-sm text-emerald-700" role="status" data-testid="projects-notice">
          {notice}
        </p>
      )}

      {projects === null ? (
        <p className="mt-4 text-sm text-neutral-500">Loading projects…</p>
      ) : (
        <table className="mt-4 w-full border-collapse text-sm" data-testid="projects-table">
          <thead>
            <tr className="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500">
              <th className="px-3 py-2">Project</th>
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
              <th className="px-3 py-2" title="Current disk usage (artifacts)">
                Storage
              </th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {projects.map((p) => (
              <Fragment key={p.project_id}>
                <tr
                  className="border-b border-neutral-100"
                  data-testid={`project-row-${p.project_id}`}
                >
                  <td className="px-3 py-2">
                    <div className="font-medium text-neutral-800">{p.project_id}</div>
                    <div className="text-xs text-neutral-500">{p.name}</div>
                  </td>
                  <td className="px-3 py-2 text-neutral-600">{p.tenant_id}</td>
                  <td className="px-3 py-2">
                    <span
                      className={STATUS_COLOR[p.status]}
                      data-testid={`project-status-${p.project_id}`}
                    >
                      {p.status}
                    </span>
                  </td>
                  <td
                    className="px-3 py-2 text-neutral-700"
                    data-testid={`project-cost-day-${p.project_id}`}
                  >
                    {money(cost[p.project_id]?.day?.cost)}
                  </td>
                  <td className="px-3 py-2 text-neutral-700">
                    {money(cost[p.project_id]?.week?.cost)}
                  </td>
                  <td className="px-3 py-2 text-neutral-700">
                    {money(cost[p.project_id]?.month?.cost)}
                  </td>
                  <td
                    className="px-3 py-2 text-neutral-700"
                    data-testid={`project-storage-${p.project_id}`}
                  >
                    {p.project_id in storage ? fmtBytes(storage[p.project_id]) : "—"}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-2">
                      {p.status !== "active" && (
                        <button
                          type="button"
                          onClick={() =>
                            run(
                              () => apiClient.activateProject(p.project_id),
                              `Activated ${p.project_id}.`,
                            )
                          }
                          className="rounded-md border border-emerald-200 px-2 py-1 text-xs text-emerald-700 hover:bg-emerald-50"
                          data-testid={`project-activate-${p.project_id}`}
                        >
                          Activate
                        </button>
                      )}
                      {p.status !== "inactive" && (
                        <button
                          type="button"
                          onClick={() =>
                            run(
                              () => apiClient.deactivateProject(p.project_id),
                              `Deactivated ${p.project_id}.`,
                            )
                          }
                          className="rounded-md border border-red-200 px-2 py-1 text-xs text-red-700 hover:bg-red-50"
                          data-testid={`project-deactivate-${p.project_id}`}
                        >
                          Deactivate
                        </button>
                      )}
                      {p.status !== "archived" && (
                        <button
                          type="button"
                          onClick={() =>
                            run(
                              () => apiClient.archiveProject(p.project_id),
                              `Archived ${p.project_id}.`,
                            )
                          }
                          className="rounded-md border border-amber-200 px-2 py-1 text-xs text-amber-700 hover:bg-amber-50"
                          data-testid={`project-archive-${p.project_id}`}
                        >
                          Archive
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => toggleMembers(p)}
                        className="rounded-md border border-neutral-200 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50"
                        data-testid={`project-members-${p.project_id}`}
                      >
                        {expanded === p.project_id ? "Hide access" : "Access"}
                      </button>
                    </div>
                  </td>
                </tr>
                {expanded === p.project_id && (
                  <tr
                    className="border-b border-neutral-100 bg-neutral-50"
                    data-testid={`project-acl-${p.project_id}`}
                  >
                    <td className="px-3 py-3" colSpan={8}>
                      <div className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
                        Cost by period ({currency})
                      </div>
                      <table className="mt-1 text-sm" data-testid={`project-cost-${p.project_id}`}>
                        <tbody>
                          <tr>
                            {COST_WINDOWS.map((w) => (
                              <td key={w.key} className="pr-4 text-xs text-neutral-500">
                                {w.label}
                              </td>
                            ))}
                          </tr>
                          <tr>
                            {COST_WINDOWS.map((w) => (
                              <td key={w.key} className="pr-4 text-neutral-800">
                                {money(cost[p.project_id]?.[w.key]?.cost)}
                              </td>
                            ))}
                          </tr>
                          <tr>
                            {COST_WINDOWS.map((w) => (
                              <td key={w.key} className="pr-4 text-xs text-neutral-500">
                                {(cost[p.project_id]?.[w.key]?.tokens ?? 0).toLocaleString()} tok
                              </td>
                            ))}
                          </tr>
                        </tbody>
                      </table>

                      <div className="mt-3 text-xs font-semibold uppercase tracking-wide text-neutral-500">
                        Disk storage
                      </div>
                      {series[p.project_id] === undefined ? (
                        <p className="mt-1 text-sm text-neutral-500">Loading storage…</p>
                      ) : (
                        <div
                          className="mt-1 text-sm text-neutral-700"
                          data-testid={`project-series-${p.project_id}`}
                        >
                          <span className="font-medium">
                            {fmtBytes(series[p.project_id].current_bytes)}
                          </span>{" "}
                          <span className="text-xs text-neutral-500">
                            now ·{" "}
                            {series[p.project_id].points.length > 0
                              ? `${series[p.project_id].points.length} snapshot(s) this month`
                              : "no history yet (metering runs periodically)"}
                          </span>
                        </div>
                      )}

                      <div className="mt-3 text-xs font-semibold uppercase tracking-wide text-neutral-500">
                        Enforced quota windows
                      </div>
                      {consumption[p.project_id] === undefined ? (
                        <p className="mt-1 text-sm text-neutral-500">Loading consumption…</p>
                      ) : consumption[p.project_id].length === 0 ? (
                        <p
                          className="mt-1 text-sm text-neutral-500"
                          data-testid={`project-consumption-empty-${p.project_id}`}
                        >
                          No recorded consumption.
                        </p>
                      ) : (
                        <ul
                          className="mt-1 space-y-0.5"
                          data-testid={`project-consumption-${p.project_id}`}
                        >
                          {consumption[p.project_id].map((w) => (
                            <li key={w.key} className="text-sm text-neutral-700">
                              <span className="text-xs text-neutral-500">{w.label}: </span>$
                              {w.levels[0].usage_cost_usd.toFixed(2)} ·{" "}
                              {w.levels[0].usage_tokens.toLocaleString()} tokens
                            </li>
                          ))}
                        </ul>
                      )}

                      <div className="mt-3 text-xs font-semibold uppercase tracking-wide text-neutral-500">
                        Access-control list
                      </div>
                      {members[p.project_id] === undefined ? (
                        <p className="mt-2 text-sm text-neutral-500">Loading access…</p>
                      ) : members[p.project_id].length === 0 ? (
                        <p className="mt-2 text-sm text-neutral-500">No members.</p>
                      ) : (
                        <ul className="mt-2 space-y-1">
                          {members[p.project_id].map((m) => (
                            <li
                              key={m.user_id}
                              className="flex items-center gap-3 text-sm"
                              data-testid={`project-member-${p.project_id}-${m.user_id}`}
                            >
                              <span className="font-medium text-neutral-800">
                                {m.username || m.user_id}
                              </span>
                              <span className="text-xs text-neutral-500">{m.role}</span>
                              <button
                                type="button"
                                onClick={() => revoke(p.project_id, m.user_id)}
                                className="rounded border border-red-200 px-2 py-0.5 text-xs text-red-700 hover:bg-red-50"
                                data-testid={`project-revoke-${p.project_id}-${m.user_id}`}
                                title="Remove this user's access. Re-grant below to reactivate."
                              >
                                Deactivate access
                              </button>
                            </li>
                          ))}
                        </ul>
                      )}

                      <div className="mt-3 flex flex-wrap items-center gap-2">
                        <input
                          className="w-48 rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
                          placeholder="user id"
                          value={grantUser}
                          onChange={(e) => setGrantUser(e.target.value)}
                          data-testid={`project-grant-user-${p.project_id}`}
                        />
                        <select
                          className="rounded-md border border-neutral-300 px-2 py-1.5 text-sm"
                          value={grantRole}
                          onChange={(e) => setGrantRole(e.target.value as RBACProjectRole)}
                          data-testid={`project-grant-role-${p.project_id}`}
                        >
                          {PROJECT_ROLES.map((r) => (
                            <option key={r} value={r}>
                              {r}
                            </option>
                          ))}
                        </select>
                        <button
                          type="button"
                          disabled={!grantUser.trim()}
                          onClick={() => grant(p.project_id)}
                          className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                          data-testid={`project-grant-submit-${p.project_id}`}
                        >
                          Grant
                        </button>
                      </div>
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
