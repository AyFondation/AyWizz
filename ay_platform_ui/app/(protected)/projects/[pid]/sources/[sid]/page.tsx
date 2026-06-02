// =============================================================================
// File: page.tsx
// Version: 5
// Path: ay_platform_ui/app/(protected)/projects/[pid]/sources/[sid]/page.tsx
// Description: Per-source detail view. Surfaces every metadata field
//              C7 exposes + (v2) an "Ingestion & storage" diagnostics
//              panel: MinIO storage locations (raw + C13 run artifacts),
//              per-chunk status from the DB, and a LIVE poll of the
//              ingestion status while the source is `pending`.
//              v3 adds the transparency surface (R-400-221): expandable
//              chunk content + chunk download (single / zip), an
//              "Extractions" table listing every C13 run with its parser
//              version, and a per-run artifact file-browser (view +
//              single-file / zip download). Actions: download the raw
//              blob and delete the source (role-gated).
// =============================================================================

"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Fragment, useEffect, useMemo, useState } from "react";

import { PageShell, Panel, PanelBody, PanelHeader, SubHeading } from "@/components/panel";
import { ApiClient, ApiError } from "@/lib/apiClient";
import {
  type ArtifactEntry,
  type ChunkContent,
  type ExtractionRunInfo,
  type RunArtifactListing,
  type Source,
  type SourceDiagnostics,
  type SourceRunListing,
  SUPPORTED_MIME_TYPES,
  type SupportedMimeType,
} from "@/lib/types";

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
  const [diag, setDiag] = useState<SourceDiagnostics | null>(null);
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

  // Ingestion diagnostics — fetched once the source loads, then POLLED live
  // every 3s while the source is still `pending` (the closest thing to an
  // inline trace for the async C12 → C13 → C7 pipeline). Stops at terminal.
  useEffect(() => {
    if (!apiClient || state.status !== "ready") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll(): Promise<void> {
      if (cancelled || !apiClient) return;
      try {
        const d = await apiClient.getSourceDiagnostics(projectId, sourceId);
        if (cancelled) return;
        setDiag(d);
        if (d.parse_status === "pending") timer = setTimeout(poll, 3000);
      } catch {
        // transient — retry on the next tick if still pending
        if (!cancelled) timer = setTimeout(poll, 5000);
      }
    }
    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [apiClient, projectId, sourceId, state.status]);

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
    <PageShell>
      <main className="mx-auto max-w-5xl px-6 py-10" data-testid="source-detail">
        <nav className="text-xs font-medium text-neutral-500" aria-label="Breadcrumb">
          <Link
            href={`/projects/${encodeURIComponent(projectId)}/sources`}
            className="inline-flex items-center gap-1 hover:text-indigo-700 hover:underline"
          >
            ← Sources
          </Link>
        </nav>
        <header className="mt-3 flex flex-wrap items-start justify-between gap-3 border-b border-neutral-200 pb-5">
          <div className="min-w-0">
            <span className="inline-block rounded bg-indigo-50 px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-indigo-700">
              {mimeLabel}
            </span>
            <h2 className="mt-1.5 break-all font-mono text-xl font-semibold text-neutral-900">
              {source.source_id}
            </h2>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onDownload}
              disabled={downloading}
              className="rounded-md border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-sm font-medium text-indigo-700 hover:bg-indigo-100 disabled:opacity-50"
              data-testid="source-download"
            >
              {downloading ? "Downloading…" : "↓ Download"}
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

        <div className="mt-6 space-y-6">
          <Panel>
            <PanelHeader title="Metadata" />
            <PanelBody>
              <dl className="grid grid-cols-1 gap-x-6 gap-y-3 text-sm md:grid-cols-2">
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
            </PanelBody>
          </Panel>

          <DiagnosticsPanel
            diag={diag}
            apiClient={apiClient}
            projectId={projectId}
            sourceId={sourceId}
          />
          <RunsArtifactsPanel apiClient={apiClient} projectId={projectId} sourceId={sourceId} />
        </div>
      </main>
    </PageShell>
  );
}

/** Programmatic download : anchor-click an object URL, then revoke it. */
function triggerBlobDownload(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objectUrl);
}

/** A content type is renderable inline when it is textual. */
function isTextual(contentType: string | null): boolean {
  if (!contentType) return false;
  return (
    contentType.startsWith("text/") ||
    contentType.includes("json") ||
    contentType.includes("ndjson") ||
    contentType.includes("xml") ||
    contentType.includes("yaml")
  );
}

function DiagnosticsPanel({
  diag,
  apiClient,
  projectId,
  sourceId,
}: {
  diag: SourceDiagnostics | null;
  apiClient: ApiClient | null;
  projectId: string;
  sourceId: string;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [contents, setContents] = useState<Record<string, ChunkContent>>({});
  const [loadingChunk, setLoadingChunk] = useState<string | null>(null);
  const [chunkError, setChunkError] = useState<string | null>(null);
  const [zipping, setZipping] = useState(false);

  async function toggleChunk(chunkId: string): Promise<void> {
    if (expanded === chunkId) {
      setExpanded(null);
      return;
    }
    setExpanded(chunkId);
    setChunkError(null);
    if (contents[chunkId] || !apiClient) return;
    setLoadingChunk(chunkId);
    try {
      const content = await apiClient.getChunkContent(projectId, sourceId, chunkId);
      setContents((prev) => ({ ...prev, [chunkId]: content }));
    } catch (err) {
      setChunkError(`Failed to load chunk ${chunkId}: ${String(err)}`);
    } finally {
      setLoadingChunk(null);
    }
  }

  function downloadChunk(content: ChunkContent): void {
    const blob = new Blob([JSON.stringify(content, null, 2)], { type: "application/json" });
    triggerBlobDownload(blob, `${content.chunk_id.replace(/[/:]/g, "_")}.json`);
  }

  async function downloadAllChunks(): Promise<void> {
    if (!apiClient) return;
    setZipping(true);
    try {
      const { blob, filename } = await apiClient.downloadChunksZip(projectId, sourceId);
      triggerBlobDownload(blob, filename ?? `${sourceId}_chunks.zip`);
    } catch (err) {
      window.alert(`Chunk zip download failed: ${String(err)}`);
    } finally {
      setZipping(false);
    }
  }

  if (diag === null) {
    return (
      <Panel>
        <PanelHeader title="Ingestion & storage" />
        <PanelBody className="text-sm text-neutral-500">Loading ingestion diagnostics…</PanelBody>
      </Panel>
    );
  }
  const pending = diag.parse_status === "pending";
  return (
    <Panel testId="source-diagnostics">
      <PanelHeader
        title="Ingestion & storage"
        actions={
          pending ? (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700">
              <span className="h-2 w-2 animate-pulse rounded-full bg-amber-500" />
              live · processing…
            </span>
          ) : null
        }
      />
      <PanelBody className="space-y-6">
        <dl className="grid grid-cols-1 gap-x-6 gap-y-3 text-sm md:grid-cols-2">
          <Field
            label="Status"
            value={diag.parse_status}
            badge={parseStatusPalette[diag.parse_status]}
          />
          <Field label="Chunks indexed" value={String(diag.chunk_count)} />
          <Field label="Extraction run" value={diag.extraction_run_id ?? "(not started)"} mono />
          <Field label="Embedding model" value={diag.model_id ?? "(none)"} mono />
        </dl>

        {/* MinIO storage locations */}
        <div>
          <SubHeading>MinIO storage</SubHeading>
          <dl className="mt-3 space-y-1.5 rounded-md border border-neutral-200 bg-neutral-50/60 p-3 text-xs">
            <StoragePath
              label="Raw object"
              bucket={diag.storage.raw_bucket}
              objectKey={diag.storage.raw_object_key}
            />
            <StoragePath
              label="Run artifacts"
              bucket={diag.storage.artifacts_bucket}
              objectKey={diag.storage.artifacts_prefix}
            />
            <StoragePath
              label="chunks.jsonl"
              bucket={diag.storage.artifacts_bucket}
              objectKey={diag.storage.chunks_jsonl_key}
            />
            <StoragePath
              label="run_manifest.json"
              bucket={diag.storage.artifacts_bucket}
              objectKey={diag.storage.manifest_key}
            />
          </dl>
        </div>

        {/* Per-chunk status from the DB — rows expand to show content */}
        <div>
          <SubHeading
            actions={
              diag.chunks.length > 0 ? (
                <button
                  type="button"
                  onClick={downloadAllChunks}
                  disabled={zipping}
                  className="rounded border border-neutral-300 bg-white px-2 py-1 text-xs font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
                  data-testid="download-chunks-zip"
                >
                  {zipping ? "Zipping…" : "↓ Download all (zip)"}
                </button>
              ) : null
            }
          >
            Chunks in database ({diag.chunks.length})
          </SubHeading>
          {chunkError ? (
            <p className="mt-2 text-xs text-red-700" role="alert">
              {chunkError}
            </p>
          ) : null}
          {diag.chunks.length === 0 ? (
            <p className="mt-3 text-xs text-neutral-500">
              {pending ? "No chunks yet — extraction in progress." : "No chunks indexed."}
            </p>
          ) : (
            <div className="mt-3 overflow-hidden rounded-md border border-neutral-200">
              <table className="min-w-full text-xs" data-testid="diagnostics-chunks">
                <thead className="border-b border-neutral-200 bg-neutral-100 text-left font-semibold uppercase tracking-wide text-neutral-600">
                  <tr>
                    <th className="px-3 py-2">#</th>
                    <th className="px-3 py-2">Chunk id</th>
                    <th className="px-3 py-2">Tokens</th>
                    <th className="px-3 py-2">Char range</th>
                    <th className="px-3 py-2">Embedding</th>
                    <th className="px-3 py-2" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-neutral-100">
                  {diag.chunks.map((c) => {
                    const isOpen = expanded === c.chunk_id;
                    const content = contents[c.chunk_id];
                    return (
                      <Fragment key={c.chunk_id}>
                        <tr
                          className={`cursor-pointer hover:bg-indigo-50/50 ${isOpen ? "bg-indigo-50/60" : ""}`}
                          onClick={() => void toggleChunk(c.chunk_id)}
                          data-testid="chunk-row"
                        >
                          <td className="px-3 py-2 text-neutral-500">{c.seq}</td>
                          <td className="px-3 py-2 font-mono text-neutral-800">{c.chunk_id}</td>
                          <td className="px-3 py-2 text-neutral-700">{c.token_count}</td>
                          <td className="px-3 py-2 text-neutral-500">
                            {c.char_start}–{c.char_end}
                          </td>
                          <td className="px-3 py-2">
                            {c.has_embedding ? (
                              <span className="font-medium text-emerald-700">✓ vector</span>
                            ) : (
                              <span className="font-medium text-red-700">✗ missing</span>
                            )}
                          </td>
                          <td className="px-3 py-2 text-indigo-500">{isOpen ? "▼" : "▶"}</td>
                        </tr>
                        {isOpen ? (
                          <tr className="bg-neutral-50">
                            <td colSpan={6} className="px-3 py-3">
                              {loadingChunk === c.chunk_id ? (
                                <p className="text-neutral-500">Loading content…</p>
                              ) : content ? (
                                <div className="space-y-2" data-testid="chunk-content">
                                  {content.section_path.length > 0 ? (
                                    <p className="text-neutral-500">
                                      Section: {content.section_path.join(" › ")}
                                    </p>
                                  ) : null}
                                  {content.original_text &&
                                  content.original_text !== content.content ? (
                                    <span className="inline-flex rounded-full bg-indigo-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-indigo-800">
                                      decontextualized
                                    </span>
                                  ) : null}
                                  <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded border border-neutral-200 bg-white p-2 font-mono text-[11px] text-neutral-800">
                                    {content.content}
                                  </pre>
                                  {content.original_text &&
                                  content.original_text !== content.content ? (
                                    <details data-testid="chunk-original">
                                      <summary className="cursor-pointer text-neutral-500">
                                        Original (before disambiguation)
                                      </summary>
                                      <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded border border-neutral-200 bg-white p-2 font-mono text-[11px] text-neutral-500">
                                        {content.original_text}
                                      </pre>
                                    </details>
                                  ) : null}
                                  {content.context ? (
                                    <details>
                                      <summary className="cursor-pointer text-neutral-500">
                                        Context summary
                                      </summary>
                                      <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded border border-neutral-200 bg-white p-2 font-mono text-[11px] text-neutral-600">
                                        {content.context}
                                      </pre>
                                    </details>
                                  ) : null}
                                  <button
                                    type="button"
                                    onClick={() => downloadChunk(content)}
                                    className="rounded border border-neutral-300 bg-white px-2 py-1 text-[11px] font-medium text-neutral-700 hover:bg-neutral-100"
                                    data-testid="download-chunk"
                                  >
                                    ↓ Download chunk (JSON)
                                  </button>
                                </div>
                              ) : (
                                <p className="text-neutral-400">No content.</p>
                              )}
                            </td>
                          </tr>
                        ) : null}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </PanelBody>
    </Panel>
  );
}

function RunsArtifactsPanel({
  apiClient,
  projectId,
  sourceId,
}: {
  apiClient: ApiClient | null;
  projectId: string;
  sourceId: string;
}) {
  const [listing, setListing] = useState<SourceRunListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);

  useEffect(() => {
    if (!apiClient) return;
    let cancelled = false;
    setListing(null);
    setError(null);
    apiClient
      .listSourceRuns(projectId, sourceId)
      .then((l) => {
        if (cancelled) return;
        setListing(l);
        setSelectedRun(l.active_run_id ?? l.runs[0]?.run_id ?? null);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, projectId, sourceId]);

  return (
    <Panel testId="source-runs">
      <PanelHeader title="Extractions" />
      <PanelBody>
        {error ? (
          <p className="text-xs text-red-700" role="alert">
            Failed to load runs: {error}
          </p>
        ) : listing === null ? (
          <p className="text-sm text-neutral-500">Loading extraction runs…</p>
        ) : listing.runs.length === 0 ? (
          <p className="text-sm text-neutral-500">No extraction runs found for this source.</p>
        ) : (
          <>
            <div className="overflow-hidden rounded-md border border-neutral-200">
              <table className="min-w-full text-xs" data-testid="runs-table">
                <thead className="border-b border-neutral-200 bg-neutral-100 text-left font-semibold uppercase tracking-wide text-neutral-600">
                  <tr>
                    <th className="px-3 py-2">Run id</th>
                    <th className="px-3 py-2">Extractor version</th>
                    <th className="px-3 py-2">Status</th>
                    <th className="px-3 py-2">Created</th>
                    <th className="px-3 py-2">Chunks</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-neutral-100">
                  {listing.runs.map((r) => (
                    <RunRow
                      key={r.run_id}
                      run={r}
                      selected={selectedRun === r.run_id}
                      onSelect={() => setSelectedRun(r.run_id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
            {selectedRun ? (
              <ArtifactBrowser
                apiClient={apiClient}
                projectId={projectId}
                sourceId={sourceId}
                runId={selectedRun}
              />
            ) : null}
          </>
        )}
      </PanelBody>
    </Panel>
  );
}

function RunRow({
  run,
  selected,
  onSelect,
}: {
  run: ExtractionRunInfo;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <tr
      className={`cursor-pointer hover:bg-indigo-50/50 ${selected ? "bg-indigo-50/70 ring-1 ring-inset ring-indigo-200" : ""}`}
      onClick={onSelect}
      data-testid="run-row"
    >
      <td className="px-3 py-2 font-mono text-neutral-800">
        {run.run_id}
        {run.is_active ? (
          <span className="ml-2 rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-800">
            active
          </span>
        ) : null}
      </td>
      <td className="px-3 py-2 font-mono font-medium text-indigo-700">
        {run.ayextractor_version ?? "(unknown)"}
      </td>
      <td className="px-3 py-2 text-neutral-600">{run.status ?? "—"}</td>
      <td className="px-3 py-2 text-neutral-500">
        {run.created_at ? new Date(run.created_at).toLocaleString() : "—"}
      </td>
      <td className="px-3 py-2 text-neutral-600">
        {run.chunk_count != null ? run.chunk_count : "—"}
      </td>
    </tr>
  );
}

function ArtifactBrowser({
  apiClient,
  projectId,
  sourceId,
  runId,
}: {
  apiClient: ApiClient | null;
  projectId: string;
  sourceId: string;
  runId: string;
}) {
  const [arts, setArts] = useState<RunArtifactListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<{ path: string; text: string | null } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!apiClient) return;
    let cancelled = false;
    setArts(null);
    setError(null);
    setView(null);
    apiClient
      .listRunArtifacts(projectId, sourceId, runId)
      .then((l) => {
        if (!cancelled) setArts(l);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, projectId, sourceId, runId]);

  async function openArtifact(entry: ArtifactEntry): Promise<void> {
    if (!apiClient) return;
    setBusy(true);
    setView({ path: entry.path, text: null });
    try {
      const { blob, contentType } = await apiClient.getRunArtifact(
        projectId,
        sourceId,
        runId,
        entry.path,
      );
      const text = isTextual(contentType ?? entry.content_type)
        ? await blob.text()
        : `(binary — ${blob.size} bytes; use download)`;
      setView({ path: entry.path, text });
    } catch (err) {
      setView({ path: entry.path, text: `Failed to load: ${String(err)}` });
    } finally {
      setBusy(false);
    }
  }

  async function downloadArtifact(entry: ArtifactEntry): Promise<void> {
    if (!apiClient) return;
    try {
      const { blob, filename } = await apiClient.getRunArtifact(
        projectId,
        sourceId,
        runId,
        entry.path,
      );
      triggerBlobDownload(blob, filename ?? entry.path.split("/").pop() ?? "artifact");
    } catch (err) {
      window.alert(`Download failed: ${String(err)}`);
    }
  }

  async function downloadAll(): Promise<void> {
    if (!apiClient) return;
    try {
      const { blob, filename } = await apiClient.downloadRunArtifactsZip(
        projectId,
        sourceId,
        runId,
      );
      triggerBlobDownload(blob, filename ?? `${sourceId}_${runId}_artifacts.zip`);
    } catch (err) {
      window.alert(`Artifacts zip download failed: ${String(err)}`);
    }
  }

  return (
    <div className="mt-5 border-t border-neutral-200 pt-4" data-testid="artifact-browser">
      <SubHeading
        actions={
          arts && arts.entries.length > 0 ? (
            <button
              type="button"
              onClick={downloadAll}
              className="rounded border border-neutral-300 bg-white px-2 py-1 text-xs font-medium text-neutral-700 hover:bg-neutral-50"
              data-testid="download-artifacts-zip"
            >
              ↓ Download all (zip)
            </button>
          ) : null
        }
      >
        Artifacts · <span className="font-mono normal-case text-neutral-700">{runId}</span>
      </SubHeading>
      {arts && apiClient ? (
        <EnrichmentDigest
          apiClient={apiClient}
          projectId={projectId}
          sourceId={sourceId}
          runId={runId}
          entries={arts.entries}
        />
      ) : null}
      {error ? (
        <p className="mt-3 text-xs text-red-700" role="alert">
          {error}
        </p>
      ) : arts === null ? (
        <p className="mt-3 text-xs text-neutral-500">Loading artifacts…</p>
      ) : arts.entries.length === 0 ? (
        <p className="mt-3 text-xs text-neutral-500">No artifacts in this run.</p>
      ) : (
        <ul className="mt-3 divide-y divide-neutral-100 overflow-hidden rounded-md border border-neutral-200">
          {arts.entries.map((e) => (
            <li
              key={e.path}
              className="flex items-center justify-between gap-2 px-3 py-2 text-xs hover:bg-neutral-50"
            >
              <button
                type="button"
                onClick={() => void openArtifact(e)}
                className="min-w-0 break-all text-left font-mono font-medium text-indigo-700 hover:underline"
                data-testid="artifact-open"
              >
                {e.path}
              </button>
              <div className="flex shrink-0 items-center gap-2">
                <span className="text-neutral-400">{e.size_bytes} B</span>
                <button
                  type="button"
                  onClick={() => void downloadArtifact(e)}
                  className="rounded border border-neutral-300 bg-white px-1.5 py-0.5 text-[11px] text-neutral-700 hover:bg-neutral-100"
                  data-testid="artifact-download"
                >
                  ↓
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {view ? (
        <div className="mt-3" data-testid="artifact-view">
          <p className="text-[11px] font-medium text-neutral-600">
            {view.path}
            {busy ? " · loading…" : ""}
          </p>
          <pre className="mt-1 max-h-96 overflow-auto whitespace-pre-wrap rounded border border-neutral-200 bg-neutral-50 p-2 font-mono text-[11px] text-neutral-800">
            {view.text ?? ""}
          </pre>
        </div>
      ) : null}
    </div>
  );
}

/** First-class render of a run's enrichment outputs: the document summary
 *  (dense → refine fallback) rendered inline, and the per-image vision captions
 *  (the heavy artifacts stay downloadable via the file list above). */
function EnrichmentDigest({
  apiClient,
  projectId,
  sourceId,
  runId,
  entries,
}: {
  apiClient: ApiClient;
  projectId: string;
  sourceId: string;
  runId: string;
  entries: ArtifactEntry[];
}) {
  const [summary, setSummary] = useState<string | null>(null);
  const [images, setImages] = useState<{ path: string; type: string; description: string }[]>([]);

  useEffect(() => {
    let cancelled = false;
    const sumEntry =
      entries.find((e) => e.path.endsWith("dense_summary.md")) ??
      entries.find((e) => e.path.endsWith("refine_summary.md"));
    const imgEntries = entries.filter(
      (e) => e.path.startsWith("01_extraction/images/") && e.path.endsWith(".json"),
    );

    async function load(): Promise<void> {
      if (sumEntry) {
        try {
          const { blob } = await apiClient.getRunArtifact(
            projectId,
            sourceId,
            runId,
            sumEntry.path,
          );
          if (!cancelled) setSummary(await blob.text());
        } catch {
          if (!cancelled) setSummary(null);
        }
      } else if (!cancelled) {
        setSummary(null);
      }
      const imgs: { path: string; type: string; description: string }[] = [];
      for (const e of imgEntries) {
        try {
          const { blob } = await apiClient.getRunArtifact(projectId, sourceId, runId, e.path);
          const parsed = JSON.parse(await blob.text());
          imgs.push({
            path: e.path,
            type: typeof parsed.type === "string" ? parsed.type : "image",
            description: typeof parsed.description === "string" ? parsed.description : "",
          });
        } catch {
          // skip an unreadable image artifact
        }
      }
      if (!cancelled) setImages(imgs);
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [apiClient, projectId, sourceId, runId, entries]);

  if (!summary && images.length === 0) return null;
  return (
    <div className="mt-3 space-y-3" data-testid="enrichment-digest">
      {summary ? (
        <div data-testid="run-summary">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-neutral-500">
            Document summary
          </p>
          <div className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded border border-neutral-200 bg-white p-3 text-xs text-neutral-800">
            {summary}
          </div>
        </div>
      ) : null}
      {images.length > 0 ? (
        <div data-testid="run-images">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-neutral-500">
            Images ({images.length})
          </p>
          <ul className="mt-1 space-y-1">
            {images.map((img) => (
              <li key={img.path} className="rounded border border-neutral-200 bg-white p-2 text-xs">
                <span className="rounded bg-neutral-100 px-1.5 py-0.5 font-medium text-neutral-700">
                  {img.type}
                </span>
                <span className="ml-2 text-neutral-600">
                  {img.description || "(no description)"}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function StoragePath({
  label,
  bucket,
  objectKey,
}: {
  label: string;
  bucket: string;
  objectKey: string | null;
}) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2">
      <dt className="w-32 shrink-0 font-medium text-neutral-500">{label}</dt>
      <dd className="min-w-0 break-all font-mono text-neutral-700">
        {objectKey ? `${bucket}/${objectKey}` : <span className="text-neutral-400">(pending)</span>}
      </dd>
    </div>
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
      <dt className="text-xs font-medium uppercase tracking-wide text-neutral-500">{label}</dt>
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
