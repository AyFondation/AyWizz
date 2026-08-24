// =============================================================================
// File: page.tsx
// Version: 1
// Path: ay_platform_ui/app/(protected)/admin/embedding-providers/page.tsx
// Description: Embedding provider registry admin surface (platform_manager
//              only), parallel to the LLM providers page. A provider is an
//              ENDPOINT + a PROTOCOL (adapter, chosen at creation) + a
//              write-only key. `ollama` / `deterministic-hash` are keyless;
//              `openai` (OpenAI-compatible endpoints) uses the key. Models
//              reference a provider by its stable id. Create / edit / set-key /
//              delete.
// =============================================================================

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/app/auth-provider";
import { useReadyConfig } from "@/app/providers";
import { ApiClient, ApiError } from "@/lib/apiClient";
import type {
  EmbeddingAdapter,
  EmbeddingProviderPublic,
  EmbeddingProviderUpsert,
} from "@/lib/types";

type FormTarget = EmbeddingProviderPublic | "new" | null;

const ADAPTERS: EmbeddingAdapter[] = ["ollama", "openai", "deterministic-hash"];

export default function EmbeddingProvidersPage() {
  const cfg = useReadyConfig();
  const { state: authState } = useAuth();
  const apiClient = useMemo(() => new ApiClient(cfg), [cfg]);

  const [providers, setProviders] = useState<EmbeddingProviderPublic[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [form, setForm] = useState<FormTarget>(null);

  const isPlatformManager = useMemo(() => {
    if (authState.status !== "authenticated") return false;
    return (authState.claims.roles ?? []).includes("platform_manager");
  }, [authState]);

  const reload = useCallback(() => {
    apiClient
      .listEmbeddingProviders()
      .then((r) => setProviders(r.providers))
      .catch((err) =>
        setError(err instanceof ApiError ? `Load failed (${err.status})` : "Load failed."),
      );
  }, [apiClient]);

  useEffect(() => {
    if (isPlatformManager) reload();
  }, [isPlatformManager, reload]);

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
          setError("A provider with that name already exists, or a model still references it.");
        else setError(err instanceof ApiError ? `Failed (${err.status})` : "Failed.");
      }
    },
    [reload],
  );

  const onSave = useCallback(
    async (
      existing: EmbeddingProviderPublic | null,
      body: EmbeddingProviderUpsert,
      key: string,
    ) => {
      setError(null);
      setNotice(null);
      try {
        const saved = existing
          ? await apiClient.updateEmbeddingProvider(existing.provider_id, body)
          : await apiClient.createEmbeddingProvider(body);
        let keyNote = "";
        if (key) {
          try {
            await apiClient.putEmbeddingProviderApiKey(saved.provider_id, key);
            keyNote = " — API key stored";
          } catch (err) {
            if (err instanceof ApiError && err.status === 503) {
              keyNote = " — saved, but key NOT stored (no master key on this deployment)";
            } else {
              throw err;
            }
          }
        }
        setNotice(`Saved ${body.name}${keyNote}.`);
        setForm(null);
        reload();
      } catch (err) {
        if (err instanceof ApiError && err.status === 409)
          setError("A provider with that name already exists.");
        else setError(err instanceof ApiError ? `Save failed (${err.status})` : "Save failed.");
      }
    },
    [apiClient, reload],
  );

  if (!isPlatformManager) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10">
        <p
          className="rounded border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm text-neutral-600"
          data-testid="embedding-providers-forbidden"
        >
          Embedding providers are restricted to platform administrators (platform_manager).
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-10" data-testid="embedding-providers">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-lg font-semibold text-neutral-800">Embedding providers</h2>
          <p className="mt-2 max-w-2xl text-sm text-neutral-600">
            An endpoint + protocol an embedding model is served by. The protocol (adapter) is chosen
            here: <code>ollama</code> and <code>deterministic-hash</code> are keyless;{" "}
            <code>openai</code> targets any OpenAI-compatible <code>/embeddings</code> endpoint and
            uses the key.
          </p>
        </div>
        {form === null && (
          <button
            type="button"
            onClick={() => setForm("new")}
            className="shrink-0 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
            data-testid="embedding-providers-add"
          >
            + Add a provider
          </button>
        )}
      </div>

      {error && (
        <p
          className="mt-3 text-sm text-red-700"
          role="alert"
          data-testid="embedding-providers-error"
        >
          {error}
        </p>
      )}
      {notice && (
        <p
          className="mt-3 text-sm text-emerald-700"
          role="status"
          data-testid="embedding-providers-notice"
        >
          {notice}
        </p>
      )}

      {form !== null && (
        <ProviderForm
          existing={form === "new" ? null : form}
          onSave={onSave}
          onCancel={() => setForm(null)}
        />
      )}

      {providers === null ? (
        <p className="mt-4 text-sm text-neutral-500">Loading providers…</p>
      ) : (
        <table
          className="mt-4 w-full border-collapse text-sm"
          data-testid="embedding-providers-table"
        >
          <thead>
            <tr className="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500">
              <th className="px-3 py-2">Provider</th>
              <th className="px-3 py-2">Endpoint</th>
              <th className="px-3 py-2">Key</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {providers.map((p) => (
              <tr
                key={p.provider_id}
                className="border-b border-neutral-100"
                data-testid={`embedding-provider-row-${p.provider_id}`}
              >
                <td className="px-3 py-2">
                  <div className="font-medium text-neutral-800">{p.name}</div>
                  <div className="text-xs text-neutral-500">{p.adapter}</div>
                </td>
                <td className="px-3 py-2 text-xs text-blue-600">{p.base_url || "—"}</td>
                <td
                  className="px-3 py-2 text-xs"
                  data-testid={`embedding-provider-keystatus-${p.provider_id}`}
                >
                  {p.key_status === "set" ? (
                    <span className="text-emerald-700">set {p.api_key_hint}</span>
                  ) : (
                    <span className="text-amber-700">not set</span>
                  )}
                </td>
                <td className="px-3 py-2">
                  <div className="flex gap-1">
                    <button
                      type="button"
                      onClick={() => {
                        setForm(p);
                        setError(null);
                        setNotice(null);
                      }}
                      className="rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50"
                      data-testid={`embedding-provider-edit-${p.provider_id}`}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        run(
                          () => apiClient.deleteEmbeddingProvider(p.provider_id),
                          `Deleted ${p.name}.`,
                        )
                      }
                      className="rounded-md border border-red-200 px-2 py-1 text-xs text-red-700 hover:bg-red-50"
                      data-testid={`embedding-provider-delete-${p.provider_id}`}
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

function ProviderForm({
  existing,
  onSave,
  onCancel,
}: {
  existing: EmbeddingProviderPublic | null;
  onSave: (e: EmbeddingProviderPublic | null, body: EmbeddingProviderUpsert, key: string) => void;
  onCancel: () => void;
}) {
  const editing = existing !== null;
  const [name, setName] = useState(existing?.name ?? "");
  const [baseUrl, setBaseUrl] = useState(existing?.base_url ?? "");
  const [adapter, setAdapter] = useState<EmbeddingAdapter>(existing?.adapter ?? "ollama");
  const [apiKey, setApiKey] = useState("");

  // base_url is mandatory for the HTTP adapters; deterministic-hash is local.
  const needsUrl = adapter !== "deterministic-hash";
  const valid = name.trim() && (!needsUrl || baseUrl.trim());

  return (
    <section
      className="mt-4 rounded-md border border-blue-200 bg-blue-50/40 p-4"
      data-testid="embedding-provider-form"
    >
      <h3 className="text-sm font-semibold text-neutral-800">
        {editing ? `Edit ${existing.name}` : "Add a provider"}
      </h3>
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="block">
          <span className="text-xs font-medium text-neutral-700">Name</span>
          <input
            className={INPUT}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Ollama local / OpenAI embeddings"
            data-testid="embedding-provider-form-name"
          />
          <span className="mt-0.5 block text-[11px] text-neutral-400">
            Unique display name. Two accounts of one type = two providers.
          </span>
        </label>
        <label className="block">
          <span className="text-xs font-medium text-neutral-700">Protocol (adapter)</span>
          <select
            className={INPUT}
            value={adapter}
            onChange={(e) => setAdapter(e.target.value as EmbeddingAdapter)}
            data-testid="embedding-provider-form-adapter"
          >
            {ADAPTERS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
          <span className="mt-0.5 block text-[11px] text-neutral-400">
            How the endpoint is called. openai = OpenAI-compatible /embeddings (key). ollama = local
            /api/embeddings. deterministic-hash = built-in dev baseline (no endpoint).
          </span>
        </label>
        <label className="block sm:col-span-2">
          <span className="text-xs font-medium text-neutral-700">
            Base URL {needsUrl ? "(mandatory)" : "(unused for deterministic-hash)"}
          </span>
          <input
            className={INPUT}
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="http://ollama:11434  /  https://api.openai.com/v1"
            data-testid="embedding-provider-form-baseurl"
          />
          <span className="mt-0.5 block text-[11px] text-neutral-400">
            The endpoint every model on this provider calls.
          </span>
        </label>
        <label className="block sm:col-span-2">
          <span className="text-xs font-medium text-neutral-700">API key</span>
          <input
            type="password"
            className={INPUT}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={
              editing && existing.key_status === "set"
                ? `current: ${existing.api_key_hint}`
                : "sk-… (openai only)"
            }
            data-testid="embedding-provider-form-key"
          />
          <span className="mt-0.5 block text-[11px] text-neutral-400">
            Write-only: encrypted at rest, never shown. Only the openai adapter uses a key.{" "}
            {editing ? "Leave blank to keep the current key." : "Optional now — set it later."}
          </span>
        </label>
      </div>
      <div className="mt-4 flex gap-2">
        <button
          type="button"
          disabled={!valid}
          onClick={() =>
            onSave(existing, { name: name.trim(), adapter, base_url: baseUrl.trim() }, apiKey)
          }
          className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          data-testid="embedding-provider-form-submit"
        >
          {editing ? "Save changes" : "Create provider"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50"
          data-testid="embedding-provider-form-cancel"
        >
          Cancel
        </button>
      </div>
    </section>
  );
}
