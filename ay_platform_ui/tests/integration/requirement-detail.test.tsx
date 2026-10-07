// =============================================================================
// File: requirement-detail.test.tsx
// Version: 2
// Path: ay_platform_ui/tests/integration/requirement-detail.test.tsx
// Description: Tests for the single requirements document page. Covers the
//              ready (renders slug + raw content), not-found (404) and
//              error states. renderWithProviders + mocked navigation, C5
//              document-detail endpoint via MSW.
//
//              v2 (2026-10-07) : the fixture served `content`, which C5's
//              `DocumentPublic` has never had (it serves `body`), so this
//              test proved the page could render a field the server does
//              not send. Now TYPED as `RequirementDocument`, so `tsc`
//              rejects the next such drift and the type itself is pinned
//              to the Python model by `ay_platform_core/tests/coherence/
//              test_ui_api_chain.py`.
// =============================================================================

import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import RequirementDocumentPage from "@/app/(protected)/projects/[pid]/requirements/[slug]/page";
import type { RequirementDocument } from "@/lib/types";
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

const DOC: RequirementDocument = {
  project_id: "p1",
  slug: "100-SPEC",
  version: 3,
  language: "en",
  status: "approved",
  entity_count: 1,
  derives_from: [],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  body: "# Spec\nR-100-001 The system SHALL boot.",
};

describe("RequirementDocumentPage", () => {
  it("renders the document header + raw content", async () => {
    server.use(http.get(DOC_URL, () => HttpResponse.json(DOC)));
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
