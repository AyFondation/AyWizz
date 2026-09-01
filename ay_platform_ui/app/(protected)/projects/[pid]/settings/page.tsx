// =============================================================================
// File: page.tsx
// Version: 5
// Path: ay_platform_ui/app/(protected)/projects/[pid]/settings/page.tsx
// Description: Project settings page. v2 ships the per-project LLM
//              system_prompt editor — admin / tenant_admin /
//              project_owner only ; lower roles see a read-only view
//              of the effective prompt and a hint pointing at the
//              right contact. v3 adds the per-project ENRICHMENT config
//              (R-400-224): quality tier preset + per-option overrides +
//              the independent image-analyzer model, persisted to C7 and
//              applied to every subsequent upload.
// =============================================================================

"use client";

import { useParams } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/app/auth-provider";
import { useReadyConfig } from "@/app/providers";
import { ApiClient, ApiError } from "@/lib/apiClient";
import type {
  EmbeddingCatalogModelPublic,
  EnrichmentConfig,
  ModelQuality,
  Project,
  ProjectEmbeddingResponse,
  TenantCatalogModelPublic,
} from "@/lib/types";

const EDITOR_ROLES = new Set(["admin", "tenant_admin"]);
const PROJECT_EDITOR_ROLE = "project_owner";

export default function ProjectSettingsPage() {
  const params = useParams<{ pid: string }>();
  const projectId = decodeURIComponent(params.pid);
  const cfg = useReadyConfig();
  const { state: authState } = useAuth();

  const apiClient = useMemo(() => new ApiClient(cfg), [cfg]);

  const [project, setProject] = useState<Project | null>(null);
  const [systemPrompt, setSystemPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedMessage, setSavedMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .getProject(projectId)
      .then((p) => {
        if (cancelled) return;
        setProject(p);
        setSystemPrompt(p.system_prompt);
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(
          err instanceof ApiError
            ? `Failed to load project (${err.status})`
            : "Failed to load project.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, projectId]);

  // Role gate: who can actually persist a system_prompt override?
  // Same set as the server-side `PATCH /api/v1/projects/{pid}` gate:
  // admin / tenant_admin (global) OR project_owner (per-project).
  const canEdit = useMemo(() => {
    if (authState.status !== "authenticated") return false;
    const globalRoles = new Set(authState.claims.roles ?? []);
    for (const r of EDITOR_ROLES) {
      if (globalRoles.has(r)) return true;
    }
    const projectScopes = (authState.claims.project_scopes ?? {}) as Record<string, string[]>;
    const projectRoles = projectScopes[projectId] ?? [];
    return projectRoles.includes(PROJECT_EDITOR_ROLE);
  }, [authState, projectId]);

  // The project's associated LLM models are tenant-admin territory.
  const isTenantAdmin = useMemo(() => {
    if (authState.status !== "authenticated") return false;
    const g = new Set(authState.claims.roles ?? []);
    return g.has("admin") || g.has("tenant_admin");
  }, [authState]);

  async function onSave(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    setSavedMessage(null);
    setSaveError(null);
    setBusy(true);
    try {
      const updated = await apiClient.updateProject(projectId, {
        system_prompt: systemPrompt,
      });
      setProject(updated);
      setSystemPrompt(updated.system_prompt);
      setSavedMessage("Project prompt saved.");
    } catch (err) {
      setSaveError(err instanceof ApiError ? `Save failed (${err.status})` : "Save failed.");
    } finally {
      setBusy(false);
    }
  }

  async function onReset(): Promise<void> {
    setSavedMessage(null);
    setSaveError(null);
    setBusy(true);
    try {
      // Empty string is the clear-override sentinel server-side.
      const updated = await apiClient.updateProject(projectId, {
        system_prompt: "",
      });
      setProject(updated);
      setSystemPrompt(updated.system_prompt);
      setSavedMessage("Project prompt reset to default.");
    } catch (err) {
      setSaveError(err instanceof ApiError ? `Reset failed (${err.status})` : "Reset failed.");
    } finally {
      setBusy(false);
    }
  }

  if (loadError) {
    return (
      <main className="px-6 py-10">
        <p
          className="rounded border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-800"
          role="alert"
        >
          {loadError}
        </p>
      </main>
    );
  }

  if (project === null) {
    return (
      <main className="px-6 py-10">
        <p className="text-sm text-neutral-500">Loading settings…</p>
      </main>
    );
  }

  const isDefault = project.system_prompt_is_default;

  return (
    <main className="px-6 py-10">
      <header>
        <h2 className="text-2xl font-semibold tracking-tight">Project settings</h2>
        <p className="mt-1 text-sm text-neutral-500">
          Configure the LLM behaviour for this project. The prompt below is appended after the
          user&rsquo;s personal prompt and before any retrieved context.
        </p>
      </header>

      {project.git_repo_url ? (
        <section
          className="mt-8 rounded-lg border border-neutral-200 bg-white p-6"
          data-testid="project-git-repo"
        >
          <h3 className="text-sm font-medium uppercase tracking-wide text-neutral-500">
            Git repository
          </h3>
          <p className="mt-2 text-sm text-neutral-600">
            HTTPS clone URL of the project&rsquo;s versioned source. Use it to{" "}
            <code className="rounded bg-neutral-100 px-1">git clone</code> the repo from your own
            machine ; the platform pushes every generated artifact here on each run.
          </p>
          <input
            type="text"
            readOnly
            value={project.git_repo_url}
            className="mt-3 block w-full select-all rounded-md border border-neutral-300 bg-neutral-50 px-3 py-1.5 font-mono text-xs text-neutral-800"
            data-testid="project-git-clone-url"
            onFocus={(e) => e.currentTarget.select()}
          />
        </section>
      ) : null}

      <section
        className="mt-8 rounded-lg border border-neutral-200 bg-white p-6"
        data-testid="project-system-prompt"
      >
        <h3 className="text-sm font-medium uppercase tracking-wide text-neutral-500">
          Project LLM prompt {isDefault ? "(using default)" : "(override active)"}
        </h3>
        <p className="mt-2 text-sm text-neutral-600">
          Prepended to every chat message after the user&rsquo;s prompt and before the retrieved
          context. Empty means &laquo; use the platform default &raquo; (currently blank unless an
          operator has tuned{" "}
          <code className="rounded bg-neutral-100 px-1">C2_DEFAULT_PROJECT_PROMPT</code>).
        </p>

        {!canEdit ? (
          <div className="mt-4 rounded border border-neutral-200 bg-neutral-50 px-4 py-3 text-xs text-neutral-600">
            Read-only — only project owners and tenant admins can change this. Ask your project
            owner if you need it tuned.
          </div>
        ) : null}

        <form onSubmit={onSave} className="mt-4">
          <label className="block">
            <span className="text-xs uppercase tracking-wide text-neutral-500">
              Active project prompt
            </span>
            <textarea
              value={systemPrompt}
              onChange={(e) => {
                setSystemPrompt(e.target.value);
                setSavedMessage(null);
              }}
              rows={6}
              maxLength={4000}
              className="mt-1 block w-full rounded-md border border-neutral-300 px-3 py-2 text-sm leading-relaxed disabled:bg-neutral-50 disabled:text-neutral-500"
              data-testid="project-prompt-input"
              disabled={!canEdit || busy}
              placeholder="(no project prompt — chat uses user prompt + RAG only)"
            />
          </label>
          {canEdit ? (
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <button
                type="submit"
                className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                data-testid="project-prompt-save"
                disabled={busy}
              >
                Save
              </button>
              {!isDefault ? (
                <button
                  type="button"
                  onClick={onReset}
                  className="rounded-md border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
                  data-testid="project-prompt-reset"
                  disabled={busy}
                >
                  Reset to default
                </button>
              ) : null}
              <span className="text-xs text-neutral-500">
                {systemPrompt.length}/4000 characters
              </span>
            </div>
          ) : null}
        </form>
      </section>

      {saveError ? (
        <p className="mt-4 text-sm text-red-700" role="alert" data-testid="project-settings-error">
          {saveError}
        </p>
      ) : null}
      {savedMessage ? (
        <p
          className="mt-4 text-sm text-emerald-700"
          role="status"
          data-testid="project-settings-saved"
        >
          {savedMessage}
        </p>
      ) : null}

      <EnrichmentSection apiClient={apiClient} projectId={projectId} canEdit={canEdit} />

      {isTenantAdmin && <ProjectModelsSection apiClient={apiClient} projectId={projectId} />}

      {isTenantAdmin && <ProjectEmbeddingSection apiClient={apiClient} projectId={projectId} />}

      <section className="mt-6 rounded-lg border border-dashed border-neutral-300 p-5 text-sm text-neutral-500">
        <p>Coming later :</p>
        <ul className="mt-1 list-disc pl-5">
          <li>Members table (admin / owner can grant/revoke project roles)</li>
          <li>Per-section feature flags (cross-tenant promotion, etc.)</li>
          <li>Project metadata edition (name, archival)</li>
        </ul>
      </section>
    </main>
  );
}

// ---------------------------------------------------------------------------
// Enrichment config — quality-tier preset + per-option overrides + the
// independent image-analyzer model. Persisted to C7 (R-400-224).
// ---------------------------------------------------------------------------

const _TIERS: EnrichmentConfig["quality_tier"][] = ["minimal", "standard", "high"];
type ToggleKey =
  | "summarization_enabled"
  | "decontextualization_enabled"
  | "densification_enabled"
  | "image_vision_enabled";
const _TOGGLES: { key: ToggleKey; label: string }[] = [
  { key: "summarization_enabled", label: "Document summary" },
  { key: "decontextualization_enabled", label: "Term disambiguation" },
  { key: "densification_enabled", label: "Densification" },
  { key: "image_vision_enabled", label: "Image vision + dedup" },
];

/** tri-state select value ↔ boolean|null (null = inherit the tier preset). */
function triValue(v: boolean | null): "inherit" | "on" | "off" {
  return v == null ? "inherit" : v ? "on" : "off";
}
function fromTri(s: string): boolean | null {
  return s === "inherit" ? null : s === "on";
}

function EnrichmentSection({
  apiClient,
  projectId,
  canEdit,
}: {
  apiClient: ApiClient;
  projectId: string;
  canEdit: boolean;
}) {
  const [config, setConfig] = useState<EnrichmentConfig | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .getEnrichmentConfig(projectId)
      .then((c) => {
        if (!cancelled) setConfig(c);
      })
      .catch((err) => {
        if (!cancelled)
          setError(err instanceof ApiError ? `Load failed (${err.status})` : "Load failed.");
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, projectId]);

  const patch = useCallback((p: Partial<EnrichmentConfig>) => {
    setSaved(false);
    setConfig((c) => (c ? { ...c, ...p } : c));
  }, []);

  async function onSave(): Promise<void> {
    if (!config) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await apiClient.updateEnrichmentConfig(projectId, config);
      setConfig(updated);
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? `Save failed (${err.status})` : "Save failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className="mt-8 rounded-lg border border-neutral-200 bg-white p-6"
      data-testid="project-enrichment"
    >
      <h3 className="text-sm font-medium uppercase tracking-wide text-neutral-500">
        Ingestion enrichment
      </h3>
      <p className="mt-2 text-sm text-neutral-600">
        Controls what runs when a source is uploaded. The <strong>quality tier</strong> is the
        preset; each option can override it independently. Applies to subsequent uploads.
      </p>

      {error ? (
        <p className="mt-3 text-sm text-red-700" role="alert" data-testid="enrichment-error">
          {error}
        </p>
      ) : null}

      {config === null ? (
        <p className="mt-3 text-sm text-neutral-500">Loading enrichment config…</p>
      ) : (
        <div className="mt-4 space-y-4">
          <label className="block">
            <span className="text-xs uppercase tracking-wide text-neutral-500">Quality tier</span>
            <select
              value={config.quality_tier}
              onChange={(e) =>
                patch({ quality_tier: e.target.value as EnrichmentConfig["quality_tier"] })
              }
              disabled={!canEdit || busy}
              className="mt-1 block w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm disabled:bg-neutral-50"
              data-testid="enrichment-tier"
            >
              {_TIERS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="text-xs uppercase tracking-wide text-neutral-500">Model quality</span>
            <select
              value={config.model_quality ?? "inherit"}
              onChange={(e) =>
                patch({
                  model_quality:
                    e.target.value === "inherit" ? null : (e.target.value as ModelQuality),
                })
              }
              disabled={!canEdit || busy}
              className="mt-1 block w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm disabled:bg-neutral-50"
              data-testid="enrichment-model-quality"
            >
              <option value="inherit">Inherit default</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </select>
            <span className="mt-1 block text-xs text-neutral-500">
              Which model runs (resolved against your tenant&apos;s catalogue) — distinct from the
              enrichment depth above.
            </span>
          </label>

          <fieldset className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {_TOGGLES.map(({ key, label }) => (
              <label key={key} className="block">
                <span className="text-xs uppercase tracking-wide text-neutral-500">{label}</span>
                <select
                  value={triValue(config[key])}
                  onChange={(e) =>
                    patch({ [key]: fromTri(e.target.value) } as Partial<EnrichmentConfig>)
                  }
                  disabled={!canEdit || busy}
                  className="mt-1 block w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm disabled:bg-neutral-50"
                  data-testid={`enrichment-${key}`}
                >
                  <option value="inherit">Inherit from tier</option>
                  <option value="on">On</option>
                  <option value="off">Off</option>
                </select>
              </label>
            ))}
          </fieldset>

          <label className="block">
            <span className="text-xs uppercase tracking-wide text-neutral-500">
              Image-analyzer model (independent of text agents)
            </span>
            <input
              type="text"
              value={config.image_analyzer_model ?? ""}
              onChange={(e) => patch({ image_analyzer_model: e.target.value || null })}
              disabled={!canEdit || busy}
              placeholder="(inherit default — e.g. ollama:llava, openai:gpt-4o-mini)"
              className="mt-1 block w-full rounded-md border border-neutral-300 px-3 py-1.5 font-mono text-xs disabled:bg-neutral-50"
              data-testid="enrichment-image-model"
            />
          </label>

          {!canEdit ? (
            <div className="rounded border border-neutral-200 bg-neutral-50 px-4 py-3 text-xs text-neutral-600">
              Read-only — only project owners and tenant admins can change ingestion behaviour.
            </div>
          ) : (
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={onSave}
                disabled={busy}
                className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
                data-testid="enrichment-save"
              >
                {busy ? "Saving…" : "Save enrichment config"}
              </button>
              {saved ? (
                <span
                  className="text-xs text-emerald-700"
                  role="status"
                  data-testid="enrichment-saved"
                >
                  Saved.
                </span>
              ) : null}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

// Per-project LLM model list (tenant-admin). Browse the tenant catalogue and
// pick which models THIS project may use; `model_quality` then resolves within
// this set. Unconfigured = the tenant's "default for new projects" set.
function ProjectModelsSection({
  apiClient,
  projectId,
}: {
  apiClient: ApiClient;
  projectId: string;
}) {
  const [catalogue, setCatalogue] = useState<TenantCatalogModelPublic[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [isExplicit, setIsExplicit] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const reload = useCallback(() => {
    Promise.all([apiClient.listLlmCatalog(), apiClient.getProjectModels(projectId)])
      .then(([cat, pm]) => {
        setCatalogue(cat.models);
        setSelected(new Set(pm.model_ids));
        setIsExplicit(pm.is_explicit);
      })
      .catch((err) =>
        setError(err instanceof ApiError ? `Load failed (${err.status})` : "Load failed."),
      );
  }, [apiClient, projectId]);

  useEffect(() => {
    reload();
  }, [reload]);

  const toggle = (id: string) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const save = async () => {
    setError(null);
    setNotice(null);
    try {
      await apiClient.setProjectModels(projectId, [...selected]);
      setNotice("Project models saved.");
      reload();
    } catch (err) {
      setError(err instanceof ApiError ? `Save failed (${err.status})` : "Save failed.");
    }
  };

  return (
    <section className="mt-8 border-t border-neutral-200 pt-6" data-testid="project-models">
      <h2 className="text-base font-semibold text-neutral-800">Models</h2>
      <p className="mt-1 text-sm text-neutral-600">
        The models this project may use. The project&apos;s quality band resolves within this set.{" "}
        {!isExplicit && (
          <span className="text-amber-700" data-testid="project-models-using-defaults">
            Currently using the tenant defaults — saving creates an explicit list.
          </span>
        )}
      </p>

      {error && (
        <p className="mt-2 text-sm text-red-700" role="alert" data-testid="project-models-error">
          {error}
        </p>
      )}
      {notice && (
        <p
          className="mt-2 text-sm text-emerald-700"
          role="status"
          data-testid="project-models-notice"
        >
          {notice}
        </p>
      )}

      {catalogue === null ? (
        <p className="mt-3 text-sm text-neutral-500">Loading…</p>
      ) : catalogue.length === 0 ? (
        <p className="mt-3 text-sm text-neutral-500" data-testid="project-models-empty">
          The tenant catalogue is empty — add models under LLM catalogue first.
        </p>
      ) : (
        <>
          <ul className="mt-3 space-y-1.5">
            {catalogue.map((m) => (
              <li key={m.model_id} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={selected.has(m.model_id)}
                  onChange={() => toggle(m.model_id)}
                  data-testid={`project-model-${m.registry.alias}`}
                />
                <span className="font-medium text-neutral-800">{m.registry.alias}</span>
                <span className="text-xs text-neutral-500">
                  {m.registry.default_model_quality} · {m.registry.upstream_model}
                </span>
              </li>
            ))}
          </ul>
          <button
            type="button"
            onClick={save}
            className="mt-3 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
            data-testid="project-models-save"
          >
            Save models
          </button>
        </>
      )}
    </section>
  );
}

function ProjectEmbeddingSection({
  apiClient,
  projectId,
}: {
  apiClient: ApiClient;
  projectId: string;
}) {
  const [catalogue, setCatalogue] = useState<EmbeddingCatalogModelPublic[] | null>(null);
  const [current, setCurrent] = useState<ProjectEmbeddingResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const reload = useCallback(() => {
    Promise.all([apiClient.listEmbeddingCatalog(), apiClient.getProjectEmbedding(projectId)])
      .then(([cat, pe]) => {
        setCatalogue(cat.models);
        setCurrent(pe);
      })
      .catch((err) =>
        setError(err instanceof ApiError ? `Load failed (${err.status})` : "Load failed."),
      );
  }, [apiClient, projectId]);

  useEffect(() => {
    reload();
  }, [reload]);

  const select = async (modelId: string) => {
    setError(null);
    setNotice(null);
    try {
      await apiClient.setProjectEmbedding(projectId, modelId);
      setNotice(
        "Embedding model saved — existing data is re-embedded automatically (no re-parse).",
      );
      reload();
    } catch (err) {
      if (err instanceof ApiError && err.status === 422)
        setError("That model isn't enabled in the tenant catalogue.");
      else setError(err instanceof ApiError ? `Save failed (${err.status})` : "Save failed.");
    }
  };

  const enabled = (catalogue ?? []).filter((m) => m.enabled);

  return (
    <section className="mt-8 border-t border-neutral-200 pt-6" data-testid="project-embedding">
      <h2 className="text-base font-semibold text-neutral-800">Embedding</h2>
      <p className="mt-1 text-sm text-neutral-600">
        The single embedding model this project&apos;s RAG index uses. Switching it re-embeds the
        existing data automatically (vectors only — no re-parse).{" "}
        {current && !current.is_explicit && (
          <span className="text-amber-700" data-testid="project-embedding-using-default">
            Currently the tenant default.
          </span>
        )}
      </p>

      {error && (
        <p className="mt-2 text-sm text-red-700" role="alert" data-testid="project-embedding-error">
          {error}
        </p>
      )}
      {notice && (
        <p
          className="mt-2 text-sm text-emerald-700"
          role="status"
          data-testid="project-embedding-notice"
        >
          {notice}
        </p>
      )}

      {catalogue === null ? (
        <p className="mt-3 text-sm text-neutral-500">Loading…</p>
      ) : enabled.length === 0 ? (
        <p className="mt-3 text-sm text-neutral-500" data-testid="project-embedding-empty">
          No embedding models enabled in the tenant catalogue — add one under Embed catalogue first.
        </p>
      ) : (
        <ul className="mt-3 space-y-1.5">
          {enabled.map((m) => (
            <li key={m.model_id} className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="project-embedding"
                checked={current?.model_id === m.model_id}
                onChange={() => select(m.model_id)}
                data-testid={`project-embedding-${m.registry.alias}`}
              />
              <span className="font-medium text-neutral-800">{m.registry.alias}</span>
              <span className="text-xs text-neutral-500">
                {m.registry.dimension}d · {m.registry.upstream_model}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
