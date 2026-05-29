// =============================================================================
// File: requirement-detail.test.tsx
// Path: ay_platform_ui/tests/integration/requirement-detail.test.tsx
// Description: Tests for the single requirements document page. Covers the
//              ready (renders slug + raw content), not-found (404) and
//              error states. renderWithProviders + mocked navigation, C5
//              document-detail endpoint via MSW.
// =============================================================================

import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import RequirementDocumentPage from "@/app/(protected)/projects/[pid]/requirements/[slug]/page";
import { server } from "../helpers/msw-server";
import { renderWithProviders } from "../helpers/render";

vi.mock("next/navigation", () => ({
  useParams: () => ({ pid: "p1", slug: "100-SPEC" }),
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => "/projects/p1/requirements/100-SPEC",
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

const DOC_URL = "/api/v1/projects/p1/requirements/documents/100-SPEC";

describe("RequirementDocumentPage", () => {
  it("renders the document header + raw content", async () => {
    server.use(
      http.get(DOC_URL, () =>
        HttpResponse.json({
          slug: "100-SPEC",
          version: 3,
          status: "approved",
          language: "en",
          updated_at: "2026-01-01T00:00:00Z",
          content: "# Spec\nR-100-001 The system SHALL boot.",
        }),
      ),
    );
    renderWithProviders(<RequirementDocumentPage />);

    await waitFor(() => expect(screen.getByTestId("requirement-detail")).toBeInTheDocument());
    expect(screen.getByTestId("document-content")).toHaveTextContent(
      "R-100-001 The system SHALL boot.",
    );
  });

  it("renders the not-found state on a 404", async () => {
    server.use(http.get(DOC_URL, () => HttpResponse.json({ detail: "gone" }, { status: 404 })));
    renderWithProviders(<RequirementDocumentPage />);
    await waitFor(() => expect(screen.getByText(/Document not found/)).toBeInTheDocument());
  });

  it("renders the error state on a non-404 failure", async () => {
    server.use(http.get(DOC_URL, () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    renderWithProviders(<RequirementDocumentPage />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText(/Failed to load:/)).toBeInTheDocument();
  });
});
