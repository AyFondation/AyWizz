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
