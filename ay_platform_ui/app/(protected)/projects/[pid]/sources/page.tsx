// =============================================================================
// File: page.tsx
// Version: 2
// Path: ay_platform_ui/app/(protected)/projects/[pid]/sources/page.tsx
// Description: Sources section — list + upload (Phase C). Lists every
//              source ingested into the active project's C7 instance
//              with size, mime, upload date, parse status and chunk
//              count. Upload zone supports drag-and-drop and the
//              file picker ; derives `source_id` from the filename
//              slug, MIME from the extension. Unsupported MIMEs are
//              rejected client-side with a clear error before any
//              network round-trip.
// =============================================================================

"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { ApiClient, ApiError } from "@/lib/apiClient";
import {
  mimeTypeFromFilename,
  type Source,
  SUPPORTED_MIME_TYPES,
  type SupportedMimeType,
} from "@/lib/types";
import { useConfigState } from "../../../../providers";

type ListState =
  | { status: "loading" }
  | { status: "ready"; sources: Source[] }
  | { status: "error"; message: string };

export default function SourcesPage() {
  const params = useParams<{ pid: string }>();
  const projectId = decodeURIComponent(params.pid);
  const configState = useConfigState();
  const [state, setState] = useState<ListState>({ status: "loading" });
  const [refreshCounter, setRefreshCounter] = useState(0);

  const apiClient = useMemo(() => {
    if (configState.status !== "ready") return null;
    return new ApiClient(configState.config);
  }, [configState]);

  // `refreshCounter` is a "trigger" — bumping it forces a refetch
  // after an upload / delete. Biome doesn't see it read in the body
  // and would suggest removing it ; the suppression is intentional.
  // biome-ignore lint/correctness/useExhaustiveDependencies: refreshCounter is a manual refetch trigger
  useEffect(() => {
    if (!apiClient) return;
    let cancelled = false;
    setState({ status: "loading" });
    apiClient
      .listSources(projectId)
      .then((resp) => {
        if (!cancelled) setState({ status: "ready", sources: resp.sources });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof ApiError ? `HTTP ${err.status}` : String(err);
        setState({ status: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, projectId, refreshCounter]);

  const refresh = useCallback(() => setRefreshCounter((n) => n + 1), []);

  return (
    <main className="mx-auto max-w-7xl px-6 py-10">
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Sources</h2>
          <p className="mt-1 text-sm text-neutral-500">
            Upload and manage the source corpus feeding RAG retrieval.
          </p>
        </div>
        {state.status === "ready" ? (
          <p className="text-sm text-neutral-500" data-testid="sources-count">
            {state.sources.length} source{state.sources.length === 1 ? "" : "s"}
          </p>
        ) : null}
      </header>

      <UploadCard projectId={projectId} apiClient={apiClient} onUploaded={refresh} />

      <section className="mt-8">
        {state.status === "loading" ? (
          <p className="text-neutral-500">Loading sources…</p>
        ) : state.status === "error" ? (
          <p className="text-red-700" role="alert">
            Failed to load sources: {state.message}
          </p>
        ) : state.sources.length === 0 ? (
          <div
            className="rounded-lg border border-dashed border-neutral-300 p-10 text-center"
            data-testid="sources-empty-state"
          >
            <p className="text-neutral-600">No sources uploaded yet.</p>
            <p className="mt-1 text-sm text-neutral-500">
              Drop a file in the upload zone above to feed the RAG index.
            </p>
          </div>
        ) : (
          <SourcesTable
            sources={state.sources}
            projectId={projectId}
            apiClient={apiClient}
            onChanged={refresh}
          />
        )}
      </section>
    </main>
  );
}

// ---------------------------------------------------------------------------
// Upload card — drag-drop zone + file picker + form.
// ---------------------------------------------------------------------------

function UploadCard({
  projectId,
  apiClient,
  onUploaded,
}: {
  projectId: string;
  apiClient: ApiClient | null;
  onUploaded: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [sourceId, setSourceId] = useState("");
  const [mime, setMime] = useState<SupportedMimeType | "">("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  function applyFile(f: File): void {
    setError(null);
    const detected = mimeTypeFromFilename(f.name);
    if (!detected) {
      const supported = Object.values(SUPPORTED_MIME_TYPES)
        .flatMap((info) => info.ext)
        .join(", ");
      setError(`Unsupported file extension. Supported: ${supported}`);
      setFile(null);
      return;
    }
    setFile(f);
    setMime(detected);
    setSourceId(filenameToSourceId(f.name));
  }

  function onFileInput(e: React.ChangeEvent<HTMLInputElement>): void {
    const f = e.target.files?.[0];
    if (f) applyFile(f);
  }

  function onDrop(e: React.DragEvent<HTMLLabelElement>): void {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) applyFile(f);
  }

  function reset(): void {
    setFile(null);
    setSourceId("");
    setMime("");
    setError(null);
  }

  async function onSubmit(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    if (!apiClient || !file || !mime || !sourceId) return;
    setSubmitting(true);
    setError(null);
    try {
      await apiClient.uploadSource(projectId, file, sourceId, mime);
      reset();
      onUploaded();
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`Upload failed (HTTP ${err.status}): ${err.body || "(no body)"}`);
      } else {
        setError(String(err));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section
      className="mt-6 rounded-lg border border-neutral-200 bg-white p-5"
      data-testid="upload-card"
    >
      <h3 className="text-sm font-medium uppercase tracking-wide text-neutral-500">
        Upload a source
      </h3>
      <form onSubmit={onSubmit} className="mt-3 space-y-3">
        {!file ? (
          /* The whole dropzone is a `<label>` wrapping the hidden file
           * input. Click anywhere → opens the picker (browser-native
           * behaviour). Drag-drop is intercepted on the same element.
           * a11y : the inner input is the focusable, keyboard-operable
           * affordance ; the label is the visual surface. */
          <label
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            className={[
              "block cursor-pointer rounded-md border-2 border-dashed p-8 text-center transition-colors",
              dragOver ? "border-blue-400 bg-blue-50" : "border-neutral-300 bg-neutral-50",
            ].join(" ")}
            data-testid="upload-dropzone"
          >
            <input
              type="file"
              className="sr-only"
              onChange={onFileInput}
              accept={Object.values(SUPPORTED_MIME_TYPES)
                .flatMap((info) => info.ext)
                .join(",")}
              data-testid="upload-file-input"
            />
            <p className="text-sm text-neutral-700">
              <span className="font-medium text-blue-700 underline">Pick a file</span> or drop one
              here.
            </p>
            <p className="mt-1 text-xs text-neutral-500">
              Accepted :{" "}
              {Object.entries(SUPPORTED_MIME_TYPES)
                .map(([_mime, info]) => info.label)
                .join(" · ")}
            </p>
          </label>
        ) : (
          <div
            className="flex items-center justify-between rounded-md border border-neutral-200 p-3"
            data-testid="upload-staged-file"
          >
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-neutral-900">{file.name}</p>
              <p className="text-xs text-neutral-500">
                {formatBytes(file.size)} · {mime ? SUPPORTED_MIME_TYPES[mime].label : "Unknown"}
              </p>
            </div>
            <button
              type="button"
              onClick={reset}
              className="rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50"
            >
              Clear
            </button>
          </div>
        )}

        {file ? (
          <label className="block">
            <span className="text-xs uppercase tracking-wide text-neutral-500">
              Source id (auto-derived, editable)
            </span>
            <input
              type="text"
              value={sourceId}
              onChange={(e) => setSourceId(e.target.value)}
              className="mt-1 block w-full rounded-md border border-neutral-300 px-3 py-1.5 font-mono text-xs"
              data-testid="upload-source-id-input"
              required
            />
          </label>
        ) : null}

        {error ? (
          <p className="text-sm text-red-700" role="alert" data-testid="upload-error">
            {error}
          </p>
        ) : null}

        {file ? (
          <button
            type="submit"
            disabled={submitting || !apiClient}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            data-testid="upload-submit"
          >
            {submitting ? "Uploading…" : "Upload"}
          </button>
        ) : null}
      </form>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Sources table — list view with status badges + per-row delete.
// ---------------------------------------------------------------------------

function SourcesTable({
  sources,
  projectId,
  apiClient,
  onChanged,
}: {
  sources: Source[];
  projectId: string;
  apiClient: ApiClient | null;
  onChanged: () => void;
}) {
  return (
    <div
      className="overflow-hidden rounded-lg border border-neutral-200 bg-white"
      data-testid="sources-table"
    >
      <table className="min-w-full text-sm">
        <thead className="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
          <tr>
            <th className="px-4 py-2">Source</th>
            <th className="px-4 py-2">Type</th>
            <th className="px-4 py-2">Size</th>
            <th className="px-4 py-2">Status</th>
            <th className="px-4 py-2">Chunks</th>
            <th className="px-4 py-2">Uploaded</th>
            <th className="px-4 py-2"></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-neutral-100">
          {sources.map((s) => (
            <SourceRow
              key={s.source_id}
              source={s}
              projectId={projectId}
              apiClient={apiClient}
              onChanged={onChanged}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SourceRow({
  source,
  projectId,
  apiClient,
  onChanged,
}: {
  source: Source;
  projectId: string;
  apiClient: ApiClient | null;
  onChanged: () => void;
}) {
  const [deleting, setDeleting] = useState(false);
  const mimeLabel =
    SUPPORTED_MIME_TYPES[source.mime_type as SupportedMimeType]?.label ?? source.mime_type;

  async function onDelete(): Promise<void> {
    if (!apiClient) return;
    if (!window.confirm(`Delete source ${source.source_id}? This cannot be undone.`)) {
      return;
    }
    setDeleting(true);
    try {
      await apiClient.deleteSource(projectId, source.source_id);
      onChanged();
    } catch (err) {
      window.alert(`Delete failed: ${String(err)}`);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <tr data-testid={`source-row-${source.source_id}`}>
      <td className="px-4 py-2">
        <Link
          href={`/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(source.source_id)}`}
          className="font-mono text-xs text-blue-700 hover:underline"
        >
          {source.source_id}
        </Link>
      </td>
      <td className="px-4 py-2 text-xs text-neutral-700">{mimeLabel}</td>
      <td className="px-4 py-2 text-xs text-neutral-700">{formatBytes(source.size_bytes)}</td>
      <td className="px-4 py-2">
        <StatusBadge status={source.parse_status} error={source.parse_error} />
      </td>
      <td className="px-4 py-2 text-xs text-neutral-700">{source.chunk_count}</td>
      <td className="px-4 py-2 text-xs text-neutral-500">
        {new Date(source.uploaded_at).toLocaleDateString()} · {source.uploaded_by}
      </td>
      <td className="px-4 py-2 text-right">
        <button
          type="button"
          onClick={onDelete}
          disabled={deleting}
          className="rounded-md border border-red-200 px-2 py-1 text-xs text-red-700 hover:bg-red-50 disabled:opacity-50"
          data-testid={`source-delete-${source.source_id}`}
        >
          {deleting ? "Deleting…" : "Delete"}
        </button>
      </td>
    </tr>
  );
}

function StatusBadge({ status, error }: { status: Source["parse_status"]; error: string | null }) {
  const palette: Record<Source["parse_status"], string> = {
    pending: "bg-neutral-100 text-neutral-700",
    parsed: "bg-amber-100 text-amber-900",
    indexed: "bg-emerald-100 text-emerald-900",
    failed: "bg-red-100 text-red-900",
  };
  return (
    <span
      className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${palette[status]}`}
      title={error ?? undefined}
    >
      {status}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Turn a filename into a URL/key-safe source_id. */
function filenameToSourceId(filename: string): string {
  // Strip extension, lowercase, replace non-alnum with dashes, dedupe dashes.
  const noExt = filename.replace(/\.[^./]+$/, "");
  return (
    noExt
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 64) || `source-${Date.now()}`
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
