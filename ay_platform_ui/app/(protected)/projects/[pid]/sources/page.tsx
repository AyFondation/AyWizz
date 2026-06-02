// =============================================================================
// File: page.tsx
// Version: 5
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
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { PageShell, Panel, PanelBody, PanelHeader } from "@/components/panel";
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

/** Stable empty reference so `useMemo` deps don't change identity every
 *  render while the list is loading. */
const EMPTY_SOURCES: Source[] = [];

/** Ownership scope of a source. v1: every source is project-owned unless a
 *  future tenant-common corpus sets `ownership_scope` (option B). */
function sourceOwnerScope(source: Source): string {
  return (source as { ownership_scope?: string }).ownership_scope ?? "project";
}

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

  // ---- Client-side filtering (search + owner/type facets) -----------------
  const [query, setQuery] = useState("");
  const [excludedOwners, setExcludedOwners] = useState<Set<string>>(new Set());
  const [excludedTypes, setExcludedTypes] = useState<Set<string>>(new Set());

  const allSources = state.status === "ready" ? state.sources : EMPTY_SOURCES;

  // Facets derived from the FULL list so toggles stay stable as the query
  // narrows the visible rows.
  const owners = useMemo(() => {
    const keys = [...new Set(allSources.map(sourceOwnerScope))].sort();
    return keys.map((key) => ({ key, label: key.charAt(0).toUpperCase() + key.slice(1) }));
  }, [allSources]);
  const types = useMemo(() => {
    const keys = [...new Set(allSources.map((s) => s.mime_type))].sort();
    return keys.map((key) => ({
      key,
      label: SUPPORTED_MIME_TYPES[key as SupportedMimeType]?.label ?? key,
    }));
  }, [allSources]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return allSources.filter(
      (s) =>
        (q === "" || s.source_id.toLowerCase().includes(q)) &&
        !excludedOwners.has(sourceOwnerScope(s)) &&
        !excludedTypes.has(s.mime_type),
    );
  }, [allSources, query, excludedOwners, excludedTypes]);

  const toggle = (set: Set<string>, key: string): Set<string> => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  };

  return (
    <PageShell>
      <main className="mx-auto max-w-7xl px-6 py-10">
        <header className="flex flex-wrap items-baseline justify-between gap-3 border-b border-neutral-200 pb-5">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight text-neutral-900">Sources</h2>
            <p className="mt-1 text-sm text-neutral-500">
              Upload and manage the source corpus feeding RAG retrieval.
            </p>
          </div>
          {state.status === "ready" ? (
            <span
              className="rounded-full bg-indigo-50 px-2.5 py-1 text-sm font-medium text-indigo-700"
              data-testid="sources-count"
            >
              {filtered.length === allSources.length
                ? `${allSources.length} source${allSources.length === 1 ? "" : "s"}`
                : `${filtered.length} / ${allSources.length} sources`}
            </span>
          ) : null}
        </header>

        <div className="mt-6 space-y-6">
          <UploadCard projectId={projectId} apiClient={apiClient} onUploaded={refresh} />

          {state.status === "loading" ? (
            <p className="text-neutral-500">Loading sources…</p>
          ) : state.status === "error" ? (
            <p className="text-red-700" role="alert">
              Failed to load sources: {state.message}
            </p>
          ) : state.sources.length === 0 ? (
            <div
              className="rounded-xl border border-dashed border-neutral-300 bg-white p-10 text-center"
              data-testid="sources-empty-state"
            >
              <p className="text-neutral-600">No sources uploaded yet.</p>
              <p className="mt-1 text-sm text-neutral-500">
                Drop a file in the upload zone above to feed the RAG index.
              </p>
            </div>
          ) : (
            <>
              <SourcesFilterBar
                query={query}
                onQuery={setQuery}
                owners={owners}
                types={types}
                excludedOwners={excludedOwners}
                excludedTypes={excludedTypes}
                onToggleOwner={(k) => setExcludedOwners((s) => toggle(s, k))}
                onToggleType={(k) => setExcludedTypes((s) => toggle(s, k))}
              />
              {filtered.length === 0 ? (
                <div
                  className="rounded-xl border border-dashed border-neutral-300 bg-white p-10 text-center"
                  data-testid="sources-no-matches"
                >
                  <p className="text-neutral-600">No sources match the current filters.</p>
                  <p className="mt-1 text-sm text-neutral-500">
                    Adjust the search term or re-enable an owner / type.
                  </p>
                </div>
              ) : (
                <SourcesTable
                  sources={filtered}
                  projectId={projectId}
                  apiClient={apiClient}
                  onChanged={refresh}
                />
              )}
            </>
          )}
        </div>
      </main>
    </PageShell>
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
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [rejected, setRejected] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const idRef = useRef(0);

  /** Add dropped / picked files to the queue. Supported files become
   *  `pending` items ; unsupported ones are skipped (not queued) and
   *  reported as an aggregated rejection note — they never block the rest. */
  function addFiles(files: File[]): void {
    if (files.length === 0) return;
    const accepted: QueueItem[] = [];
    const bad: string[] = [];
    for (const f of files) {
      const detected = mimeTypeFromFilename(f.name);
      if (!detected) {
        bad.push(f.name);
        continue;
      }
      idRef.current += 1;
      accepted.push({
        id: `q${idRef.current}`,
        file: f,
        sourceId: filenameToSourceId(f.name),
        mime: detected,
        status: "pending",
      });
    }
    if (accepted.length > 0) setQueue((q) => [...q, ...accepted]);
    if (bad.length > 0) {
      const supported = Object.values(SUPPORTED_MIME_TYPES)
        .flatMap((info) => info.ext)
        .join(", ");
      setRejected(
        `Unsupported file extension. Supported: ${supported}. Skipped: ${bad.join(", ")}`,
      );
    } else {
      setRejected(null);
    }
  }

  function onFileInput(e: React.ChangeEvent<HTMLInputElement>): void {
    addFiles(Array.from(e.target.files ?? []));
    // Reset the input so re-picking the same file(s) fires onChange again.
    e.target.value = "";
  }

  function onDrop(e: React.DragEvent<HTMLLabelElement>): void {
    e.preventDefault();
    setDragOver(false);
    addFiles(Array.from(e.dataTransfer.files ?? []));
  }

  function patchItem(id: string, patch: Partial<QueueItem>): void {
    setQueue((q) => q.map((i) => (i.id === id ? { ...i, ...patch } : i)));
  }

  function removeItem(id: string): void {
    setQueue((q) => q.filter((i) => i.id !== id));
  }

  function clearAll(): void {
    setQueue([]);
    setRejected(null);
  }

  const pendingCount = queue.filter((i) => i.status === "pending").length;

  /** Upload every `pending` item ONE AT A TIME, in order. A failure on one
   *  file marks it `error` and continues with the next (never aborts the
   *  batch). The list refreshes once at the end if anything succeeded. */
  async function onSubmit(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    if (!apiClient || submitting) return;
    const pending = queue.filter((i) => i.status === "pending");
    if (pending.length === 0) return;
    setSubmitting(true);
    let anyDone = false;
    for (const item of pending) {
      patchItem(item.id, { status: "uploading", error: undefined });
      try {
        await apiClient.uploadSource(projectId, item.file, item.sourceId, item.mime);
        patchItem(item.id, { status: "done" });
        anyDone = true;
      } catch (err) {
        const msg =
          err instanceof ApiError ? `HTTP ${err.status}: ${err.body || "(no body)"}` : String(err);
        patchItem(item.id, { status: "error", error: msg });
      }
    }
    setSubmitting(false);
    if (anyDone) onUploaded();
  }

  return (
    <Panel testId="upload-card">
      <PanelHeader title="Upload sources" />
      <PanelBody>
        <form onSubmit={onSubmit} className="space-y-3">
          {/* The dropzone is a `<label>` wrapping the hidden multi-file
           * input. Click → opens the picker ; drag-drop is intercepted on
           * the same element. It stays visible so more files can be added
           * to the queue at any time. */}
          <label
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            className={[
              "block cursor-pointer rounded-md border-2 border-dashed p-8 text-center transition-colors",
              dragOver ? "border-indigo-400 bg-indigo-50" : "border-neutral-300 bg-neutral-50",
            ].join(" ")}
            data-testid="upload-dropzone"
          >
            <input
              type="file"
              multiple
              className="sr-only"
              onChange={onFileInput}
              accept={Object.values(SUPPORTED_MIME_TYPES)
                .flatMap((info) => info.ext)
                .join(",")}
              data-testid="upload-file-input"
            />
            <p className="text-sm text-neutral-700">
              <span className="font-medium text-indigo-700 underline">Pick files</span> or drop one
              or more here.
            </p>
            <p className="mt-1 text-xs text-neutral-500">
              Accepted :{" "}
              {Object.entries(SUPPORTED_MIME_TYPES)
                .map(([_mime, info]) => info.label)
                .join(" · ")}
            </p>
          </label>

          {rejected ? (
            <p className="text-sm text-red-700" role="alert" data-testid="upload-error">
              {rejected}
            </p>
          ) : null}

          {queue.length > 0 ? (
            <ul className="space-y-2" data-testid="upload-queue">
              {queue.map((item) => (
                <li
                  key={item.id}
                  className="rounded-md border border-neutral-200 p-3"
                  data-testid="upload-staged-file"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-neutral-900">
                        {item.file.name}
                      </p>
                      <p className="text-xs text-neutral-500">
                        {formatBytes(item.file.size)} · {SUPPORTED_MIME_TYPES[item.mime].label}
                      </p>
                    </div>
                    <UploadStatusBadge status={item.status} />
                  </div>

                  {item.status === "pending" ? (
                    <div className="mt-2 flex items-end gap-2">
                      <label className="block min-w-0 flex-1">
                        <span className="text-[11px] uppercase tracking-wide text-neutral-500">
                          Source id (editable)
                        </span>
                        <input
                          type="text"
                          value={item.sourceId}
                          onChange={(e) => patchItem(item.id, { sourceId: e.target.value })}
                          className="mt-1 block w-full rounded-md border border-neutral-300 px-3 py-1.5 font-mono text-xs"
                          data-testid="upload-source-id-input"
                          required
                        />
                      </label>
                      <button
                        type="button"
                        onClick={() => removeItem(item.id)}
                        disabled={submitting}
                        className="rounded-md border border-neutral-300 px-2 py-1.5 text-xs text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
                        data-testid="upload-remove"
                        aria-label={`Remove ${item.file.name}`}
                      >
                        ✕
                      </button>
                    </div>
                  ) : null}

                  {item.status === "error" && item.error ? (
                    <p className="mt-2 text-xs text-red-700">{item.error}</p>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}

          {queue.length > 0 ? (
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={submitting || pendingCount === 0 || !apiClient}
                className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-indigo-700 disabled:opacity-50"
                data-testid="upload-submit"
              >
                {submitting
                  ? "Uploading…"
                  : `Upload ${pendingCount} file${pendingCount === 1 ? "" : "s"}`}
              </button>
              <button
                type="button"
                onClick={clearAll}
                disabled={submitting}
                className="rounded-md border border-neutral-300 px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
                data-testid="upload-clear"
              >
                Clear
              </button>
            </div>
          ) : null}
        </form>
      </PanelBody>
    </Panel>
  );
}

type UploadStatus = "pending" | "uploading" | "done" | "error";
type QueueItem = {
  id: string;
  file: File;
  sourceId: string;
  mime: SupportedMimeType;
  status: UploadStatus;
  error?: string;
};

function UploadStatusBadge({ status }: { status: UploadStatus }) {
  const map: Record<UploadStatus, { label: string; cls: string }> = {
    pending: { label: "queued", cls: "bg-neutral-100 text-neutral-600" },
    uploading: { label: "uploading…", cls: "bg-indigo-100 text-indigo-700" },
    done: { label: "✓ uploaded", cls: "bg-emerald-100 text-emerald-800" },
    error: { label: "✗ failed", cls: "bg-red-100 text-red-800" },
  };
  const { label, cls } = map[status];
  return (
    <span
      className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${cls}`}
      data-testid="upload-status"
    >
      {label}
    </span>
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
      className="overflow-hidden rounded-xl border border-neutral-200 bg-white shadow-sm"
      data-testid="sources-table"
    >
      <table className="min-w-full text-sm">
        <thead className="border-b border-neutral-200 bg-neutral-100 text-left text-xs font-semibold uppercase tracking-wide text-neutral-600">
          <tr>
            <th className="px-4 py-2.5">Source</th>
            <th className="px-4 py-2.5">Type</th>
            <th className="px-4 py-2.5">Size</th>
            <th className="px-4 py-2.5">Status</th>
            <th className="px-4 py-2.5">Chunks</th>
            <th className="px-4 py-2.5">Owner</th>
            <th className="px-4 py-2.5">Uploaded</th>
            <th className="px-4 py-2.5"></th>
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

/** Ownership badge (R-100-083). v1: every source is per-project, so it is
 *  owned by the project. A future tenant-common corpus (option B) will set an
 *  `ownership_scope` field; until then this always reads "Project". */
function OwnerBadge({ source }: { source: Source }) {
  const scope = (source as { ownership_scope?: "project" | "tenant" }).ownership_scope ?? "project";
  const isTenant = scope === "tenant";
  return (
    <span
      className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
        isTenant ? "bg-purple-50 text-purple-700" : "bg-blue-50 text-blue-700"
      }`}
      title={
        isTenant
          ? "Tenant-common document — owned by the tenant owner."
          : "Project-owned document — owned by this project's owner."
      }
    >
      {isTenant ? "Tenant" : "Project"}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Filter bar — live text search + owner/type facet toggles.
// ---------------------------------------------------------------------------

type Facet = { key: string; label: string };

function SourcesFilterBar({
  query,
  onQuery,
  owners,
  types,
  excludedOwners,
  excludedTypes,
  onToggleOwner,
  onToggleType,
}: {
  query: string;
  onQuery: (q: string) => void;
  owners: Facet[];
  types: Facet[];
  excludedOwners: Set<string>;
  excludedTypes: Set<string>;
  onToggleOwner: (key: string) => void;
  onToggleType: (key: string) => void;
}) {
  return (
    <Panel testId="sources-filter">
      <PanelBody className="space-y-3">
        <div className="relative">
          <input
            type="text"
            value={query}
            onChange={(e) => onQuery(e.target.value)}
            placeholder="Search by name (word, syllable, part of a word)…"
            className="block w-full rounded-md border border-neutral-300 py-2 pr-9 pl-3 text-sm focus:border-indigo-400 focus:outline-none focus:ring-1 focus:ring-indigo-400"
            data-testid="sources-search-input"
            aria-label="Search sources"
          />
          {query ? (
            <button
              type="button"
              onClick={() => onQuery("")}
              className="absolute top-1/2 right-2 -translate-y-1/2 rounded-full p-1 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700"
              data-testid="sources-search-clear"
              aria-label="Clear search"
            >
              ✕
            </button>
          ) : null}
        </div>

        {owners.length > 0 || types.length > 0 ? (
          <div className="flex flex-col gap-3 sm:flex-row sm:gap-8">
            {owners.length > 0 ? (
              <FacetGroup label="Owners" testId="filter-owners">
                {owners.map((o) => (
                  <FilterChip
                    key={o.key}
                    label={o.label}
                    active={!excludedOwners.has(o.key)}
                    onClick={() => onToggleOwner(o.key)}
                    testId={`filter-owner-${o.key}`}
                  />
                ))}
              </FacetGroup>
            ) : null}
            {types.length > 0 ? (
              <FacetGroup label="File types" testId="filter-types">
                {types.map((t) => (
                  <FilterChip
                    key={t.key}
                    label={t.label}
                    active={!excludedTypes.has(t.key)}
                    onClick={() => onToggleType(t.key)}
                    testId={`filter-type-${t.key}`}
                  />
                ))}
              </FacetGroup>
            ) : null}
          </div>
        ) : null}
      </PanelBody>
    </Panel>
  );
}

function FacetGroup({
  label,
  testId,
  children,
}: {
  label: string;
  testId: string;
  children: React.ReactNode;
}) {
  return (
    <div data-testid={testId}>
      <span className="text-xs font-medium uppercase tracking-wide text-neutral-500">{label}</span>
      <div className="mt-1.5 flex flex-wrap gap-2">{children}</div>
    </div>
  );
}

function FilterChip({
  label,
  active,
  onClick,
  testId,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  testId: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      data-testid={testId}
      className={
        active
          ? "inline-flex items-center gap-1 rounded-full border border-indigo-200 bg-indigo-50 px-2.5 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-100"
          : "inline-flex items-center gap-1 rounded-full border border-neutral-200 bg-white px-2.5 py-1 text-xs font-medium text-neutral-400 line-through hover:bg-neutral-50"
      }
    >
      <span aria-hidden="true">{active ? "✓" : "+"}</span>
      {label}
    </button>
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
      <td className="px-4 py-2">
        <OwnerBadge source={source} />
      </td>
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
