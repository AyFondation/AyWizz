// =============================================================================
// File: page.tsx
// Version: 2
// Path: ay_platform_ui/app/(protected)/admin/embedding-registry/page.tsx
// Description: Platform EMBEDDING model registry admin surface (platform_manager
//              only), parallel to the LLM registry. A model references a
//              PROVIDER (endpoint + protocol + key, managed on the Embedding
//              providers page) by its stable id, and declares an upstream model
//              + a fixed output DIMENSION. No cost / capabilities / quality —
//              embeddings only carry a dimension. Create / edit / delete.
// =============================================================================

"use client";

import type React from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/app/auth-provider";
import { useReadyConfig } from "@/app/providers";
import { ApiClient, ApiError, apiErrorDetail } from "@/lib/apiClient";
import type {
  EmbeddingModelPublic,
  EmbeddingModelUpsert,
  EmbeddingProviderPublic,
} from "@/lib/types";

type FormTarget = EmbeddingModelPublic | "new" | null;

export default function EmbeddingRegistryPage() {
  const cfg = useReadyConfig();
  const { state: authState } = useAuth();
  const apiClient = useMemo(() => new ApiClient(cfg), [cfg]);

  const [models, setModels] = useState<EmbeddingModelPublic[] | null>(null);
  const [providers, setProviders] = useState<EmbeddingProviderPublic[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [form, setForm] = useState<FormTarget>(null);

  const isPlatformManager = useMemo(() => {
    if (authState.status !== "authenticated") return false;
    return (authState.claims.roles ?? []).includes("platform_manager");
  }, [authState]);

  const reload = useCallback(() => {
    apiClient
      .listEmbeddingModels()
      .then((r) => setModels(r.models))
      .catch((err) =>
        setError(err instanceof ApiError ? `Load failed (${err.status})` : "Load failed."),
      );
    apiClient
      .listEmbeddingProviders()
      .then((r) => setProviders(r.providers))
      .catch(() => {
        /* providers feed the form; a failure surfaces on save */
      });
  }, [apiClient]);

  useEffect(() => {
    if (isPlatformManager) reload();
  }, [isPlatformManager, reload]);

  const providerName = useCallback(
    (id: string) => providers.find((p) => p.provider_id === id)?.name ?? id,
    [providers],
  );

  const run = useCallback(
    async (fn: () => Promise<unknown>, ok: string) => {
      setError(null);
      setNotice(null);
      try {
        await fn();
        setNotice(ok);
        reload();
      } catch (err) {
        // Prefer the backend's actual reason (e.g. "embedding model … is
        // selected by 1 project(s): [...]; reassign them first") over a
        // hardcoded guess that mislabels a delete-guard 409 as an alias clash.
        const detail = apiErrorDetail(err);
        if (detail) setError(detail);
        else if (err instanceof ApiError && err.status === 422)
          setError("Unknown provider — pick an existing embedding provider.");
        else setError(err instanceof ApiError ? `Failed (${err.status})` : "Failed.");
      }
    },
    [reload],
  );

  const onSave = useCallback(
    (existing: EmbeddingModelPublic | null, body: EmbeddingModelUpsert) =>
      run(
        () =>
          existing
            ? apiClient.updateEmbeddingModel(existing.model_id, body)
            : apiClient.createEmbeddingModel(body),
        `Saved ${body.alias}.`,
      ).then(() => setForm(null)),
    [apiClient, run],
  );

  if (!isPlatformManager) {
    return (
      <main className="mx-auto max-w-6xl px-6 py-10">
        <p
          className="rounded border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm text-neutral-600"
          data-testid="embedding-registry-forbidden"
        >
          The embedding registry is restricted to platform administrators (platform_manager).
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-6xl px-6 py-10" data-testid="embedding-registry">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-lg font-semibold text-neutral-800">Embedding registry</h2>
          <p className="mt-2 max-w-2xl text-sm text-neutral-600">
            Embedding models the platform can call. Each maps an internal <strong>alias</strong> to
            an upstream model on a <strong>provider</strong>, with a fixed output{" "}
            <strong>dimension</strong>. Renaming a model never breaks the tenants using it.
          </p>
        </div>
        {form === null && (
          <button
            type="button"
            onClick={() => setForm("new")}
            disabled={providers.length === 0}
            title={providers.length === 0 ? "Create an embedding provider first" : undefined}
            className="shrink-0 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            data-testid="embedding-registry-add"
          >
            + Add a model
          </button>
        )}
      </div>

      {providers.length === 0 && models !== null && (
        <p className="mt-3 text-sm text-amber-700" data-testid="embedding-registry-no-providers">
          No embedding providers yet — create one under <strong>Embedding providers</strong> first.
        </p>
      )}
      {error && (
        <p
          className="mt-3 text-sm text-red-700"
          role="alert"
          data-testid="embedding-registry-error"
        >
          {error}
        </p>
      )}
      {notice && (
        <p
          className="mt-3 text-sm text-emerald-700"
          role="status"
          data-testid="embedding-registry-notice"
        >
          {notice}
        </p>
      )}

      {form !== null && (
        <ModelForm
          existing={form === "new" ? null : form}
          providers={providers}
          onSave={onSave}
          onCancel={() => setForm(null)}
        />
      )}

      {models === null ? (
        <p className="mt-4 text-sm text-neutral-500">Loading registry…</p>
      ) : (
        <table
          className="mt-4 w-full border-collapse text-sm"
          data-testid="embedding-registry-table"
        >
          <thead>
            <tr className="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500">
              <th className="px-3 py-2">Model</th>
              <th className="px-3 py-2">Provider</th>
              <th className="px-3 py-2">Dimension</th>
              <th className="px-3 py-2">Enabled</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr
                key={m.model_id}
                className="border-b border-neutral-100"
                data-testid={`embedding-registry-row-${m.alias}`}
              >
                <td className="px-3 py-2">
                  <div className="font-medium text-neutral-800">{m.alias}</div>
                  <div className="text-xs text-neutral-500">{m.upstream_model}</div>
                </td>
                <td className="px-3 py-2 text-neutral-700">{providerName(m.provider_id)}</td>
                <td className="px-3 py-2 text-neutral-700">{m.dimension}</td>
                <td className="px-3 py-2">
                  <span className={m.enabled ? "text-emerald-700" : "text-neutral-400"}>
                    {m.enabled ? "on" : "off"}
                  </span>
                </td>
                <td className="px-3 py-2">
                  <div className="flex gap-1">
                    <button
                      type="button"
                      onClick={() => {
                        setForm(m);
                        setError(null);
                        setNotice(null);
                      }}
                      className="rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50"
                      data-testid={`embedding-registry-edit-${m.alias}`}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        run(() => apiClient.deleteEmbeddingModel(m.model_id), `Deleted ${m.alias}.`)
                      }
                      className="rounded-md border border-red-200 px-2 py-1 text-xs text-red-700 hover:bg-red-50"
                      data-testid={`embedding-registry-delete-${m.alias}`}
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

const INPUT = "mt-1 w-full rounded-md border border-neutral-300 px-2 py-1 text-sm";

function Field({
  label,
  help,
  children,
}: {
  label: string;
  help: string;
  children: React.ReactNode;
}) {
  return (
    // biome-ignore lint/a11y/noLabelWithoutControl: control is passed as children inside the label
    <label className="block">
      <span className="text-xs font-medium text-neutral-700">{label}</span>
      {children}
      <span className="mt-0.5 block text-[11px] leading-tight text-neutral-400">{help}</span>
    </label>
  );
}

function ModelForm({
  existing,
  providers,
  onSave,
  onCancel,
}: {
  existing: EmbeddingModelPublic | null;
  providers: EmbeddingProviderPublic[];
  onSave: (e: EmbeddingModelPublic | null, body: EmbeddingModelUpsert) => void;
  onCancel: () => void;
}) {
  const editing = existing !== null;
  const [alias, setAlias] = useState(existing?.alias ?? "");
  const [providerId, setProviderId] = useState(
    existing?.provider_id ?? providers[0]?.provider_id ?? "",
  );
  const [upstream, setUpstream] = useState(existing?.upstream_model ?? "");
  const [dimension, setDimension] = useState(String(existing?.dimension ?? 384));
  const [enabled, setEnabled] = useState(existing?.enabled ?? true);

  const dim = Number(dimension);
  const valid = alias.trim() && providerId && upstream.trim() && dim >= 1;

  const submit = () =>
    onSave(existing, {
      alias: alias.trim(),
      provider_id: providerId,
      upstream_model: upstream.trim(),
      dimension: dim,
      enabled,
    });

  return (
    <section
      className="mt-4 rounded-md border border-blue-200 bg-blue-50/40 p-4"
      data-testid="embedding-registry-form"
    >
      <h3 className="text-sm font-semibold text-neutral-800">
        {editing ? `Edit ${existing.alias}` : "Add a model"}
      </h3>
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field
          label="Alias"
          help="Internal name tenants & projects reference. Unique; can be renamed safely (the model id stays stable)."
        >
          <input
            className={INPUT}
            value={alias}
            onChange={(e) => setAlias(e.target.value)}
            placeholder="e.g. all-minilm-384"
            data-testid="embedding-registry-form-alias"
          />
        </Field>
        <Field
          label="Provider"
          help="The endpoint + protocol this model uses. Manage providers under Embedding providers."
        >
          <select
            className={INPUT}
            value={providerId}
            onChange={(e) => setProviderId(e.target.value)}
            data-testid="embedding-registry-form-provider"
          >
            {providers.map((p) => (
              <option key={p.provider_id} value={p.provider_id}>
                {p.name} ({p.adapter})
              </option>
            ))}
          </select>
        </Field>
        <Field
          label="Upstream model"
          help="The model id AT the provider, e.g. all-minilm or text-embedding-3-small (no provider prefix)."
        >
          <input
            className={INPUT}
            value={upstream}
            onChange={(e) => setUpstream(e.target.value)}
            placeholder="text-embedding-3-small"
            data-testid="embedding-registry-form-upstream"
          />
        </Field>
        <Field
          label="Dimension"
          help="The model's output vector size. MUST match what the endpoint returns — a project's whole index shares one dimension."
        >
          <input
            type="number"
            className={INPUT}
            value={dimension}
            onChange={(e) => setDimension(e.target.value)}
            data-testid="embedding-registry-form-dimension"
          />
        </Field>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-4">
        <label className="flex items-center gap-1.5 text-xs text-neutral-700">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            data-testid="embedding-registry-form-enabled"
          />{" "}
          enabled
        </label>
      </div>
      <div className="mt-4 flex gap-2">
        <button
          type="button"
          disabled={!valid}
          onClick={submit}
          className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          data-testid="embedding-registry-form-submit"
        >
          {editing ? "Save changes" : "Create model"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50"
          data-testid="embedding-registry-form-cancel"
        >
          Cancel
        </button>
      </div>
    </section>
  );
}
