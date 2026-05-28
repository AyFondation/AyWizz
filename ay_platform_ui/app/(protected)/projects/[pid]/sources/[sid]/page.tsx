// =============================================================================
// File: page.tsx
// Version: 1
// Path: ay_platform_ui/app/(protected)/projects/[pid]/sources/[sid]/page.tsx
// Description: Per-source detail view. Surfaces every metadata field
//              C7 exposes (mime, size, parse status / error, chunk
//              count, embedding model) and offers two actions :
//              download the raw blob and delete the source (role-
//              gated server-side). Chunk preview / embedding stats
//              land in a follow-up (require a new C7 endpoint).
// =============================================================================

"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { ApiClient, ApiError } from "@/lib/apiClient";
import { type Source, SUPPORTED_MIME_TYPES, type SupportedMimeType } from "@/lib/types";

import { useConfigState } from "../../../../../providers";

type DetailState =
  | { status: "loading" }
  | { status: "ready"; source: Source }
  | { status: "not-found" }
  | { status: "error"; message: string };

export default function SourceDetailPage() {
  const params = useParams<{ pid: string; sid: string }>();
  const router = useRouter();
  const projectId = decodeURIComponent(params.pid);
  const sourceId = decodeURIComponent(params.sid);
  const configState = useConfigState();
  const [state, setState] = useState<DetailState>({ status: "loading" });
  const [downloading, setDownloading] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const apiClient = useMemo(() => {
    if (configState.status !== "ready") return null;
    return new ApiClient(configState.config);
  }, [configState]);

  useEffect(() => {
    if (!apiClient) return;
    let cancelled = false;
    setState({ status: "loading" });
    apiClient
      .getSource(projectId, sourceId)
      .then((source) => {
        if (!cancelled) setState({ status: "ready", source });
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
  }, [apiClient, projectId, sourceId]);

  async function onDownload(): Promise<void> {
    if (!apiClient) return;
    setDownloading(true);
    try {
      const { blob, filename } = await apiClient.downloadSourceBlob(projectId, sourceId);
      // Programmatic download — anchor click triggers Save As.
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = filename ?? sourceId;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (err) {
      window.alert(`Download failed: ${String(err)}`);
    } finally {
      setDownloading(false);
    }
  }

  async function onDelete(): Promise<void> {
    if (!apiClient) return;
    if (!window.confirm(`Delete source ${sourceId}? This cannot be undone.`)) return;
    setDeleting(true);
    try {
      await apiClient.deleteSource(projectId, sourceId);
      router.push(`/projects/${encodeURIComponent(projectId)}/sources`);
    } catch (err) {
      window.alert(`Delete failed: ${String(err)}`);
      setDeleting(false);
    }
  }

  if (state.status === "loading") {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10">
        <p className="text-neutral-500">Loading source…</p>
      </main>
    );
  }

  if (state.status === "not-found") {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10">
        <h2 className="text-2xl font-semibold">Source not found</h2>
        <p className="mt-2 text-sm text-neutral-500">
          The source <code className="rounded bg-neutral-100 px-1">{sourceId}</code> doesn't exist
          in this project (or you don't have access).
        </p>
        <Link
          href={`/projects/${encodeURIComponent(projectId)}/sources`}
          className="mt-6 inline-block rounded-md border border-neutral-300 px-3 py-1.5 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
        >
          ← Back to sources
        </Link>
      </main>
    );
  }

  if (state.status === "error") {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10">
        <p className="text-red-700" role="alert">
          Failed to load source: {state.message}
        </p>
      </main>
    );
  }

  const { source } = state;
  const mimeLabel =
    SUPPORTED_MIME_TYPES[source.mime_type as SupportedMimeType]?.label ?? source.mime_type;

  return (
    <main className="mx-auto max-w-5xl px-6 py-10" data-testid="source-detail">
      <nav className="text-xs text-neutral-500" aria-label="Breadcrumb">
        <Link
          href={`/projects/${encodeURIComponent(projectId)}/sources`}
          className="hover:underline"
        >
          ← Sources
        </Link>
      </nav>
      <header className="mt-2 flex flex-wrap items-baseline justify-between gap-3">
        <div className="min-w-0">
          <h2 className="break-all font-mono text-xl font-semibold text-neutral-900">
            {source.source_id}
          </h2>
          <p className="mt-1 text-sm text-neutral-500">{mimeLabel}</p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onDownload}
            disabled={downloading}
            className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
            data-testid="source-download"
          >
            {downloading ? "Downloading…" : "Download"}
          </button>
          <button
            type="button"
            onClick={onDelete}
            disabled={deleting}
            className="rounded-md border border-red-200 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
            data-testid="source-delete"
          >
            {deleting ? "Deleting…" : "Delete"}
          </button>
        </div>
      </header>

      <section className="mt-8 rounded-lg border border-neutral-200 bg-white p-6">
        <h3 className="text-sm font-medium uppercase tracking-wide text-neutral-500">Metadata</h3>
        <dl className="mt-4 grid grid-cols-1 gap-x-6 gap-y-3 text-sm md:grid-cols-2">
          <Field label="Project" value={source.project_id} mono />
          <Field label="MIME type" value={source.mime_type} mono />
          <Field label="Size" value={`${source.size_bytes} bytes`} />
          <Field label="Chunks" value={String(source.chunk_count)} />
          <Field label="Uploaded by" value={source.uploaded_by} mono />
          <Field label="Uploaded at" value={new Date(source.uploaded_at).toLocaleString()} />
          <Field
            label="Parse status"
            value={source.parse_status}
            badge={parseStatusPalette[source.parse_status]}
          />
          <Field label="Embedding model" value={source.model_id ?? "(none)"} mono />
        </dl>
        {source.parse_error ? (
          <div
            className="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900"
            role="alert"
            data-testid="source-parse-error"
          >
            <p className="font-medium">Parse error</p>
            <p className="mt-1 whitespace-pre-wrap font-mono text-xs">{source.parse_error}</p>
          </div>
        ) : null}
      </section>

      <section className="mt-6 rounded-lg border border-dashed border-neutral-300 p-5 text-sm text-neutral-500">
        Chunk preview + embedding stats land in a follow-up (require a new C7 endpoint that returns
        chunk text + vector summary).
      </section>
    </main>
  );
}

const parseStatusPalette: Record<Source["parse_status"], string> = {
  pending: "bg-neutral-100 text-neutral-700",
  parsed: "bg-amber-100 text-amber-900",
  indexed: "bg-emerald-100 text-emerald-900",
  failed: "bg-red-100 text-red-900",
};

function Field({
  label,
  value,
  mono = false,
  badge,
}: {
  label: string;
  value: string;
  mono?: boolean;
  badge?: string;
}) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-neutral-400">{label}</dt>
      <dd className="mt-0.5">
        {badge ? (
          <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${badge}`}>
            {value}
          </span>
        ) : (
          <span className={["text-neutral-900", mono ? "font-mono text-xs" : "text-sm"].join(" ")}>
            {value}
          </span>
        )}
      </dd>
    </div>
  );
}
