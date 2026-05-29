// =============================================================================
// File: projects.test.tsx
// Version: 1
// Path: ay_platform_ui/tests/integration/projects.test.tsx
// Description: Integration tests for /projects — the post-login landing
//              list. Mounts the page inside the Config + Auth providers
//              (so useConfigState resolves) and scripts GET
//              /api/v1/projects via MSW. Covers :
//                - ready state : one card per project, the count line,
//                  and the profile badge (known profile → its label ;
//                  unknown profile → "Unknown (<id>)") ;
//                - empty state placeholder ;
//                - error state surfaces "HTTP <status>".
// =============================================================================

import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import ProjectsPage from "@/app/(protected)/projects/page";
import { server } from "../helpers/msw-server";
import { renderWithProviders } from "../helpers/render";

// AuthProvider (pulled in by renderWithProviders) reads useRouter ; the
// page itself uses <Link>. Stub both so jsdom has no real Next router.
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => "/projects",
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

const PROJECTS = [
  {
    project_id: "p1",
    name: "Alpha",
    profile: "code",
    tenant_id: "tenant-test",
    created_by: "alice",
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    project_id: "p2",
    name: "Beta",
    profile: "mystery", // unknown profile id → "Unknown (mystery)" badge
    tenant_id: "tenant-test",
    created_by: "bob",
    created_at: "2026-02-01T00:00:00Z",
  },
];

describe("ProjectsPage", () => {
  it("renders a card per project with the count and profile badges", async () => {
    server.use(http.get("/api/v1/projects", () => HttpResponse.json({ items: PROJECTS })));
    renderWithProviders(<ProjectsPage />);

    await waitFor(() => {
      expect(screen.getByTestId("projects-list")).toBeInTheDocument();
    });
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
    expect(screen.getByText("2 projects")).toBeInTheDocument();
    // known profile → registry label ; unknown → explicit Unknown(<id>)
    expect(screen.getByText("Code")).toBeInTheDocument();
    expect(screen.getByText("Unknown (mystery)")).toBeInTheDocument();
    // card links to the project shell
    expect(screen.getByTestId("project-card-p1")).toHaveAttribute("href", "/projects/p1");
  });

  it("singularises the count for a single project", async () => {
    server.use(http.get("/api/v1/projects", () => HttpResponse.json({ items: [PROJECTS[0]] })));
    renderWithProviders(<ProjectsPage />);
    await waitFor(() => {
      expect(screen.getByText("1 project")).toBeInTheDocument();
    });
  });

  it("shows the empty-state placeholder when there are no projects", async () => {
    server.use(http.get("/api/v1/projects", () => HttpResponse.json({ items: [] })));
    renderWithProviders(<ProjectsPage />);
    await waitFor(() => {
      expect(screen.getByTestId("projects-empty-state")).toBeInTheDocument();
    });
    expect(screen.getByText(/No projects yet/i)).toBeInTheDocument();
  });

  it("surfaces an HTTP error in the error state", async () => {
    server.use(
      http.get("/api/v1/projects", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    renderWithProviders(<ProjectsPage />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByText(/Failed to load projects: HTTP 500/)).toBeInTheDocument();
  });
});
