// =============================================================================
// File: page.tsx
// Version: 1
// Path: ay_platform_ui/app/(protected)/admin/embedding-catalogue/page.tsx
// Description: Per-tenant EMBEDDING catalogue admin surface (admin /
//              tenant_admin), parallel to the LLM catalogue. Curates which
//              platform-registry embedding models the tenant's projects may
//              select, and which is the default for NEW projects. Opt-IN: the
//              catalogue starts empty; models are added from a picker populated
//              by GET /embedding-catalog/available (the registry list itself is
//              platform_manager-only). No API key is ever handled here.
// =============================================================================

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/app/auth-provider";
import { useReadyConfig } from "@/app/providers";
import { ApiClient, ApiError } from "@/lib/apiClient";
import type { EmbeddingCatalogModelPublic, EmbeddingModelPublic } from "@/lib/types";

const ADMIN_ROLES = new Set(["admin", "tenant_admin"]);

export default function EmbeddingCataloguePage() {
  const cfg = useReadyConfig();
  const { state: authState } = useAuth();
  const apiClient = useMemo(() => new ApiClient(cfg), [cfg]);

  const [models, setModels] = useState<EmbeddingCatalogModelPublic[] | null>(null);
  const [available, setAvailable] = useState<EmbeddingModelPublic[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pickModelId, setPickModelId] = useState("");

  const canEdit = useMemo(() => {
    if (authState.status !== "authenticated") return false;
    const roles = new Set(authState.claims.roles ?? []);
    for (const r of ADMIN_ROLES) if (roles.has(r)) return true;
    return false;
  }, [authState]);

  const reload = useCallback(() => {
    Promise.all([apiClient.listEmbeddingCatalog(), apiClient.listAvailableEmbeddingCatalogModels()])
      .then(([cat, avail]) => {
        setModels(cat.models);
        setAvailable(avail.models);
        setPickModelId("");
      })
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
      body: { enabled?: boolean; default_for_new_projects?: boolean },
      ok: string,
    ) => {
      setError(null);
      setNotice(null);
      try {
        await apiClient.putEmbeddingCatalogModel(modelId, body);
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
        await apiClient.deleteEmbeddingCatalogModel(modelId);
        setNotice(`Removed ${label}.`);
        reload();
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          setError("A project still uses this model — reassign it before removing.");
        } else {
          setError(err instanceof ApiError ? `Remove failed (${err.status})` : "Remove failed.");
        }
      }
    },
    [apiClient, reload],
  );

  if (!canEdit) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10">
        <p
          className="rounded border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm text-neutral-600"
          data-testid="embedding-catalogue-forbidden"
        >
          The tenant embedding catalogue is restricted to tenant administrators.
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-10" data-testid="embedding-catalogue">
      <h2 className="text-lg font-semibold text-neutral-800">Tenant embedding catalogue</h2>
      <p className="mt-2 text-sm text-neutral-600">
        Embedding models your projects may select, and the default for new projects. Add models from
        the picker below (platform models your tenant hasn't catalogued yet). Each project uses
        exactly one embedding — its whole index shares that model's dimension. Changing a project's
        model triggers an automatic re-embed.
      </p>

      {error && (
        <p
          className="mt-3 text-sm text-red-700"
          role="alert"
          data-testid="embedding-catalogue-error"
        >
          {error}
        </p>
      )}
      {notice && (
        <p
          className="mt-3 text-sm text-emerald-700"
          role="status"
          data-testid="embedding-catalogue-notice"
        >
          {notice}
        </p>
      )}

      <div className="mt-4 flex gap-2" data-testid="embedding-catalogue-add">
        <select
          value={pickModelId}
          onChange={(e) => setPickModelId(e.target.value)}
          disabled={available.length === 0}
          className="w-72 rounded-md border border-neutral-300 px-3 py-1.5 text-sm disabled:opacity-50"
          data-testid="embedding-catalogue-add-select"
        >
          <option value="">
            {available.length === 0 ? "no more models to add" : "add a model…"}
          </option>
          {available.map((m) => (
            <option key={m.model_id} value={m.model_id}>
              {m.alias} ({m.dimension}d)
            </option>
          ))}
        </select>
        <button
          type="button"
          disabled={!pickModelId}
          onClick={() => {
            const picked = available.find((m) => m.model_id === pickModelId);
            onPut(pickModelId, { enabled: true }, `Added ${picked?.alias ?? pickModelId}.`);
          }}
          className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          data-testid="embedding-catalogue-add-button"
        >
          Add
        </button>
      </div>

      {models === null ? (
        <p className="mt-4 text-sm text-neutral-500">Loading catalogue…</p>
      ) : models.length === 0 ? (
        <p className="mt-4 text-sm text-neutral-500" data-testid="embedding-catalogue-empty">
          No embedding models catalogued — projects fall back to the platform default (lexical
          hash). Add a real model from the picker above.
        </p>
      ) : (
        <table
          className="mt-4 w-full border-collapse text-sm"
          data-testid="embedding-catalogue-table"
        >
          <thead>
            <tr className="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500">
              <th className="px-3 py-2">Model</th>
              <th className="px-3 py-2">Dimension</th>
              <th className="px-3 py-2">Enabled</th>
              <th className="px-3 py-2" title="Auto-selected for new projects">
                Default for new projects
              </th>
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
  model: EmbeddingCatalogModelPublic;
  onPut: (
    modelId: string,
    body: { enabled?: boolean; default_for_new_projects?: boolean },
    ok: string,
  ) => void;
  onRemove: (modelId: string, label: string) => void;
}) {
  const alias = model.registry.alias;
  return (
    <tr className="border-b border-neutral-100" data-testid={`embedding-catalogue-row-${alias}`}>
      <td className="px-3 py-2">
        <div className="font-medium text-neutral-800">{alias}</div>
        <div className="text-xs text-neutral-500">{model.registry.upstream_model}</div>
      </td>
      <td className="px-3 py-2 text-neutral-700">{model.registry.dimension}</td>
      <td className="px-3 py-2">
        <input
          type="checkbox"
          checked={model.enabled}
          onChange={(e) => onPut(model.model_id, { enabled: e.target.checked }, `Saved ${alias}.`)}
          data-testid={`embedding-catalogue-enabled-${alias}`}
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
          data-testid={`embedding-catalogue-default-${alias}`}
        />
      </td>
      <td className="px-3 py-2">
        <button
          type="button"
          onClick={() => onRemove(model.model_id, alias)}
          className="rounded-md border border-red-200 px-2 py-1 text-xs text-red-700 hover:bg-red-50"
          data-testid={`embedding-catalogue-remove-${alias}`}
        >
          Remove
        </button>
      </td>
    </tr>
  );
}
