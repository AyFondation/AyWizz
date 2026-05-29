// =============================================================================
// File: project-layout.test.tsx
// Path: ay_platform_ui/tests/integration/project-layout.test.tsx
// Description: Tests for the project shell layout. renderWithProviders
//              (useConfigState) + mocked navigation. Fetches the project
//              via listProjects (filtered by pid) and renders the sidebar +
//              header shell around its children. Covers : found (header +
//              content + child), not-found, load error, unsupported-profile
//              fallback. Exercises the Sidebar + SidebarProvider too.
// =============================================================================

import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import ProjectShellLayout from "@/app/(protected)/projects/[pid]/layout";
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
  usePathname: () => "/projects/p1/overview",
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

const PROJECTS = "/api/v1/projects";

function makeProject(over: Partial<Record<string, unknown>> = {}) {
  return {
    project_id: "p1",
    name: "Alpha",
    profile: "code",
    tenant_id: "tenant-test",
    created_by: "alice",
    created_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

const child = <div data-testid="shell-child">child content</div>;

describe("ProjectShellLayout", () => {
  it("renders the shell (header + content) around its children for a known project", async () => {
    server.use(http.get(PROJECTS, () => HttpResponse.json({ items: [makeProject()] })));
    renderWithProviders(<ProjectShellLayout>{child}</ProjectShellLayout>);

    await waitFor(() => expect(screen.getByTestId("project-content")).toBeInTheDocument());
    expect(screen.getByTestId("shell-child")).toBeInTheDocument();
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("tenant-test / p1")).toBeInTheDocument();
    // profile label badge ("Code") from the resolved profile
    expect(screen.getAllByText("Code").length).toBeGreaterThan(0);
  });

  it("renders not-found when the project is absent from the tenant list", async () => {
    server.use(
      http.get(PROJECTS, () =>
        HttpResponse.json({ items: [makeProject({ project_id: "other" })] }),
      ),
    );
    renderWithProviders(<ProjectShellLayout>{child}</ProjectShellLayout>);
    await waitFor(() => expect(screen.getByText(/Project not found/)).toBeInTheDocument());
    expect(screen.queryByTestId("shell-child")).not.toBeInTheDocument();
  });

  it("surfaces a load error", async () => {
    server.use(http.get(PROJECTS, () => HttpResponse.json({ detail: "x" }, { status: 500 })));
    renderWithProviders(<ProjectShellLayout>{child}</ProjectShellLayout>);
    await waitFor(() => expect(screen.getByText(/Failed to load project:/)).toBeInTheDocument());
  });

  it("renders the unsupported-profile fallback for an unknown profile", async () => {
    server.use(
      http.get(PROJECTS, () => HttpResponse.json({ items: [makeProject({ profile: "mystery" })] })),
    );
    renderWithProviders(<ProjectShellLayout>{child}</ProjectShellLayout>);
    await waitFor(() => expect(screen.getByText(/doesn't yet support/)).toBeInTheDocument());
    expect(screen.queryByTestId("project-content")).not.toBeInTheDocument();
  });
});
