// =============================================================================
// File: source-detail.test.tsx
// Path: ay_platform_ui/tests/integration/source-detail.test.tsx
// Description: Tests for the per-source detail page. renderWithProviders +
//              mocked navigation, C7 source endpoints via MSW. Covers :
//              ready metadata (fields + mime label + parse-error block),
//              not-found (404), error, Download (object-URL path), and
//              Delete (confirm → DELETE → navigate back to the list).
// =============================================================================

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import SourceDetailPage from "@/app/(protected)/projects/[pid]/sources/[sid]/page";
import { server } from "../helpers/msw-server";
import { renderWithProviders } from "../helpers/render";

const { mockRouter } = vi.hoisted(() => ({
  mockRouter: {
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  },
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ pid: "p1", sid: "doc-a" }),
  useRouter: () => mockRouter,
  usePathname: () => "/projects/p1/sources/doc-a",
}));
vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: {
    href: string;
    children: React.ReactNode;
  } & Record<string, unknown>) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

const SRC_URL = "/api/v1/memory/projects/p1/sources/doc-a";

function makeSource(over: Partial<Record<string, unknown>> = {}) {
  return {
    source_id: "doc-a",
    project_id: "p1",
    mime_type: "text/markdown",
    size_bytes: 2048,
    chunk_count: 12,
    uploaded_by: "alice",
    uploaded_at: "2026-01-01T00:00:00Z",
    parse_status: "indexed",
    parse_error: null,
    model_id: "all-minilm",
    ...over,
  };
}

afterEach(() => vi.restoreAllMocks());

describe("SourceDetailPage", () => {
  it("renders the metadata fields + mime label", async () => {
    server.use(http.get(SRC_URL, () => HttpResponse.json(makeSource())));
    renderWithProviders(<SourceDetailPage />);

    await waitFor(() => expect(screen.getByTestId("source-detail")).toBeInTheDocument());
    expect(screen.getByText("Markdown")).toBeInTheDocument(); // mime label
    expect(screen.getByText("indexed")).toBeInTheDocument();
    expect(screen.getByText("all-minilm")).toBeInTheDocument();
    expect(screen.getByText("2048 bytes")).toBeInTheDocument();
  });

  it("renders the parse-error block when present", async () => {
    server.use(
      http.get(SRC_URL, () =>
        HttpResponse.json(makeSource({ parse_status: "failed", parse_error: "bad PDF header" })),
      ),
    );
    renderWithProviders(<SourceDetailPage />);
    await waitFor(() => expect(screen.getByTestId("source-parse-error")).toBeInTheDocument());
    expect(screen.getByText("bad PDF header")).toBeInTheDocument();
  });

  it("renders not-found on a 404", async () => {
    server.use(http.get(SRC_URL, () => HttpResponse.json({ detail: "gone" }, { status: 404 })));
    renderWithProviders(<SourceDetailPage />);
    await waitFor(() => expect(screen.getByText(/Source not found/)).toBeInTheDocument());
  });

  it("renders the error state on a non-404 failure", async () => {
    server.use(http.get(SRC_URL, () => HttpResponse.json({ detail: "x" }, { status: 500 })));
    renderWithProviders(<SourceDetailPage />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText(/Failed to load source:/)).toBeInTheDocument();
  });

  it("downloads the blob via an object URL", async () => {
    server.use(
      http.get(SRC_URL, () => HttpResponse.json(makeSource())),
      http.get(`${SRC_URL}/blob`, () =>
        HttpResponse.text("bytes", {
          headers: { "Content-Disposition": 'attachment; filename="doc-a.md"' },
        }),
      ),
    );
    const createObjectURL = vi.fn(() => "blob:mock");
    (URL as unknown as { createObjectURL: unknown }).createObjectURL = createObjectURL;
    (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = vi.fn();
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});

    renderWithProviders(<SourceDetailPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("source-download"));

    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    expect(alertSpy).not.toHaveBeenCalled();
  });

  it("deletes after confirmation and navigates back to the list", async () => {
    const del = vi.fn(() => new HttpResponse(null, { status: 204 }));
    server.use(
      http.get(SRC_URL, () => HttpResponse.json(makeSource())),
      http.delete(SRC_URL, del),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);

    renderWithProviders(<SourceDetailPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("source-delete"));

    await waitFor(() => expect(del).toHaveBeenCalled());
    await waitFor(() => expect(mockRouter.push).toHaveBeenCalledWith("/projects/p1/sources"));
  });
});

// ---------------------------------------------------------------------------
// Transparency surface (R-400-221): runs table, artifact browser, chunk
// expand + downloads.
// ---------------------------------------------------------------------------

function diagWithOneChunk() {
  return {
    source_id: "doc-a",
    project_id: "p1",
    parse_status: "indexed",
    parse_error: null,
    chunk_count: 1,
    model_id: "all-minilm",
    processing_version: "chunk=512/64;embed=all-minilm",
    uploaded_by: "alice",
    uploaded_at: "2026-01-01T00:00:00Z",
    mime_type: "text/markdown",
    size_bytes: 2048,
    extraction_run_id: "run-new",
    storage: {
      raw_bucket: "c13-extractor-artifacts",
      raw_object_key: "sources/t/p1/doc-a/raw.md",
      artifacts_bucket: "c13-extractor-artifacts",
      artifacts_prefix: "t/p1/doc-a/runs/run-new/",
      chunks_jsonl_key: "t/p1/doc-a/runs/run-new/02_chunks/chunks.jsonl",
      manifest_key: "t/p1/doc-a/runs/run-new/00_metadata/run_manifest.json",
    },
    chunks: [
      {
        chunk_id: "c0",
        seq: 0,
        token_count: 8,
        char_start: 0,
        char_end: 42,
        has_embedding: true,
      },
    ],
  };
}

function runsListing() {
  return {
    source_id: "doc-a",
    project_id: "p1",
    active_run_id: "run-new",
    runs: [
      {
        run_id: "run-old",
        ayextractor_version: "0.9.0",
        git_sha: "old123",
        created_at: "2026-01-01T09:00:00Z",
        completed_at: "2026-01-01T09:00:05Z",
        status: "completed",
        is_active: false,
        chunk_count: null,
      },
      {
        run_id: "run-new",
        ayextractor_version: "1.2.0",
        git_sha: "new456",
        created_at: "2026-01-02T10:00:00Z",
        completed_at: "2026-01-02T10:00:05Z",
        status: "completed",
        is_active: true,
        chunk_count: 1,
      },
    ],
  };
}

function artifactsListing() {
  return {
    source_id: "doc-a",
    project_id: "p1",
    run_id: "run-new",
    prefix: "t/p1/doc-a/runs/run-new/",
    entries: [
      {
        path: "00_metadata/run_manifest.json",
        size_bytes: 120,
        content_type: "application/json",
      },
      { path: "02_chunks/chunks.jsonl", size_bytes: 80, content_type: "application/x-ndjson" },
    ],
  };
}

describe("SourceDetailPage — runs, artifacts & chunk content", () => {
  function wireBaseStack() {
    server.use(
      http.get(SRC_URL, () => HttpResponse.json(makeSource())),
      http.get(`${SRC_URL}/diagnostics`, () => HttpResponse.json(diagWithOneChunk())),
      http.get(`${SRC_URL}/runs`, () => HttpResponse.json(runsListing())),
      http.get(`${SRC_URL}/runs/run-new/artifacts`, () => HttpResponse.json(artifactsListing())),
    );
  }

  it("lists extraction runs with parser version and an active badge", async () => {
    wireBaseStack();
    renderWithProviders(<SourceDetailPage />);

    await waitFor(() => expect(screen.getByTestId("runs-table")).toBeInTheDocument());
    expect(screen.getByText("1.2.0")).toBeInTheDocument(); // active run's extractor version
    expect(screen.getByText("0.9.0")).toBeInTheDocument(); // the older run too
    expect(screen.getByText("active")).toBeInTheDocument();
  });

  it("browses a run and views an artifact's content", async () => {
    wireBaseStack();
    server.use(
      http.get(`${SRC_URL}/runs/run-new/artifacts/00_metadata/run_manifest.json`, () =>
        HttpResponse.text('{"ayextractor_version":"1.2.0"}', {
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    renderWithProviders(<SourceDetailPage />);
    const user = userEvent.setup();

    // The active run is auto-selected → its artifacts load.
    const opener = await screen.findByText("00_metadata/run_manifest.json");
    await user.click(opener);

    await waitFor(() => expect(screen.getByTestId("artifact-view")).toBeInTheDocument());
    expect(screen.getByText(/ayextractor_version/)).toBeInTheDocument();
  });

  it("expands a chunk to load and show its content + retrieval structure + metadata", async () => {
    wireBaseStack();
    server.use(
      http.get(`${SRC_URL}/chunks/c0`, () =>
        HttpResponse.json({
          chunk_id: "c0",
          seq: 0,
          content: "Voyager 1 launched in 1977.",
          context: "About spacecraft.",
          original_text: "Voyager 1 launched in 1977.",
          char_start: 0,
          char_end: 27,
          token_count: 8,
          section_path: ["Intro"],
          // Retrieval text (embedded + BM25-indexed) differs from content.
          search_text: "Intro\n\nVoyager 1 launched in 1977.",
          content_hash: "sha256:abc123",
          embedding_model: "all-minilm",
          embedding_dim: 384,
          extraction_run_id: "20260603_1000_xyz",
          references: ["ref:nasa-1977"],
          images: ["img_aabbccdd"],
          tables: [],
          // Document summary referenced ONCE from the source (not per chunk).
          document_summary: "A document about the Voyager program.",
        }),
      ),
    );
    renderWithProviders(<SourceDetailPage />);
    const user = userEvent.setup();

    const row = await screen.findByTestId("chunk-row");
    await user.click(row);

    await waitFor(() => expect(screen.getByTestId("chunk-content")).toBeInTheDocument());
    expect(screen.getByText("Voyager 1 launched in 1977.")).toBeInTheDocument();
    expect(screen.getByText(/Section: Intro/)).toBeInTheDocument();
    // Retrieval-structure + document-summary + metadata blocks render.
    expect(screen.getByTestId("chunk-search-text")).toBeInTheDocument();
    expect(screen.getByTestId("chunk-doc-summary")).toBeInTheDocument();
    expect(screen.getByText("A document about the Voyager program.")).toBeInTheDocument();
    const meta = screen.getByTestId("chunk-metadata");
    expect(meta).toHaveTextContent("all-minilm (384d)");
    expect(meta).toHaveTextContent("sha256:abc123");
    expect(meta).toHaveTextContent("ref:nasa-1977");
  });

  it("shows the decontextualization before/after when the chunk was disambiguated", async () => {
    wireBaseStack();
    server.use(
      http.get(`${SRC_URL}/chunks/c0`, () =>
        HttpResponse.json({
          chunk_id: "c0",
          seq: 0,
          content: "Voyager 1 launched in 1977.", // decontextualized
          context: null,
          original_text: "It launched in 1977.", // original (differs)
          char_start: 0,
          char_end: 27,
          token_count: 8,
          section_path: [],
        }),
      ),
    );
    renderWithProviders(<SourceDetailPage />);
    const user = userEvent.setup();

    await user.click(await screen.findByTestId("chunk-row"));
    await waitFor(() => expect(screen.getByTestId("chunk-content")).toBeInTheDocument());
    expect(screen.getByText("decontextualized")).toBeInTheDocument();
    expect(screen.getByTestId("chunk-original")).toBeInTheDocument();
    expect(screen.getByText("It launched in 1977.")).toBeInTheDocument();
  });

  it("renders the document summary + image captions (enrichment digest)", async () => {
    wireBaseStack();
    server.use(
      http.get(`${SRC_URL}/runs/run-new/artifacts`, () =>
        HttpResponse.json({
          source_id: "doc-a",
          project_id: "p1",
          run_id: "run-new",
          prefix: "t/p1/doc-a/runs/run-new/",
          entries: [
            { path: "02_chunks/dense_summary.md", size_bytes: 50, content_type: "text/markdown" },
            {
              path: "01_extraction/images/img_abc12345.json",
              size_bytes: 60,
              content_type: "application/json",
            },
          ],
        }),
      ),
      http.get(`${SRC_URL}/runs/run-new/artifacts/02_chunks/dense_summary.md`, () =>
        HttpResponse.text("Voyager 1 is the most distant human-made object.", {
          headers: { "Content-Type": "text/markdown" },
        }),
      ),
      http.get(`${SRC_URL}/runs/run-new/artifacts/01_extraction/images/img_abc12345.json`, () =>
        HttpResponse.json({
          sha256: "abc12345deadbeef",
          type: "diagram",
          description: "A trajectory diagram of the probe.",
        }),
      ),
    );
    renderWithProviders(<SourceDetailPage />);

    await waitFor(() => expect(screen.getByTestId("run-summary")).toBeInTheDocument());
    expect(screen.getByText(/most distant human-made object/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("run-images")).toBeInTheDocument());
    expect(screen.getByText(/trajectory diagram of the probe/)).toBeInTheDocument();
  });

  it("downloads all chunks as a zip via an object URL", async () => {
    wireBaseStack();
    server.use(
      http.get(`${SRC_URL}/chunks.zip`, () =>
        HttpResponse.arrayBuffer(new Uint8Array([80, 75, 3, 4]).buffer, {
          headers: {
            "Content-Type": "application/zip",
            "Content-Disposition": 'attachment; filename="doc-a_chunks.zip"',
          },
        }),
      ),
    );
    const createObjectURL = vi.fn(() => "blob:mock");
    (URL as unknown as { createObjectURL: unknown }).createObjectURL = createObjectURL;
    (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = vi.fn();

    renderWithProviders(<SourceDetailPage />);
    const user = userEvent.setup();

    await user.click(await screen.findByTestId("download-chunks-zip"));
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
  });

  it("downloads a single artifact and the whole-run zip via object URLs", async () => {
    wireBaseStack();
    server.use(
      http.get(`${SRC_URL}/runs/run-new/artifacts/00_metadata/run_manifest.json`, () =>
        HttpResponse.text("{}", {
          headers: {
            "Content-Type": "application/json",
            "Content-Disposition": 'inline; filename="run_manifest.json"',
          },
        }),
      ),
      http.get(`${SRC_URL}/runs/run-new/artifacts.zip`, () =>
        HttpResponse.arrayBuffer(new Uint8Array([80, 75, 3, 4]).buffer, {
          headers: {
            "Content-Type": "application/zip",
            "Content-Disposition": 'attachment; filename="doc-a_run-new_artifacts.zip"',
          },
        }),
      ),
    );
    const createObjectURL = vi.fn(() => "blob:mock");
    (URL as unknown as { createObjectURL: unknown }).createObjectURL = createObjectURL;
    (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = vi.fn();

    renderWithProviders(<SourceDetailPage />);
    const user = userEvent.setup();

    const dlButtons = await screen.findAllByTestId("artifact-download");
    await user.click(dlButtons[0]);
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());

    await user.click(screen.getByTestId("download-artifacts-zip"));
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(2));
  });

  it("shows a binary placeholder for non-textual artifacts", async () => {
    wireBaseStack();
    server.use(
      http.get(`${SRC_URL}/runs/run-new/artifacts/00_metadata/run_manifest.json`, () =>
        HttpResponse.arrayBuffer(new Uint8Array([1, 2, 3]).buffer, {
          headers: { "Content-Type": "application/octet-stream" },
        }),
      ),
    );
    renderWithProviders(<SourceDetailPage />);
    const user = userEvent.setup();

    await user.click(await screen.findByText("00_metadata/run_manifest.json"));
    await waitFor(() => expect(screen.getByTestId("artifact-view")).toBeInTheDocument());
    expect(screen.getByText(/binary/)).toBeInTheDocument();
  });

  it("collapses an expanded chunk on a second click", async () => {
    wireBaseStack();
    server.use(
      http.get(`${SRC_URL}/chunks/c0`, () =>
        HttpResponse.json({
          chunk_id: "c0",
          seq: 0,
          content: "chunk body",
          context: null,
          original_text: null,
          char_start: 0,
          char_end: 10,
          token_count: 2,
          section_path: [],
        }),
      ),
    );
    renderWithProviders(<SourceDetailPage />);
    const user = userEvent.setup();

    const row = await screen.findByTestId("chunk-row");
    await user.click(row);
    await waitFor(() => expect(screen.getByTestId("chunk-content")).toBeInTheDocument());
    await user.click(row);
    await waitFor(() => expect(screen.queryByTestId("chunk-content")).not.toBeInTheDocument());
  });

  it("handles a runs-load failure gracefully", async () => {
    server.use(
      http.get(SRC_URL, () => HttpResponse.json(makeSource())),
      http.get(`${SRC_URL}/diagnostics`, () => HttpResponse.json(diagWithOneChunk())),
      http.get(`${SRC_URL}/runs`, () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    renderWithProviders(<SourceDetailPage />);
    await waitFor(() => expect(screen.getByText(/Failed to load runs:/)).toBeInTheDocument());
  });

  it("shows an empty state when the source has no runs", async () => {
    server.use(
      http.get(SRC_URL, () => HttpResponse.json(makeSource())),
      http.get(`${SRC_URL}/diagnostics`, () => HttpResponse.json(diagWithOneChunk())),
      http.get(`${SRC_URL}/runs`, () =>
        HttpResponse.json({
          source_id: "doc-a",
          project_id: "p1",
          active_run_id: null,
          runs: [],
        }),
      ),
    );
    renderWithProviders(<SourceDetailPage />);
    await waitFor(() => expect(screen.getByText(/No extraction runs found/)).toBeInTheDocument());
  });

  it("shows an empty state when the active run has no artifacts", async () => {
    server.use(
      http.get(SRC_URL, () => HttpResponse.json(makeSource())),
      http.get(`${SRC_URL}/diagnostics`, () => HttpResponse.json(diagWithOneChunk())),
      http.get(`${SRC_URL}/runs`, () => HttpResponse.json(runsListing())),
      http.get(`${SRC_URL}/runs/run-new/artifacts`, () =>
        HttpResponse.json({
          source_id: "doc-a",
          project_id: "p1",
          run_id: "run-new",
          prefix: "t/p1/doc-a/runs/run-new/",
          entries: [],
        }),
      ),
    );
    renderWithProviders(<SourceDetailPage />);
    await waitFor(() => expect(screen.getByText(/No artifacts in this run/)).toBeInTheDocument());
  });
});
