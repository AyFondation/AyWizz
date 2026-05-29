// =============================================================================
// File: artifacts.test.tsx
// Version: 1
// Path: ay_platform_ui/tests/integration/artifacts.test.tsx
// Description: Integration tests for the project Artifacts browser. Mocks
//              @/app/providers (useReadyConfig throws until ready) +
//              next/navigation (useParams), and scripts the C4 artifact
//              endpoints via MSW. Covers :
//                - code profile : the chained loads (project → runs →
//                  auto-select run → tree → auto-select file → blob)
//                  render the CodeViewer with the file text ;
//                - a binary content-type surfaces the download-only
//                  placeholder instead of dumping bytes ;
//                - the Versions tab lazy-loads the commit history ;
//                - the runs empty-state ;
//                - the docgen profile swaps to the LiveDocsManager view ;
//                - the Download button streams the blob (object-URL path).
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import ArtifactsPage from "@/app/(protected)/projects/[pid]/artifacts/page";

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
vi.mock("next/navigation", () => ({ useParams: () => ({ pid: "p1" }) }));

const P = "/api/v1/projects/p1";

function makeRun(over: Partial<Record<string, unknown>> = {}) {
  return {
    run_id: "run-aaaa1111",
    project_id: "p1",
    tenant_id: "t1",
    started_at: "2026-01-01T00:00:00Z",
    completed_at: "2026-01-01T00:05:00Z",
    status: "completed",
    file_count: 1,
    total_bytes: 2048,
    label: "First run",
    ...over,
  };
}

/** Register the happy code-profile chain ; `blob` lets a test pick the
 *  served content-type + body for the file viewer branch. */
function codeProfileHandlers(opts: { blobType?: string; blobBody?: string } = {}) {
  const blobType = opts.blobType ?? "text/x-python";
  const blobBody = opts.blobBody ?? "print('hello')\n";
  return [
    http.get(P, () => HttpResponse.json({ project_id: "p1", profile: "code", name: "P" })),
    http.get(`${P}/artifacts/runs`, () => HttpResponse.json({ runs: [makeRun()] })),
    http.get(`${P}/artifacts/runs/:rid/tree`, () =>
      HttpResponse.json({
        run_id: "run-aaaa1111",
        nodes: [{ path: "src/app.py", kind: "file", size_bytes: 14, mime_type: "text/x-python" }],
      }),
    ),
    http.get(`${P}/artifacts/runs/:rid/blob`, () =>
      HttpResponse.text(blobBody, { headers: { "Content-Type": blobType } }),
    ),
  ];
}

afterEach(() => vi.restoreAllMocks());

describe("ArtifactsPage code profile", () => {
  it("walks project → runs → tree → blob and renders the file in the viewer", async () => {
    server.use(...codeProfileHandlers());
    render(<ArtifactsPage />);

    // section label resolves from the code profile registry ("Code source")
    await waitFor(() => expect(screen.getByTestId("artifacts-view")).toBeInTheDocument());
    expect(screen.getByText("Code source")).toBeInTheDocument();
    // run auto-selected
    expect(await screen.findByTestId("artifacts-run-run-aaaa1111")).toBeInTheDocument();
    // file auto-selected → CodeViewer with the python text
    expect(await screen.findByTestId("code-viewer")).toBeInTheDocument();
    expect(screen.getByTestId("artifacts-current-path")).toHaveTextContent("src/app.py");
    expect(screen.getByText("print('hello')")).toBeInTheDocument();
  });

  it("shows the binary placeholder for a non-text content-type", async () => {
    server.use(...codeProfileHandlers({ blobType: "application/pdf", blobBody: "%PDF-1.7" }));
    render(<ArtifactsPage />);
    await waitFor(() => expect(screen.getByText(/Binary file/)).toBeInTheDocument());
    expect(screen.queryByTestId("code-viewer")).not.toBeInTheDocument();
  });

  it("lazy-loads the commit history on the Versions tab", async () => {
    server.use(
      ...codeProfileHandlers(),
      http.get(`${P}/git/commits`, () =>
        HttpResponse.json({
          page: 1,
          commits: [
            {
              sha: "abcdef1234",
              message: "feat: initial commit\nbody",
              author_name: "alice",
              author_email: "a@x",
              committed_at: "2026-01-01T00:00:00Z",
            },
          ],
        }),
      ),
    );
    render(<ArtifactsPage />);
    await waitFor(() => expect(screen.getByTestId("artifacts-tabs")).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByTestId("artifacts-tab-versions"));

    expect(await screen.findByTestId("artifacts-commit-abcdef1234")).toBeInTheDocument();
    // only the first line of the commit message is shown
    expect(screen.getByText("feat: initial commit")).toBeInTheDocument();
    expect(screen.getByText(/abcdef12/)).toBeInTheDocument();
  });

  it("renders the empty-state when there are no runs", async () => {
    server.use(
      http.get(P, () => HttpResponse.json({ project_id: "p1", profile: "code", name: "P" })),
      http.get(`${P}/artifacts/runs`, () => HttpResponse.json({ runs: [] })),
    );
    render(<ArtifactsPage />);
    await waitFor(() => expect(screen.getByText(/No artifacts yet/)).toBeInTheDocument());
  });

  it("streams the blob via an object URL on Download", async () => {
    server.use(
      ...codeProfileHandlers(),
      http.get(`${P}/artifacts/runs/:rid/blob`, () =>
        HttpResponse.text("print('hello')\n", {
          headers: {
            "Content-Type": "text/x-python",
            "Content-Disposition": 'attachment; filename="app.py"',
          },
        }),
      ),
    );
    const createObjectURL = vi.fn(() => "blob:mock");
    const revokeObjectURL = vi.fn();
    (URL as unknown as { createObjectURL: unknown }).createObjectURL = createObjectURL;
    (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = revokeObjectURL;
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});

    render(<ArtifactsPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("artifacts-download"));

    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    expect(revokeObjectURL).toHaveBeenCalled();
    expect(alertSpy).not.toHaveBeenCalled();
  });
});

describe("ArtifactsPage docgen profile", () => {
  it("renders the LiveDocsManager view instead of the runs browser", async () => {
    server.use(
      http.get(P, () => HttpResponse.json({ project_id: "p1", profile: "docgen", name: "P" })),
      // LiveDocsManager loads its own tree (live-docs run id) on mount
      http.get(`${P}/artifacts/runs/:rid/tree`, () =>
        HttpResponse.json({ run_id: "live", nodes: [] }),
      ),
    );
    render(<ArtifactsPage />);
    await waitFor(() => expect(screen.getByTestId("artifacts-view-docgen")).toBeInTheDocument());
    // docgen profile's artifacts section label is "Documents"
    expect(screen.getByText("Documents")).toBeInTheDocument();
    // the read-only runs browser must NOT be mounted
    expect(screen.queryByTestId("artifacts-view")).not.toBeInTheDocument();
  });
});
