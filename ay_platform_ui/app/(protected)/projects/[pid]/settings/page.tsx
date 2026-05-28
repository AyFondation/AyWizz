// =============================================================================
// File: page.tsx
// Version: 2
// Path: ay_platform_ui/app/(protected)/projects/[pid]/settings/page.tsx
// Description: Project settings page. v2 ships the per-project LLM
//              system_prompt editor — admin / tenant_admin /
//              project_owner only ; lower roles see a read-only view
//              of the effective prompt and a hint pointing at the
//              right contact. Project-wide metadata (members table,
//              cross-tenant flags, etc.) lands in subsequent passes.
// =============================================================================

"use client";

import { useParams } from "next/navigation";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/app/auth-provider";
import { useReadyConfig } from "@/app/providers";
import { ApiClient, ApiError } from "@/lib/apiClient";
import type { Project } from "@/lib/types";

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
      <main className="mx-auto max-w-3xl px-6 py-10">
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
      <main className="mx-auto max-w-3xl px-6 py-10">
        <p className="text-sm text-neutral-500">Loading settings…</p>
      </main>
    );
  }

  const isDefault = project.system_prompt_is_default;

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
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
