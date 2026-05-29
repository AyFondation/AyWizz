// =============================================================================
// File: requirements.test.tsx
// Path: ay_platform_ui/tests/integration/requirements.test.tsx
// Description: Tests for the Requirements documents LIST page (read-only).
//              renderWithProviders (useConfigState) + mocked navigation,
//              C5 documents endpoint via MSW. Covers ready list (rows +
//              count + status badge), empty + error states.
// =============================================================================

import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import RequirementsListPage from "@/app/(protected)/projects/[pid]/requirements/page";
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
  usePathname: () => "/projects/p1/requirements",
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

const DOCS_URL = "/api/v1/projects/p1/requirements/documents";

function makeDoc(over: Partial<Record<string, unknown>> = {}) {
  return {
    slug: "100-SPEC",
    version: 2,
    status: "approved",
    language: "en",
    updated_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

describe("RequirementsListPage", () => {
  it("renders the document rows with count + status badge", async () => {
    server.use(
      http.get(DOCS_URL, () =>
        HttpResponse.json({
          documents: [makeDoc(), makeDoc({ slug: "200-DRAFT", status: "draft" })],
        }),
      ),
    );
    renderWithProviders(<RequirementsListPage />);

    await waitFor(() => expect(screen.getByTestId("requirements-list")).toBeInTheDocument());
    expect(screen.getByTestId("requirements-count")).toHaveTextContent("2 documents");
    expect(screen.getByTestId("requirements-row-100-SPEC")).toHaveAttribute(
      "href",
      "/projects/p1/requirements/100-SPEC",
    );
    expect(screen.getByText("approved")).toBeInTheDocument();
    expect(screen.getByText("draft")).toBeInTheDocument();
  });

  it("shows the empty-state when there are no documents", async () => {
    server.use(http.get(DOCS_URL, () => HttpResponse.json({ documents: [] })));
    renderWithProviders(<RequirementsListPage />);
    await waitFor(() => expect(screen.getByTestId("requirements-empty-state")).toBeInTheDocument());
  });

  it("surfaces an HTTP error", async () => {
    server.use(http.get(DOCS_URL, () => HttpResponse.json({ detail: "x" }, { status: 500 })));
    renderWithProviders(<RequirementsListPage />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText(/Failed to load: HTTP 500/)).toBeInTheDocument();
  });
});
