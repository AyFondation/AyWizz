// =============================================================================
// File: page.tsx
// Version: 1
// Path: ay_platform_ui/app/(protected)/admin/llm-catalogue/page.tsx
// Description: Per-tenant LLM catalogue admin surface (LLM-governance HMI,
//              admin / tenant_admin). Curates which platform-registry models
//              the tenant's projects may use: enable/disable, set an optional
//              chargeback markup, remove, or add a model by alias. No API key
//              is ever handled here (keys are platform secrets).
// =============================================================================

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/app/auth-provider";
import { useReadyConfig } from "@/app/providers";
import { ApiClient, ApiError } from "@/lib/apiClient";
import type { TenantCatalogModelPublic } from "@/lib/types";

const ADMIN_ROLES = new Set(["admin", "tenant_admin"]);

export default function LlmCataloguePage() {
  const cfg = useReadyConfig();
  const { state: authState } = useAuth();
  const apiClient = useMemo(() => new ApiClient(cfg), [cfg]);

  const [models, setModels] = useState<TenantCatalogModelPublic[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [newAlias, setNewAlias] = useState("");

  const canEdit = useMemo(() => {
    if (authState.status !== "authenticated") return false;
    const roles = new Set(authState.claims.roles ?? []);
    for (const r of ADMIN_ROLES) if (roles.has(r)) return true;
    return false;
  }, [authState]);

  const reload = useCallback(() => {
    apiClient
      .listLlmCatalog()
      .then((r) => setModels(r.models))
      .catch((err) =>
        setError(err instanceof ApiError ? `Load failed (${err.status})` : "Load failed."),
      );
  }, [apiClient]);

  useEffect(() => {
    if (canEdit) reload();
  }, [canEdit, reload]);

  const onPut = useCallback(
    async (
      modelId: string,
      body: {
        enabled?: boolean;
        markup_pct?: number | null;
        default_for_new_projects?: boolean;
      },
      ok: string,
    ) => {
      setError(null);
      setNotice(null);
      try {
        await apiClient.putLlmCatalogModel(modelId, body);
        setNotice(ok);
        reload();
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          setError(`Unknown model id '${modelId}' (not in the platform registry).`);
        } else {
          setError(err instanceof ApiError ? `Save failed (${err.status})` : "Save failed.");
        }
      }
    },
    [apiClient, reload],
  );

  const onRemove = useCallback(
    async (modelId: string, label: string) => {
      setError(null);
      setNotice(null);
      try {
        await apiClient.deleteLlmCatalogModel(modelId);
        setNotice(`Removed ${label}.`);
        reload();
      } catch (err) {
        setError(err instanceof ApiError ? `Remove failed (${err.status})` : "Remove failed.");
      }
    },
    [apiClient, reload],
  );

  if (!canEdit) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10">
        <p
          className="rounded border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm text-neutral-600"
          data-testid="catalogue-forbidden"
        >
          The tenant LLM catalogue is restricted to tenant administrators.
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-10" data-testid="llm-catalogue">
      <h2 className="text-lg font-semibold text-neutral-800">Tenant LLM catalogue</h2>
      <p className="mt-2 text-sm text-neutral-600">
        Models your projects may use, curated from the platform registry. Keys stay on the platform
        — none are handled here.
      </p>

      {error && (
        <p className="mt-3 text-sm text-red-700" role="alert" data-testid="catalogue-error">
          {error}
        </p>
      )}
      {notice && (
        <p className="mt-3 text-sm text-emerald-700" role="status" data-testid="catalogue-notice">
          {notice}
        </p>
      )}

      <div className="mt-4 flex gap-2" data-testid="catalogue-add">
        <input
          type="text"
          value={newAlias}
          onChange={(e) => setNewAlias(e.target.value)}
          placeholder="add model by id (from your platform admin)"
          className="w-72 rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
          data-testid="catalogue-add-input"
        />
        <button
          type="button"
          disabled={!newAlias}
          onClick={() => {
            onPut(newAlias.trim(), { enabled: true }, `Added ${newAlias.trim()}.`);
            setNewAlias("");
          }}
          className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          data-testid="catalogue-add-button"
        >
          Add
        </button>
      </div>

      {models === null ? (
        <p className="mt-4 text-sm text-neutral-500">Loading catalogue…</p>
      ) : models.length === 0 ? (
        <p className="mt-4 text-sm text-neutral-500" data-testid="catalogue-empty">
          No models catalogued yet — add one by its id above.
        </p>
      ) : (
        <table className="mt-4 w-full border-collapse text-sm" data-testid="catalogue-table">
          <thead>
            <tr className="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500">
              <th className="px-3 py-2">Model</th>
              <th className="px-3 py-2">Default quality</th>
              <th className="px-3 py-2">Enabled</th>
              <th className="px-3 py-2" title="Auto-added to new projects">
                Default for new projects
              </th>
              <th className="px-3 py-2">Markup %</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <CatalogueRow key={m.model_id} model={m} onPut={onPut} onRemove={onRemove} />
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}

function CatalogueRow({
  model,
  onPut,
  onRemove,
}: {
  model: TenantCatalogModelPublic;
  onPut: (
    modelId: string,
    body: {
      enabled?: boolean;
      markup_pct?: number | null;
      default_for_new_projects?: boolean;
    },
    ok: string,
  ) => void;
  onRemove: (modelId: string, label: string) => void;
}) {
  const [markup, setMarkup] = useState(model.markup_pct?.toString() ?? "");
  const alias = model.registry.alias;
  return (
    <tr className="border-b border-neutral-100" data-testid={`catalogue-row-${alias}`}>
      <td className="px-3 py-2">
        <div className="font-medium text-neutral-800">{alias}</div>
        <div className="text-xs text-neutral-500">{model.registry.upstream_model}</div>
      </td>
      <td className="px-3 py-2 text-neutral-700">{model.registry.default_model_quality}</td>
      <td className="px-3 py-2">
        <input
          type="checkbox"
          checked={model.enabled}
          onChange={(e) => onPut(model.model_id, { enabled: e.target.checked }, `Saved ${alias}.`)}
          data-testid={`catalogue-enabled-${alias}`}
        />
      </td>
      <td className="px-3 py-2">
        <input
          type="checkbox"
          checked={model.default_for_new_projects}
          onChange={(e) =>
            onPut(
              model.model_id,
              { enabled: model.enabled, default_for_new_projects: e.target.checked },
              `Saved ${alias}.`,
            )
          }
          data-testid={`catalogue-default-${alias}`}
        />
      </td>
      <td className="px-3 py-2">
        <div className="flex gap-1">
          <input
            type="number"
            value={markup}
            onChange={(e) => setMarkup(e.target.value)}
            placeholder="—"
            className="w-20 rounded-md border border-neutral-300 px-2 py-1 text-xs"
            data-testid={`catalogue-markup-${alias}`}
          />
          <button
            type="button"
            onClick={() =>
              onPut(
                model.model_id,
                { enabled: model.enabled, markup_pct: markup === "" ? null : Number(markup) },
                `Saved ${alias}.`,
              )
            }
            className="rounded-md border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-50"
            data-testid={`catalogue-markup-save-${alias}`}
          >
            Save
          </button>
        </div>
      </td>
      <td className="px-3 py-2">
        <button
          type="button"
          onClick={() => onRemove(model.model_id, alias)}
          className="rounded-md border border-red-200 px-2 py-1 text-xs text-red-700 hover:bg-red-50"
          data-testid={`catalogue-remove-${alias}`}
        >
          Remove
        </button>
      </td>
    </tr>
  );
}
