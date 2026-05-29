// =============================================================================
// File: working-area.test.tsx
// Path: ay_platform_ui/tests/integration/working-area.test.tsx
// Description: First batch for the DocGen Working area (3-pane). The page is
//              large and wires several heavy children (ChatSidebar, FileTree,
//              FileTreeContextMenu) — those are stubbed so we isolate the
//              page's own load chain + handlers. Hooks (useReadyConfig /
//              useAuth / useProjectUi / useSearchParams) are mocked ; the
//              artifacts + live-docs endpoints are scripted via MSW. Covers :
//                - load chain (runs → live-docs auto-select → tree → blob →
//                  CodeViewer) + the live-docs toolbar ;
//                - the live-docs empty-state ;
//                - the runs load error ;
//                - the inline editor : Edit → textarea → Save (updateDocument).
//              Context-menu ops / history / drag-drop / references are left
//              to follow-up batches.
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import WorkingAreaPage from "@/app/(protected)/projects/[pid]/working-area/page";
import { server } from "../helpers/msw-server";

const READY_CONFIG = {
  runtime: { apiBaseUrl: "", publicBaseUrl: "" },
  ux: {
    api_version: "v1",
    auth_mode: "local",
    brand: { name: "AyWizz", short_name: "AY", accent_color_hex: "#000" },
    features: {
      chat_enabled: true,
      kg_enabled: true,
      cross_tenant_enabled: false,
      file_download_enabled: true,
    },
  },
};

vi.mock("@/app/providers", () => ({ useReadyConfig: () => READY_CONFIG }));
vi.mock("@/app/auth-provider", () => ({
  useAuth: () => ({ state: { status: "authenticated", claims: { sub: "u1" } } }),
}));
vi.mock("@/app/(protected)/workspace-store", () => ({
  useProjectUi: () => ({ ui: {}, setUi: vi.fn() }),
}));
vi.mock("next/navigation", () => ({
  useParams: () => ({ pid: "p1" }),
  useSearchParams: () => new URLSearchParams(),
}));

// Heavy children stubbed — their internals are covered by their own tests ;
// here we only need the page's wiring around them.
vi.mock("@/components/chat-sidebar", () => ({
  ChatSidebar: () => <div data-testid="mock-chat-sidebar" />,
}));
vi.mock("@/components/file-tree", () => ({
  FileTree: () => <div data-testid="mock-file-tree" />,
}));
vi.mock("@/components/file-tree-context-menu", () => ({
  FileTreeContextMenu: () => null,
}));

const RUNS_URL = "/api/v1/projects/p1/artifacts/runs";

function liveRun() {
  return {
    run_id: "live-docs",
    project_id: "p1",
    tenant_id: "t1",
    started_at: "2026-01-01T00:00:00Z",
    completed_at: null,
    status: "completed",
    file_count: 1,
    total_bytes: 10,
    label: "Live docs",
  };
}

/** Happy live-docs chain: one run, a tree with one markdown file, its blob. */
function liveDocsHandlers(opts: { nodes?: unknown[]; blob?: string } = {}) {
  const nodes = opts.nodes ?? [
    { path: "intro.md", kind: "file", size_bytes: 6, mime_type: "text/markdown" },
  ];
  return [
    http.get(RUNS_URL, () => HttpResponse.json({ runs: [liveRun()] })),
    http.get(`${RUNS_URL}/:rid/tree`, () => HttpResponse.json({ run_id: "live-docs", nodes })),
    http.get(`${RUNS_URL}/:rid/blob`, () =>
      HttpResponse.text(opts.blob ?? "# Hello\n", { headers: { "Content-Type": "text/markdown" } }),
    ),
  ];
}

describe("WorkingAreaPage", () => {
  it("loads the live-docs run, shows the toolbar and renders the file in the viewer", async () => {
    server.use(...liveDocsHandlers());
    render(<WorkingAreaPage />);

    await waitFor(() => expect(screen.getByTestId("working-area")).toBeInTheDocument());
    // live-docs auto-selected → toolbar present
    expect(await screen.findByTestId("working-live-docs-toolbar")).toBeInTheDocument();
    expect(screen.getByTestId("working-new-file")).toBeInTheDocument();
    // file auto-selected → viewer shows the path + the CodeViewer content
    await waitFor(() =>
      expect(screen.getByTestId("working-viewer-pane")).toHaveTextContent("intro.md"),
    );
    expect(await screen.findByTestId("code-viewer")).toBeInTheDocument();
    expect(screen.getByText("# Hello")).toBeInTheDocument();
  });

  it("shows the live-docs empty-state when the tree has no nodes", async () => {
    server.use(...liveDocsHandlers({ nodes: [] }));
    render(<WorkingAreaPage />);
    await waitFor(() => expect(screen.getByTestId("working-live-docs-empty")).toBeInTheDocument());
    expect(screen.getByText(/No documents yet/)).toBeInTheDocument();
  });

  it("surfaces the runs load error", async () => {
    server.use(http.get(RUNS_URL, () => HttpResponse.json({ detail: "x" }, { status: 500 })));
    render(<WorkingAreaPage />);
    await waitFor(() => expect(screen.getByText(/Failed to load runs/)).toBeInTheDocument());
  });

  it("edits a live-docs file inline and saves via updateDocument", async () => {
    const put = vi.fn(() => HttpResponse.json({ path: "intro.md", size_bytes: 12, version: 2 }));
    server.use(...liveDocsHandlers(), http.put("/api/v1/projects/p1/documents/intro.md", put));
    render(<WorkingAreaPage />);

    const user = userEvent.setup();
    await user.click(await screen.findByTestId("working-edit"));
    const editor = await screen.findByTestId("working-editor");
    await user.clear(editor);
    await user.type(editor, "edited body");
    await user.click(screen.getByTestId("working-save"));

    await waitFor(() => expect(put).toHaveBeenCalled());
  });
});

describe("WorkingAreaPage root file/folder handlers", () => {
  afterEach(() => vi.restoreAllMocks());

  it("creates a new file at root via the toolbar", async () => {
    const post = vi.fn(() => HttpResponse.json({ path: "notes.md", size_bytes: 0, version: 1 }));
    server.use(...liveDocsHandlers(), http.post("/api/v1/projects/p1/documents", post));
    vi.spyOn(window, "prompt").mockReturnValue("notes.md");
    render(<WorkingAreaPage />);

    const user = userEvent.setup();
    await user.click(await screen.findByTestId("working-new-file"));
    await waitFor(() => expect(post).toHaveBeenCalled());
  });

  it("rejects a slash in a new file name (client validation, no request)", async () => {
    const post = vi.fn(() => HttpResponse.json({ path: "x", size_bytes: 0, version: 1 }));
    server.use(...liveDocsHandlers(), http.post("/api/v1/projects/p1/documents", post));
    vi.spyOn(window, "prompt").mockReturnValue("a/b.md");
    render(<WorkingAreaPage />);

    const user = userEvent.setup();
    await user.click(await screen.findByTestId("working-new-file"));
    expect(await screen.findByText(/SHALL NOT contain slashes/)).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("creates a new folder at root via the toolbar", async () => {
    const mkdir = vi.fn(() => HttpResponse.json({ ok: true }));
    server.use(...liveDocsHandlers(), http.post("/api/v1/projects/p1/documents/mkdir", mkdir));
    vi.spyOn(window, "prompt").mockReturnValue("docs");
    render(<WorkingAreaPage />);

    const user = userEvent.setup();
    await user.click(await screen.findByTestId("working-new-folder"));
    await waitFor(() => expect(mkdir).toHaveBeenCalled());
  });

  it("surfaces a create error in the structural-op toast", async () => {
    server.use(
      ...liveDocsHandlers(),
      http.post("/api/v1/projects/p1/documents", () =>
        HttpResponse.json({ detail: "exists" }, { status: 409 }),
      ),
    );
    vi.spyOn(window, "prompt").mockReturnValue("dup.md");
    render(<WorkingAreaPage />);

    const user = userEvent.setup();
    await user.click(await screen.findByTestId("working-new-file"));
    expect(await screen.findByText(/createDocument failed \(409/)).toBeInTheDocument();
  });
});
