// =============================================================================
// File: page.tsx
// Version: 2
// Path: ay_platform_ui/app/(protected)/projects/[pid]/validation/page.tsx
// Description: Validation kick-off (Phase F). v1 scope : trigger a run
//              (one of the installed plugins / domains) ; on 202 jump
//              to the run detail page where the operator follows
//              progress + findings. A real "list runs by project"
//              endpoint doesn't exist yet on C6, so there's no list
//              view here — the kick-off form is the only entry point.
// =============================================================================

"use client";

import { useParams, useRouter } from "next/navigation";
import { type FormEvent, useEffect, useMemo, useState } from "react";

import { ApiClient, ApiError } from "@/lib/apiClient";
import type { ValidationPlugin } from "@/lib/types";

import { useConfigState } from "../../../../providers";

type PluginsState =
  | { status: "loading" }
  | { status: "ready"; plugins: ValidationPlugin[] }
  | { status: "error"; message: string };

export default function ValidationPage() {
  const params = useParams<{ pid: string }>();
  const router = useRouter();
  const projectId = decodeURIComponent(params.pid);
  const configState = useConfigState();
  const [plugins, setPlugins] = useState<PluginsState>({ status: "loading" });
  const [domain, setDomain] = useState("code");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const apiClient = useMemo(() => {
    if (configState.status !== "ready") return null;
    return new ApiClient(configState.config);
  }, [configState]);

  useEffect(() => {
    if (!apiClient) return;
    let cancelled = false;
    apiClient
      .listValidationPlugins()
      .then((items) => {
        if (cancelled) return;
        setPlugins({ status: "ready", plugins: items });
        if (items.length > 0) setDomain(items[0].domain);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof ApiError ? `HTTP ${err.status}` : String(err);
        setPlugins({ status: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient]);

  async function onSubmit(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    if (!apiClient) return;
    setSubmitting(true);
    setError(null);
    try {
      const resp = await apiClient.triggerValidationRun({
        project_id: projectId,
        domain,
      });
      router.push(
        `/projects/${encodeURIComponent(projectId)}/validation/${encodeURIComponent(resp.run_id)}`,
      );
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`Trigger failed (HTTP ${err.status}): ${err.body || "(no body)"}`);
      } else {
        setError(String(err));
      }
      setSubmitting(false);
    }
  }

  return (
    <main className="mx-auto max-w-7xl px-6 py-10">
      <header>
        <h2 className="text-2xl font-semibold tracking-tight">Validation</h2>
        <p className="mt-1 text-sm text-neutral-500">
          Trigger a validation run and inspect findings.
        </p>
      </header>

      <section
        className="mt-8 rounded-lg border border-neutral-200 bg-white p-6"
        data-testid="trigger-run-card"
      >
        <h3 className="text-sm font-medium uppercase tracking-wide text-neutral-500">
          Kick off a new run
        </h3>
        {plugins.status === "loading" ? (
          <p className="mt-3 text-neutral-500">Loading available domains…</p>
        ) : plugins.status === "error" ? (
          <p className="mt-3 text-red-700" role="alert">
            Failed to load plugins: {plugins.message}
          </p>
        ) : plugins.plugins.length === 0 ? (
          <p className="mt-3 text-sm text-neutral-500">
            No validation plugins installed on this platform.
          </p>
        ) : (
          <form onSubmit={onSubmit} className="mt-3 flex flex-wrap items-end gap-3">
            <label className="block">
              <span className="text-xs uppercase tracking-wide text-neutral-500">Domain</span>
              <select
                value={domain}
                onChange={(e) => setDomain(e.target.value)}
                className="mt-1 block rounded-md border border-neutral-300 bg-white px-3 py-1.5 text-sm"
                data-testid="trigger-domain-select"
              >
                {plugins.plugins.map((p) => (
                  <option key={p.plugin_id} value={p.domain}>
                    {p.domain} ({p.plugin_id} v{p.version})
                  </option>
                ))}
              </select>
            </label>
            <button
              type="submit"
              disabled={submitting || !apiClient}
              className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              data-testid="trigger-submit"
            >
              {submitting ? "Triggering…" : "Run validation"}
            </button>
          </form>
        )}
        {error ? (
          <p className="mt-3 text-sm text-red-700" role="alert">
            {error}
          </p>
        ) : null}
      </section>

      <section className="mt-6 rounded-lg border border-dashed border-neutral-300 p-5 text-sm text-neutral-500">
        <p>
          Project-scoped run history coming when C6 exposes a `list runs by project` endpoint. For
          now the kick-off above is the entry point ; each triggered run gets its own detail page at{" "}
          <code>/validation/[rid]</code>.
        </p>
      </section>
    </main>
  );
}
