// =============================================================================
// File: page.tsx
// Version: 1
// Path: ay_platform_ui/app/(protected)/operator/quotas/page.tsx
// Description: Global LLM quota policy console (platform operator,
//              tenant_manager — Lot 3). Edit the single platform-wide policy
//              (rolling windows; per-window limits in cost AND/OR tokens +
//              warn threshold, all parametrable) and inspect any tenant's live
//              status (usage / limit / % / ok·warn·exceeded). Soft → hard:
//              an exceeded window blocks the tenant's LLM calls at the gateway.
// =============================================================================

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/app/auth-provider";
import { useReadyConfig } from "@/app/providers";
import { ApiClient, ApiError } from "@/lib/apiClient";
import type { QuotaLevel, QuotaState, QuotaStatus, QuotaWindow } from "@/lib/types";

const _LEVELS: { level: QuotaLevel; label: string; hint: string }[] = [
  { level: "global", label: "Global", hint: "platform aggregate" },
  { level: "tenant", label: "Tenant", hint: "default per tenant" },
  { level: "project", label: "Project", hint: "default per project" },
  { level: "user", label: "User", hint: "default per user (e.g. ~$173 ≈ 160€)" },
];

function numOrNull(v: string): number | null {
  const t = v.trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

const STATE_CLASS: Record<QuotaState, string> = {
  ok: "text-emerald-700",
  warn: "text-amber-700",
  exceeded: "text-red-700",
};

export default function QuotasAdminPage() {
  const cfg = useReadyConfig();
  const { state: authState } = useAuth();
  const apiClient = useMemo(() => new ApiClient(cfg), [cfg]);

  const [windows, setWindows] = useState<QuotaWindow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [tenantId, setTenantId] = useState("");
  const [status, setStatus] = useState<QuotaStatus | null>(null);

  const isTenantManager = useMemo(() => {
    if (authState.status !== "authenticated") return false;
    return (authState.claims.roles ?? []).includes("tenant_manager");
  }, [authState]);

  const reload = useCallback(() => {
    apiClient
      .getQuotaPolicy()
      .then((p) => setWindows(p.windows))
      .catch((err) =>
        setError(err instanceof ApiError ? `Load failed (${err.status})` : "Load failed."),
      );
  }, [apiClient]);

  useEffect(() => {
    if (isTenantManager) reload();
  }, [isTenantManager, reload]);

  const patchWindow = useCallback((i: number, change: Partial<QuotaWindow>) => {
    setWindows((ws) => (ws ? ws.map((w, j) => (j === i ? { ...w, ...change } : w)) : ws));
  }, []);

  const patchLimit = useCallback(
    (i: number, level: QuotaLevel, dim: "max_cost_usd" | "max_tokens", value: number | null) => {
      setWindows((ws) =>
        ws
          ? ws.map((w, j) => {
              if (j !== i) return w;
              const existing = w.limits[level] ?? { max_cost_usd: null, max_tokens: null };
              return { ...w, limits: { ...w.limits, [level]: { ...existing, [dim]: value } } };
            })
          : ws,
      );
    },
    [],
  );

  const save = useCallback(async () => {
    if (!windows) return;
    setError(null);
    setNotice(null);
    try {
      await apiClient.putQuotaPolicy(windows);
      setNotice("Policy saved.");
    } catch (err) {
      setError(err instanceof ApiError ? `Save failed (${err.status})` : "Save failed.");
    }
  }, [apiClient, windows]);

  const checkStatus = useCallback(async () => {
    setError(null);
    try {
      setStatus(await apiClient.getQuotaStatus(tenantId.trim()));
    } catch (err) {
      setError(err instanceof ApiError ? `Status failed (${err.status})` : "Status failed.");
    }
  }, [apiClient, tenantId]);

  if (!isTenantManager) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10">
        <p
          className="rounded border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm text-neutral-600"
          data-testid="quotas-forbidden"
        >
          Quota management is restricted to platform administrators (tenant_manager).
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-10" data-testid="quotas-admin">
      <h2 className="text-lg font-semibold text-neutral-800">Global LLM quotas</h2>
      <p className="mt-2 text-sm text-neutral-600">
        One policy for everyone. Each window caps usage at four levels — global (platform), tenant,
        project, user — in cost and/or tokens; a call is blocked if ANY level is exceeded (a warning
        fires first). Caps are clamped so a narrower level can&apos;t permit more than a wider one.
        Session anchors to first use; week/month to the calendar.
      </p>

      {error && (
        <p className="mt-3 text-sm text-red-700" role="alert" data-testid="quotas-error">
          {error}
        </p>
      )}
      {notice && (
        <p className="mt-3 text-sm text-emerald-700" role="status" data-testid="quotas-notice">
          {notice}
        </p>
      )}

      {windows === null ? (
        <p className="mt-4 text-sm text-neutral-500">Loading policy…</p>
      ) : (
        <>
          <div className="mt-4 space-y-4">
            {windows.map((w, i) => (
              <section
                key={w.key}
                className="rounded-md border border-neutral-200 p-4"
                data-testid={`quota-window-${w.key}`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <span className="font-medium text-neutral-800">{w.label}</span>
                    <span className="ml-2 text-xs text-neutral-400">
                      anchor: {w.anchor.replace("_", " ")}
                    </span>
                  </div>
                  <label className="text-xs text-neutral-600">
                    Warn %
                    <input
                      type="number"
                      className="ml-1 w-16 rounded-md border border-neutral-300 px-2 py-1"
                      value={w.warn_threshold_pct}
                      onChange={(e) =>
                        patchWindow(i, { warn_threshold_pct: Number(e.target.value) || 0 })
                      }
                      data-testid={`quota-warn-${w.key}`}
                    />
                  </label>
                </div>
                <table className="mt-3 w-full border-collapse text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wide text-neutral-500">
                      <th className="px-2 py-1">Level</th>
                      <th className="px-2 py-1">Max cost ($)</th>
                      <th className="px-2 py-1">Max tokens</th>
                    </tr>
                  </thead>
                  <tbody>
                    {_LEVELS.map(({ level, label, hint }) => (
                      <tr key={level} className="border-t border-neutral-100">
                        <td className="px-2 py-1">
                          <span className="text-neutral-700">{label}</span>
                          <span className="ml-1 text-[11px] text-neutral-400">{hint}</span>
                        </td>
                        <td className="px-2 py-1">
                          <input
                            type="number"
                            className="w-24 rounded-md border border-neutral-300 px-2 py-1"
                            placeholder="∞"
                            value={w.limits[level]?.max_cost_usd ?? ""}
                            onChange={(e) =>
                              patchLimit(i, level, "max_cost_usd", numOrNull(e.target.value))
                            }
                            data-testid={`quota-${w.key}-${level}-cost`}
                          />
                        </td>
                        <td className="px-2 py-1">
                          <input
                            type="number"
                            className="w-28 rounded-md border border-neutral-300 px-2 py-1"
                            placeholder="∞"
                            value={w.limits[level]?.max_tokens ?? ""}
                            onChange={(e) =>
                              patchLimit(i, level, "max_tokens", numOrNull(e.target.value))
                            }
                            data-testid={`quota-${w.key}-${level}-tokens`}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            ))}
          </div>
          <button
            type="button"
            onClick={save}
            className="mt-3 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
            data-testid="quotas-save"
          >
            Save policy
          </button>
        </>
      )}

      <section className="mt-10 border-t border-neutral-200 pt-6">
        <h3 className="text-sm font-semibold text-neutral-800">Tenant status</h3>
        <div className="mt-3 flex gap-2" data-testid="quotas-status-form">
          <input
            className="w-56 rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
            placeholder="tenant id"
            value={tenantId}
            onChange={(e) => setTenantId(e.target.value)}
            data-testid="quotas-status-tenant"
          />
          <button
            type="button"
            disabled={!tenantId.trim()}
            onClick={checkStatus}
            className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-50 disabled:opacity-50"
            data-testid="quotas-status-check"
          >
            Check
          </button>
        </div>

        {status && (
          <div className="mt-4" data-testid="quotas-status-result">
            <p className="text-sm">
              <span className="text-neutral-500">{status.tenant_id}:</span>{" "}
              <span
                className={
                  status.blocked
                    ? "text-red-700"
                    : status.warned
                      ? "text-amber-700"
                      : "text-emerald-700"
                }
                data-testid="quotas-status-verdict"
              >
                {status.blocked ? "BLOCKED" : status.warned ? "warning" : "ok"}
              </span>
            </p>
            <table className="mt-2 w-full border-collapse text-sm">
              <tbody>
                {status.windows.map((w) => (
                  <tr
                    key={w.key}
                    className="border-b border-neutral-100"
                    data-testid={`quota-status-${w.key}`}
                  >
                    <td className="px-2 py-2 text-neutral-700">{w.label}</td>
                    <td className="px-2 py-2 text-neutral-600">
                      ${w.usage_cost_usd.toFixed(2)}
                      {w.max_cost_usd !== null ? ` / $${w.max_cost_usd}` : ""}
                      {w.cost_pct !== null ? ` (${w.cost_pct}%)` : ""}
                    </td>
                    <td className="px-2 py-2 text-neutral-600">
                      {w.usage_tokens} tok
                      {w.max_tokens !== null ? ` / ${w.max_tokens}` : ""}
                    </td>
                    <td className={`px-2 py-2 font-medium ${STATE_CLASS[w.state]}`}>{w.state}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}
