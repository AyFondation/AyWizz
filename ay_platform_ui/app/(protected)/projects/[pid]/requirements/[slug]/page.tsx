// =============================================================================
// File: page.tsx
// Version: 1
// Path: ay_platform_ui/app/(protected)/projects/[pid]/requirements/[slug]/page.tsx
// Description: Single requirements document — fetches the full Markdown
//              content from C5 and renders it inside a styled <pre>
//              block (no Markdown-to-HTML conversion in v1 ; the doc
//              corpus is human-readable as-is and adding a renderer
//              dep is deferred). Header surfaces version + status +
//              update timestamp.
// =============================================================================

"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { ApiClient, ApiError } from "@/lib/apiClient";
import type { RequirementDocumentDetail } from "@/lib/types";

import { useConfigState } from "../../../../../providers";

type DetailState =
  | { status: "loading" }
  | { status: "ready"; doc: RequirementDocumentDetail }
  | { status: "not-found" }
  | { status: "error"; message: string };

export default function RequirementDocumentPage() {
  const params = useParams<{ pid: string; slug: string }>();
  const projectId = decodeURIComponent(params.pid);
  const slug = decodeURIComponent(params.slug);
  const configState = useConfigState();
  const [state, setState] = useState<DetailState>({ status: "loading" });

  const apiClient = useMemo(() => {
    if (configState.status !== "ready") return null;
    return new ApiClient(configState.config);
  }, [configState]);

  useEffect(() => {
    if (!apiClient) return;
    let cancelled = false;
    apiClient
      .getRequirementDocument(projectId, slug)
      .then((doc) => {
        if (!cancelled) setState({ status: "ready", doc });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setState({ status: "not-found" });
          return;
        }
        const message = err instanceof Error ? err.message : String(err);
        setState({ status: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, projectId, slug]);

  if (state.status === "loading") {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10">
        <p className="text-neutral-500">Loading document…</p>
      </main>
    );
  }

  if (state.status === "not-found") {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10">
        <h2 className="text-2xl font-semibold">Document not found</h2>
        <p className="mt-2 text-sm text-neutral-500">
          The document <code className="rounded bg-neutral-100 px-1">{slug}</code> doesn't exist (or
          you don't have access).
        </p>
        <Link
          href={`/projects/${encodeURIComponent(projectId)}/requirements`}
          className="mt-6 inline-block rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50"
        >
          ← Back to documents
        </Link>
      </main>
    );
  }

  if (state.status === "error") {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10">
        <p className="text-red-700" role="alert">
          Failed to load: {state.message}
        </p>
      </main>
    );
  }

  const { doc } = state;
  return (
    <main className="mx-auto max-w-5xl px-6 py-10" data-testid="requirement-detail">
      <nav className="text-xs text-neutral-500" aria-label="Breadcrumb">
        <Link
          href={`/projects/${encodeURIComponent(projectId)}/requirements`}
          className="hover:underline"
        >
          ← Documents
        </Link>
      </nav>
      <header className="mt-2 flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h2 className="break-all font-mono text-xl font-semibold text-neutral-900">{doc.slug}</h2>
          <p className="mt-1 text-sm text-neutral-500">
            v{doc.version} · {doc.status} · {doc.language} · updated{" "}
            {new Date(doc.updated_at).toLocaleString()}
          </p>
        </div>
      </header>

      <article
        className="mt-8 overflow-x-auto rounded-lg border border-neutral-200 bg-white p-6"
        data-testid="document-content"
      >
        <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-neutral-900">
          {doc.content}
        </pre>
      </article>

      <p className="mt-4 text-xs text-neutral-400">
        Rich Markdown rendering deferred — v1 surfaces the raw spec source so you can copy / search
        verbatim. Add a renderer (e.g. <code>marked</code>) when prettier viewing is needed.
      </p>
    </main>
  );
}
