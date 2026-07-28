// =============================================================================
// File: page.tsx
// Version: 4
// Path: ay_platform_ui/app/(protected)/admin/llm-registry/page.tsx
// Description: Platform LLM MODEL registry admin surface (platform_manager only).
//              v4 (provider normalisation + stable ids): a model references a
//              PROVIDER (endpoint + key, managed on the Providers page) and is
//              addressed by a stable model_id — renaming the alias never breaks
//              tenant/project references. The form picks a provider from a
//              dropdown; the endpoint URL + API key are NOT here (they live on
//              the provider). Sortable table, default quality ascending.
// =============================================================================

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/app/auth-provider";
import { useReadyConfig } from "@/app/providers";
import { ApiClient, ApiError } from "@/lib/apiClient";
import type {
  LLMModelUpsert,
  LLMProviderPublic,
  LLMRegistryPublic,
  ModelQuality,
} from "@/lib/types";

const QUALITIES: ModelQuality[] = ["low", "medium", "high"];
const QUALITY_ORDER: Record<ModelQuality, number> = { low: 0, medium: 1, high: 2 };

type SortKey = "alias" | "provider_id" | "provider_cost_in_per_1m" | "default_model_quality";
type SortDir = "asc" | "desc";

function compareModels(a: LLMRegistryPublic, b: LLMRegistryPublic, key: SortKey): number {
  if (key === "default_model_quality") {
    return QUALITY_ORDER[a.default_model_quality] - QUALITY_ORDER[b.default_model_quality];
  }
  if (key === "provider_cost_in_per_1m") {
    return a.provider_cost_in_per_1m - b.provider_cost_in_per_1m;
  }
  return String(a[key]).localeCompare(String(b[key]));
}

type FormTarget = LLMRegistryPublic | "new" | null;

export default function LlmRegistryPage() {
  const cfg = useReadyConfig();
  const { state: authState } = useAuth();
  const apiClient = useMemo(() => new ApiClient(cfg), [cfg]);

  const [models, setModels] = useState<LLMRegistryPublic[] | null>(null);
  const [providers, setProviders] = useState<LLMProviderPublic[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [form, setForm] = useState<FormTarget>(null);
  const [sortKey, setSortKey] = useState<SortKey>("default_model_quality");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const isTenantManager = useMemo(() => {
    if (authState.status !== "authenticated") return false;
    return (authState.claims.roles ?? []).includes("platform_manager");
  }, [authState]);

  const reload = useCallback(() => {
    apiClient
      .listLlmRegistry()
      .then((r) => setModels(r.models))
      .catch((err) =>
        setError(err instanceof ApiError ? `Load failed (${err.status})` : "Load failed."),
      );
    apiClient
      .listLlmProviders()
      .then((r) => setProviders(r.providers))
      .catch(() => {
        /* providers are needed for the form; a failure surfaces on save */
      });
  }, [apiClient]);

  useEffect(() => {
    if (isTenantManager) reload();
  }, [isTenantManager, reload]);

  const providerName = useCallback(
    (id: string) => providers.find((p) => p.provider_id === id)?.name ?? id,
    [providers],
  );

  const sorted = useMemo(() => {
    if (models === null) return null;
    const out = [...models].sort((a, b) => compareModels(a, b, sortKey));
    return sortDir === "desc" ? out.reverse() : out;
  }, [models, sortKey, sortDir]);

  const onSort = useCallback(
    (key: SortKey) => {
      if (key === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
      else {
        setSortKey(key);
        setSortDir("asc");
      }
    },
    [sortKey],
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
        if (err instanceof ApiError && err.status === 409)
          setError("A model with that alias already exists.");
        else setError(err instanceof ApiError ? `Failed (${err.status})` : "Failed.");
      }
    },
    [reload],
  );

  const onSave = useCallback(
    (existing: LLMRegistryPublic | null, body: LLMModelUpsert) =>
      run(
        () =>
          existing
            ? apiClient.updateLlmRegistryModel(existing.model_id, body)
            : apiClient.createLlmRegistryModel(body),
        `Saved ${body.alias}.`,
      ).then(() => setForm(null)),
    [apiClient, run],
  );

  if (!isTenantManager) {
    return (
      <main className="mx-auto max-w-6xl px-6 py-10">
        <p
          className="rounded border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm text-neutral-600"
          data-testid="registry-forbidden"
        >
          The platform LLM registry is restricted to platform administrators (platform_manager).
        </p>
      </main>
    );
  }

  const arrow = (key: SortKey) => (key === sortKey ? (sortDir === "asc" ? " ▲" : " ▼") : "");

  return (
    <main className="mx-auto max-w-6xl px-6 py-10" data-testid="llm-registry">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-lg font-semibold text-neutral-800">Platform LLM registry</h2>
          <p className="mt-2 max-w-2xl text-sm text-neutral-600">
            Models the platform can call. Each maps an internal <strong>alias</strong> to a model on
            a <strong>provider</strong> (endpoint + key, managed under Providers), with a list
            price, capabilities, and a default <strong>quality band</strong>. Renaming a model never
            breaks the tenants using it.
          </p>
        </div>
        {form === null && (
          <button
            type="button"
            onClick={() => setForm("new")}
            disabled={providers.length === 0}
            title={providers.length === 0 ? "Create a provider first" : undefined}
            className="shrink-0 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            data-testid="registry-add"
          >
            + Add a model
          </button>
        )}
      </div>

      {providers.length === 0 && models !== null && (
        <p className="mt-3 text-sm text-amber-700" data-testid="registry-no-providers">
          No providers yet — create one under <strong>Providers</strong> before adding a model.
        </p>
      )}
      {error && (
        <p className="mt-3 text-sm text-red-700" role="alert" data-testid="registry-error">
          {error}
        </p>
      )}
      {notice && (
        <p className="mt-3 text-sm text-emerald-700" role="status" data-testid="registry-notice">
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

      {sorted === null ? (
        <p className="mt-4 text-sm text-neutral-500">Loading registry…</p>
      ) : (
        <table className="mt-4 w-full border-collapse text-sm" data-testid="registry-table">
          <thead>
            <tr className="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500">
              <th
                className="cursor-pointer px-3 py-2"
                onClick={() => onSort("alias")}
                data-testid="registry-sort-alias"
              >
                Model{arrow("alias")}
              </th>
              <th
                className="cursor-pointer px-3 py-2"
                onClick={() => onSort("provider_id")}
                data-testid="registry-sort-provider"
              >
                Provider{arrow("provider_id")}
              </th>
              <th
                className="cursor-pointer px-3 py-2"
                onClick={() => onSort("provider_cost_in_per_1m")}
                data-testid="registry-sort-cost"
              >
                Cost in/out (/1M){arrow("provider_cost_in_per_1m")}
              </th>
              <th
                className="cursor-pointer px-3 py-2"
                onClick={() => onSort("default_model_quality")}
                data-testid="registry-sort-quality"
              >
                Quality{arrow("default_model_quality")}
              </th>
              <th className="px-3 py-2">Enabled</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((m) => (
              <tr
                key={m.model_id}
                className="border-b border-neutral-100"
                data-testid={`registry-row-${m.alias}`}
              >
                <td className="px-3 py-2">
                  <div className="font-medium text-neutral-800">{m.alias}</div>
                  <div className="text-xs text-neutral-500">{m.upstream_model}</div>
                  <div className="mt-0.5 text-[11px] text-neutral-400">
                    {m.capabilities.vision ? "vision · " : ""}
                    {m.capabilities.tool_calling ? "tools · " : ""}
                    {m.capabilities.context_window.toLocaleString()} ctx
                  </div>
                </td>
                <td className="px-3 py-2 text-neutral-700">{providerName(m.provider_id)}</td>
                <td className="px-3 py-2 text-neutral-700">
                  ${m.provider_cost_in_per_1m} / ${m.provider_cost_out_per_1m}
                </td>
                <td className="px-3 py-2 text-neutral-700">{m.default_model_quality}</td>
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
                      data-testid={`registry-edit-${m.alias}`}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        run(
                          () => apiClient.deleteLlmRegistryModel(m.model_id),
                          `Deleted ${m.alias}.`,
                        )
                      }
                      className="rounded-md border border-red-200 px-2 py-1 text-xs text-red-700 hover:bg-red-50"
                      data-testid={`registry-delete-${m.alias}`}
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
  existing: LLMRegistryPublic | null;
  providers: LLMProviderPublic[];
  onSave: (e: LLMRegistryPublic | null, body: LLMModelUpsert) => void;
  onCancel: () => void;
}) {
  const editing = existing !== null;
  const [alias, setAlias] = useState(existing?.alias ?? "");
  const [providerId, setProviderId] = useState(
    existing?.provider_id ?? providers[0]?.provider_id ?? "",
  );
  const [upstream, setUpstream] = useState(existing?.upstream_model ?? "");
  const [quality, setQuality] = useState<ModelQuality>(existing?.default_model_quality ?? "low");
  const [costIn, setCostIn] = useState(String(existing?.provider_cost_in_per_1m ?? 0));
  const [costOut, setCostOut] = useState(String(existing?.provider_cost_out_per_1m ?? 0));
  const [ctx, setCtx] = useState(String(existing?.capabilities.context_window ?? 200000));
  const [vision, setVision] = useState(existing?.capabilities.vision ?? false);
  const [tools, setTools] = useState(existing?.capabilities.tool_calling ?? true);
  const [enabled, setEnabled] = useState(existing?.enabled ?? true);

  const valid = alias.trim() && providerId && upstream.trim();

  const submit = () =>
    onSave(existing, {
      alias: alias.trim(),
      provider_id: providerId,
      upstream_model: upstream.trim(),
      capabilities: { vision, tool_calling: tools, context_window: Number(ctx) || 1 },
      provider_cost_in_per_1m: Number(costIn) || 0,
      provider_cost_out_per_1m: Number(costOut) || 0,
      default_model_quality: quality,
      enabled,
    });

  return (
    <section
      className="mt-4 rounded-md border border-blue-200 bg-blue-50/40 p-4"
      data-testid="registry-form"
    >
      <h3 className="text-sm font-semibold text-neutral-800">
        {editing ? `Edit ${existing.alias}` : "Add a model"}
      </h3>
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field
          label="Alias"
          help="Internal name projects & catalogues reference. Unique; can be renamed safely (the model id stays stable)."
        >
          <input
            className={INPUT}
            value={alias}
            onChange={(e) => setAlias(e.target.value)}
            placeholder="e.g. claude-haiku-fast"
            data-testid="registry-form-alias"
          />
        </Field>
        <Field
          label="Provider"
          help="The endpoint + key this model uses. Manage providers under the Providers page."
        >
          <select
            className={INPUT}
            value={providerId}
            onChange={(e) => setProviderId(e.target.value)}
            data-testid="registry-form-provider"
          >
            {providers.map((p) => (
              <option key={p.provider_id} value={p.provider_id}>
                {p.name} ({p.wire_format})
              </option>
            ))}
          </select>
        </Field>
        <Field
          label="Upstream model"
          help="The model id AT the provider, e.g. claude-haiku-4-5-20251001 or gpt-4o (no provider prefix)."
        >
          <input
            className={INPUT}
            value={upstream}
            onChange={(e) => setUpstream(e.target.value)}
            placeholder="gpt-4o"
            data-testid="registry-form-upstream"
          />
        </Field>
        <Field
          label="Default quality band"
          help="Band (low/medium/high) projects choose. The cheapest enabled model in the band is used."
        >
          <select
            className={INPUT}
            value={quality}
            onChange={(e) => setQuality(e.target.value as ModelQuality)}
            data-testid="registry-form-quality"
          >
            {QUALITIES.map((q) => (
              <option key={q} value={q}>
                {q}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label="Cost in — $ / 1M input tokens"
          help="Provider list price. Feeds cost tracking + the global quota meters."
        >
          <input
            type="number"
            step="0.01"
            className={INPUT}
            value={costIn}
            onChange={(e) => setCostIn(e.target.value)}
            data-testid="registry-form-costin"
          />
        </Field>
        <Field
          label="Cost out — $ / 1M output tokens"
          help="Provider list price for generated tokens."
        >
          <input
            type="number"
            step="0.01"
            className={INPUT}
            value={costOut}
            onChange={(e) => setCostOut(e.target.value)}
            data-testid="registry-form-costout"
          />
        </Field>
        <Field label="Context window (tokens)" help="Maximum context size the model accepts.">
          <input
            type="number"
            className={INPUT}
            value={ctx}
            onChange={(e) => setCtx(e.target.value)}
            data-testid="registry-form-ctx"
          />
        </Field>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-4">
        <label className="flex items-center gap-1.5 text-xs text-neutral-700">
          <input
            type="checkbox"
            checked={vision}
            onChange={(e) => setVision(e.target.checked)}
            data-testid="registry-form-vision"
          />{" "}
          vision
        </label>
        <label className="flex items-center gap-1.5 text-xs text-neutral-700">
          <input
            type="checkbox"
            checked={tools}
            onChange={(e) => setTools(e.target.checked)}
            data-testid="registry-form-tools"
          />{" "}
          tool_calling
        </label>
        <label className="flex items-center gap-1.5 text-xs text-neutral-700">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            data-testid="registry-form-enabled"
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
          data-testid="registry-form-submit"
        >
          {editing ? "Save changes" : "Create model"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50"
          data-testid="registry-form-cancel"
        >
          Cancel
        </button>
      </div>
    </section>
  );
}
