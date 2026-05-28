// =============================================================================
// File: page.tsx
// Version: 2
// Path: ay_platform_ui/app/(protected)/projects/[pid]/requirements/page.tsx
// Description: Requirements documents list (Phase E). Lists every
//              document slug the project has registered with C5,
//              shows version/status/language, and links to the
//              per-document detail at /[slug]. Read-only — v1 doesn't
//              edit specs from the UX.
// =============================================================================

"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { ApiClient, ApiError } from "@/lib/apiClient";
import type { RequirementDocument } from "@/lib/types";

import { useConfigState } from "../../../../providers";

type ListState =
  | { status: "loading" }
  | { status: "ready"; items: RequirementDocument[] }
  | { status: "error"; message: string };

export default function RequirementsListPage() {
  const params = useParams<{ pid: string }>();
  const projectId = decodeURIComponent(params.pid);
  const configState = useConfigState();
  const [state, setState] = useState<ListState>({ status: "loading" });

  const apiClient = useMemo(() => {
    if (configState.status !== "ready") return null;
    return new ApiClient(configState.config);
  }, [configState]);

  useEffect(() => {
    if (!apiClient) return;
    let cancelled = false;
    apiClient
      .listRequirementDocuments(projectId)
      .then((resp) => {
        if (!cancelled) setState({ status: "ready", items: resp.documents ?? [] });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof ApiError ? `HTTP ${err.status}` : String(err);
        setState({ status: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, projectId]);

  return (
    <main className="mx-auto max-w-7xl px-6 py-10">
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Requirements</h2>
          <p className="mt-1 text-sm text-neutral-500">
            Browse the project's specification corpus. Read-only in v1.
          </p>
        </div>
        {state.status === "ready" ? (
          <p className="text-sm text-neutral-500" data-testid="requirements-count">
            {state.items.length} document{state.items.length === 1 ? "" : "s"}
          </p>
        ) : null}
      </header>

      <section className="mt-8">
        {state.status === "loading" ? (
          <p className="text-neutral-500">Loading documents…</p>
        ) : state.status === "error" ? (
          <p className="text-red-700" role="alert">
            Failed to load: {state.message}
          </p>
        ) : state.items.length === 0 ? (
          <div
            className="rounded-lg border border-dashed border-neutral-300 p-10 text-center"
            data-testid="requirements-empty-state"
          >
            <p className="text-neutral-600">No documents yet.</p>
            <p className="mt-1 text-sm text-neutral-500">
              An admin can author specs via C5's authoring endpoints (UX authoring lands in a
              follow-up).
            </p>
          </div>
        ) : (
          <ul className="space-y-2" data-testid="requirements-list">
            {state.items.map((doc) => (
              <li key={doc.slug}>
                <Link
                  href={`/projects/${encodeURIComponent(projectId)}/requirements/${encodeURIComponent(doc.slug)}`}
                  className="flex items-center justify-between gap-3 rounded-md border border-neutral-200 bg-white px-4 py-3 transition-colors hover:bg-neutral-50"
                  data-testid={`requirements-row-${doc.slug}`}
                >
                  <div className="min-w-0">
                    <p className="truncate font-mono text-sm font-medium text-neutral-900">
                      {doc.slug}
                    </p>
                    <p className="mt-0.5 text-xs text-neutral-500">
                      v{doc.version} · {doc.status} · {doc.language} ·{" "}
                      {new Date(doc.updated_at).toLocaleDateString()}
                    </p>
                  </div>
                  <StatusBadge status={doc.status} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}

function StatusBadge({ status }: { status: string }) {
  const palette: Record<string, string> = {
    draft: "bg-amber-100 text-amber-900",
    approved: "bg-emerald-100 text-emerald-900",
    superseded: "bg-neutral-200 text-neutral-700",
  };
  const cls = palette[status] ?? "bg-neutral-100 text-neutral-700";
  return (
    <span className={`shrink-0 rounded px-2 py-0.5 text-xs font-medium ${cls}`}>{status}</span>
  );
}
