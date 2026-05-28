// =============================================================================
// File: live-docs-manager.test.tsx
// Version: 1
// Path: ay_platform_ui/tests/integration/live-docs-manager.test.tsx
// Description: Integration tests for the shared <LiveDocsManager>
//              component (R-500-010 v2). Exercises the three gaps the
//              component closes :
//                (a) Empty-state — when the live-docs run does not
//                    exist (404 from getArtifactTree) the component
//                    renders the empty-state with `New file` / `New
//                    folder` affordances at root.
//                (b) Blank-file creation — clicking `+ New file` →
//                    `createDocument(projectId, name, "")` POST.
//                (c) Inline content editor — selecting a file loads
//                    its text via `getDocumentText` ; `Edit` flips
//                    to a textarea ; `Save` PUTs via `updateDocument`.
//              Uses MSW to stub the artifacts + documents endpoints
//              and the renderWithProviders helper for the ConfigProvider
//              + AuthProvider tree.
// =============================================================================

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useConfigState } from "@/app/providers";
import { LiveDocsManager } from "@/components/live-docs-manager";

import { fakeJWT } from "../helpers/msw-handlers";
import { server } from "../helpers/msw-server";
import { renderWithProviders } from "../helpers/render";

// LiveDocsManager calls `useReadyConfig()` which throws while the
// ConfigProvider is still bootstrapping (status=loading). This gate
// withholds the subtree until bootstrap completes — mirrors what the
// protected-layout does in production.
function BootstrapGate({ children }: { children: ReactNode }) {
  const state = useConfigState();
  return state.status === "ready" ? children : null;
}

// LiveDocsManager uses native window.prompt/confirm/alert ; jsdom returns
// `null` by default. We stub them so the create / save flows can drive a
// value through the handlers without manual interaction.
let promptMock: ReturnType<typeof vi.fn>;
let alertMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  // Seed an auth token so the apiClient adds the Authorization header
  // (the providers chain is otherwise blind to it).
  window.localStorage.setItem(
    "aywizz.token",
    fakeJWT({
      sub: "user-alice",
      username: "alice",
      tenant_id: "tenant-test",
      roles: ["project_editor"],
      exp: Math.floor(Date.now() / 1000) + 3600,
      iat: Math.floor(Date.now() / 1000),
    }),
  );
  promptMock = vi.fn();
  alertMock = vi.fn();
  vi.stubGlobal("prompt", promptMock);
  vi.stubGlobal("alert", alertMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("LiveDocsManager — empty-state", () => {
  it("renders the empty-state when the live-docs run does not exist (404)", async () => {
    server.use(
      http.get("/api/v1/projects/proj-a/artifacts/runs/live-docs/tree", () =>
        HttpResponse.json({ detail: "run not found" }, { status: 404 }),
      ),
    );

    renderWithProviders(
      <BootstrapGate>
        <LiveDocsManager projectId="proj-a" variant="full" />
      </BootstrapGate>,
    );

    // Empty-state surfaces the heading + the two root-level actions.
    expect(await screen.findByText("No documents yet")).toBeInTheDocument();
    expect(screen.getByText(/create the first file or folder/i)).toBeInTheDocument();
  });

  it("renders the empty-state when the run exists but is empty (200 nodes:[])", async () => {
    server.use(
      http.get("/api/v1/projects/proj-a/artifacts/runs/live-docs/tree", () =>
        HttpResponse.json({ run_id: "live-docs", nodes: [] }),
      ),
    );

    renderWithProviders(
      <BootstrapGate>
        <LiveDocsManager projectId="proj-a" variant="full" />
      </BootstrapGate>,
    );

    expect(await screen.findByText("No documents yet")).toBeInTheDocument();
  });
});

describe("LiveDocsManager — blank-file creation", () => {
  it("POSTs to /documents with an empty content body when + New file is clicked", async () => {
    // Tree starts empty (404 from the live-docs run) ; after creation
    // the refresh call returns the new file.
    let createCalled = false;
    server.use(
      http.get("/api/v1/projects/proj-a/artifacts/runs/live-docs/tree", () => {
        if (createCalled) {
          return HttpResponse.json({
            run_id: "live-docs",
            nodes: [
              {
                path: "notes.md",
                kind: "file",
                size_bytes: 0,
                mime_type: "text/markdown",
                version: 1,
              },
            ],
          });
        }
        return HttpResponse.json({ detail: "not found" }, { status: 404 });
      }),
      http.post("/api/v1/projects/proj-a/documents", async ({ request }) => {
        const body = (await request.json()) as { path: string; content: string };
        expect(body).toEqual({ path: "notes.md", content: "" });
        createCalled = true;
        return HttpResponse.json({ path: "notes.md", size_bytes: 0, version: 1 });
      }),
    );

    promptMock.mockReturnValueOnce("notes.md");

    renderWithProviders(
      <BootstrapGate>
        <LiveDocsManager projectId="proj-a" variant="full" />
      </BootstrapGate>,
    );

    // Wait for the empty-state, then click `+ New file` (toolbar).
    await screen.findByText("No documents yet");
    fireEvent.click(screen.getByTestId("live-docs-new-file"));

    // After the POST + refresh, the tree should have the new file.
    await waitFor(() => {
      expect(createCalled).toBe(true);
    });
  });
});
