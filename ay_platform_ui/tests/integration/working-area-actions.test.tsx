// =============================================================================
// File: working-area-actions.test.tsx
// Path: ay_platform_ui/tests/integration/working-area-actions.test.tsx
// Description: Working area batch 3 — the context-menu structural ops
//              (handleStructuralAction) + drag-move + history panel, which
//              batch 1 couldn't reach. The FileTree mock here EXPOSES its
//              onContextMenu / onMove callbacks as buttons, and the REAL
//              FileTreeContextMenu is rendered, so picking a menu action
//              dispatches into the page's handlers. live-docs run.
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

// FileTree mock exposes the structural callbacks as buttons so the test can
// drive a context-menu open + a drag-move. The REAL FileTreeContextMenu is
// kept (not mocked) so a picked action dispatches into handleStructuralAction.
vi.mock("@/components/file-tree", () => ({
  // biome-ignore lint/suspicious/noExplicitAny: test stub
  FileTree: ({ onContextMenu, onMove }: any) => (
    <div data-testid="mock-file-tree">
      <button
        type="button"
        data-testid="ctx-file"
        onClick={() => onContextMenu?.({ path: "intro.md", kind: "file", clientX: 5, clientY: 5 })}
      >
        ctx file
      </button>
      <button
        type="button"
        data-testid="ctx-folder"
        onClick={() => onContextMenu?.({ path: "docs", kind: "folder", clientX: 5, clientY: 5 })}
      >
        ctx folder
      </button>
      <button type="button" data-testid="do-move" onClick={() => onMove?.("intro.md", "docs")}>
        move
      </button>
    </div>
  ),
}));
vi.mock("@/components/chat-sidebar", () => ({
  ChatSidebar: () => <div data-testid="mock-chat" />,
}));

const RUNS_URL = "/api/v1/projects/p1/artifacts/runs";
const DOCS_URL = "/api/v1/projects/p1/documents";

function liveDocsHandlers() {
  return [
    http.get(RUNS_URL, () =>
      HttpResponse.json({
        runs: [
          {
            run_id: "live-docs",
            project_id: "p1",
            tenant_id: "t1",
            started_at: "2026-01-01T00:00:00Z",
            completed_at: null,
            status: "completed",
            file_count: 1,
            total_bytes: 10,
            label: "Live docs",
          },
        ],
      }),
    ),
    http.get(`${RUNS_URL}/:rid/tree`, () =>
      HttpResponse.json({
        run_id: "live-docs",
        nodes: [{ path: "intro.md", kind: "file", size_bytes: 6, mime_type: "text/markdown" }],
      }),
    ),
    http.get(`${RUNS_URL}/:rid/blob`, () =>
      HttpResponse.text("# Hello\n", { headers: { "Content-Type": "text/markdown" } }),
    ),
  ];
}

afterEach(() => vi.restoreAllMocks());

async function openFileMenu() {
  const user = userEvent.setup();
  await user.click(await screen.findByTestId("ctx-file"));
  await screen.findByRole("menu");
  return user;
}

describe("WorkingAreaPage context-menu actions (live-docs)", () => {
  it("renames a file via the context menu", async () => {
    const rename = vi.fn(() => HttpResponse.json({ ok: true }));
    server.use(...liveDocsHandlers(), http.post(`${DOCS_URL}/rename`, rename));
    vi.spyOn(window, "prompt").mockReturnValue("renamed.md");
    render(<WorkingAreaPage />);

    const user = await openFileMenu();
    await user.click(screen.getByText("Rename…"));
    await waitFor(() => expect(rename).toHaveBeenCalled());
  });

  it("deletes a file via the context menu after confirmation", async () => {
    const del = vi.fn(() => new HttpResponse(null, { status: 204 }));
    server.use(...liveDocsHandlers(), http.delete(`${DOCS_URL}/intro.md`, del));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<WorkingAreaPage />);

    const user = await openFileMenu();
    await user.click(screen.getByText("Delete"));
    await waitFor(() => expect(del).toHaveBeenCalled());
  });

  it("opens the version-history panel from the context menu", async () => {
    server.use(
      ...liveDocsHandlers(),
      http.get("/api/v1/projects/p1/git/commits", () =>
        HttpResponse.json({
          page: 1,
          commits: [
            {
              sha: "abcdef1234",
              message: "edit intro",
              author_name: "alice",
              author_email: "a@x",
              committed_at: "2026-01-01T00:00:00Z",
            },
          ],
        }),
      ),
    );
    render(<WorkingAreaPage />);

    const user = await openFileMenu();
    await user.click(screen.getByText("View history…"));
    expect(await screen.findByTestId("working-history-panel")).toBeInTheDocument();
    expect(await screen.findByTestId("working-history-commit-abcdef1234")).toBeInTheDocument();
  });

  it("creates a sub-folder from a folder's context menu", async () => {
    const mkdir = vi.fn(() => HttpResponse.json({ ok: true }));
    server.use(...liveDocsHandlers(), http.post(`${DOCS_URL}/mkdir`, mkdir));
    vi.spyOn(window, "prompt").mockReturnValue("sub");
    render(<WorkingAreaPage />);

    const user = userEvent.setup();
    await user.click(await screen.findByTestId("ctx-folder"));
    await screen.findByRole("menu");
    await user.click(screen.getByText("New folder…"));
    await waitFor(() => expect(mkdir).toHaveBeenCalled());
  });

  it("moves a file via drag-and-drop (onMove → moveDocument)", async () => {
    const move = vi.fn(() => HttpResponse.json({ ok: true }));
    server.use(...liveDocsHandlers(), http.post(`${DOCS_URL}/move`, move));
    render(<WorkingAreaPage />);

    const user = userEvent.setup();
    await user.click(await screen.findByTestId("do-move"));
    await waitFor(() => expect(move).toHaveBeenCalled());
  });

  it("attaches a file as a reference (add-as-ref closes the menu, no request)", async () => {
    server.use(...liveDocsHandlers());
    render(<WorkingAreaPage />);

    const user = await openFileMenu();
    await user.click(screen.getByText("Add as reference"));
    // menu dismissed after the pick
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
  });
});
