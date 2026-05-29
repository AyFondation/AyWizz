// =============================================================================
// File: sources.test.tsx
// Version: 1
// Path: ay_platform_ui/tests/integration/sources.test.tsx
// Description: Integration tests for the project Sources section. Mounts
//              the page inside the providers (useConfigState ready) with
//              a mocked useParams({pid}) and scripts the C7 source
//              endpoints via MSW. Covers :
//                - list states : ready table (rows, count, status badge,
//                  mime label, row link), empty placeholder, error ;
//                - upload card : a supported file stages with a derived
//                  source_id then uploads + resets ; an unsupported
//                  extension is rejected client-side with an error and
//                  never stages ;
//                - per-row delete : confirm → deleteSource + refresh ;
//                  cancel → no network call.
// =============================================================================

import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import SourcesPage from "@/app/(protected)/projects/[pid]/sources/page";
import { server } from "../helpers/msw-server";
import { renderWithProviders } from "../helpers/render";

vi.mock("next/navigation", () => ({
  useParams: () => ({ pid: "p1" }),
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => "/projects/p1/sources",
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

const SOURCES_URL = "/api/v1/memory/projects/p1/sources";

function makeSource(over: Partial<Record<string, unknown>> = {}) {
  return {
    source_id: "doc-a",
    mime_type: "text/markdown",
    size_bytes: 2048,
    parse_status: "indexed",
    parse_error: null,
    chunk_count: 12,
    uploaded_at: "2026-01-01T00:00:00Z",
    uploaded_by: "alice",
    ...over,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SourcesPage list states", () => {
  it("renders the table with a row, count and status badge", async () => {
    server.use(
      http.get(SOURCES_URL, () =>
        HttpResponse.json({
          sources: [
            makeSource(),
            makeSource({
              source_id: "doc-b",
              parse_status: "failed",
              mime_type: "application/pdf",
            }),
          ],
        }),
      ),
    );
    renderWithProviders(<SourcesPage />);

    await waitFor(() => expect(screen.getByTestId("sources-table")).toBeInTheDocument());
    expect(screen.getByTestId("sources-count")).toHaveTextContent("2 sources");
    expect(screen.getByTestId("source-row-doc-a")).toBeInTheDocument();
    expect(screen.getByText("indexed")).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
    // mime label resolved from SUPPORTED_MIME_TYPES
    expect(screen.getByText("Markdown")).toBeInTheDocument();
    expect(screen.getByText("PDF")).toBeInTheDocument();
    // row links to the source detail
    expect(screen.getByText("doc-a")).toHaveAttribute("href", "/projects/p1/sources/doc-a");
  });

  it("shows the empty-state placeholder when there are no sources", async () => {
    server.use(http.get(SOURCES_URL, () => HttpResponse.json({ sources: [] })));
    renderWithProviders(<SourcesPage />);
    await waitFor(() => expect(screen.getByTestId("sources-empty-state")).toBeInTheDocument());
  });

  it("surfaces an HTTP error", async () => {
    server.use(http.get(SOURCES_URL, () => HttpResponse.json({ detail: "x" }, { status: 503 })));
    renderWithProviders(<SourcesPage />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText(/Failed to load sources: HTTP 503/)).toBeInTheDocument();
  });
});

describe("SourcesPage upload card", () => {
  it("stages a supported file with a derived source_id, then uploads + resets", async () => {
    server.use(
      http.get(SOURCES_URL, () => HttpResponse.json({ sources: [] })),
      http.post(`${SOURCES_URL}/upload`, () => HttpResponse.json(makeSource())),
    );
    renderWithProviders(<SourcesPage />);
    await waitFor(() => expect(screen.getByTestId("upload-dropzone")).toBeInTheDocument());

    const user = userEvent.setup();
    const file = new File(["# hi"], "My Doc.md", { type: "text/markdown" });
    await user.upload(screen.getByTestId("upload-file-input"), file);

    // staged view + derived source_id ("My Doc.md" → "my-doc")
    expect(screen.getByTestId("upload-staged-file")).toBeInTheDocument();
    expect(screen.getByTestId("upload-source-id-input")).toHaveValue("my-doc");

    await user.click(screen.getByTestId("upload-submit"));

    // on success the card resets → dropzone visible again
    await waitFor(() => expect(screen.getByTestId("upload-dropzone")).toBeInTheDocument());
  });

  it("rejects an unsupported extension client-side without staging", async () => {
    server.use(http.get(SOURCES_URL, () => HttpResponse.json({ sources: [] })));
    renderWithProviders(<SourcesPage />);
    await waitFor(() => expect(screen.getByTestId("upload-dropzone")).toBeInTheDocument());

    const bad = new File(["MZ"], "virus.exe", { type: "application/octet-stream" });
    // fireEvent.change injects the file straight into onChange, bypassing the
    // input's `accept` filter (which userEvent honours) so we actually exercise
    // the component's own mimeTypeFromFilename rejection — the behaviour we test.
    fireEvent.change(screen.getByTestId("upload-file-input"), { target: { files: [bad] } });

    expect(screen.getByTestId("upload-error")).toHaveTextContent(/Unsupported file extension/);
    expect(screen.queryByTestId("upload-staged-file")).not.toBeInTheDocument();
  });
});

describe("SourcesPage row delete", () => {
  it("deletes after confirmation and refreshes the list", async () => {
    let listCalls = 0;
    server.use(
      http.get(SOURCES_URL, () => {
        listCalls += 1;
        // first load shows the row, the post-delete refresh shows none
        return HttpResponse.json({ sources: listCalls === 1 ? [makeSource()] : [] });
      }),
      http.delete(`${SOURCES_URL}/doc-a`, () => new HttpResponse(null, { status: 204 })),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWithProviders(<SourcesPage />);

    await waitFor(() => expect(screen.getByTestId("source-delete-doc-a")).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(screen.getByTestId("source-delete-doc-a"));

    await waitFor(() => expect(screen.getByTestId("sources-empty-state")).toBeInTheDocument());
    expect(window.confirm).toHaveBeenCalled();
  });

  it("does nothing when the user cancels the confirm dialog", async () => {
    const deleteHandler = vi.fn(() => new HttpResponse(null, { status: 204 }));
    server.use(
      http.get(SOURCES_URL, () => HttpResponse.json({ sources: [makeSource()] })),
      http.delete(`${SOURCES_URL}/doc-a`, deleteHandler),
    );
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderWithProviders(<SourcesPage />);

    await waitFor(() => expect(screen.getByTestId("source-delete-doc-a")).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(screen.getByTestId("source-delete-doc-a"));

    expect(window.confirm).toHaveBeenCalled();
    expect(deleteHandler).not.toHaveBeenCalled();
    // row still present
    expect(screen.getByTestId("source-row-doc-a")).toBeInTheDocument();
  });
});
