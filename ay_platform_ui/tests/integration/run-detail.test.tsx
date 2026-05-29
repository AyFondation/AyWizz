// =============================================================================
// File: run-detail.test.tsx
// Path: ay_platform_ui/tests/integration/run-detail.test.tsx
// Description: Tests for the validation run detail page. renderWithProviders
//              (useConfigState) + mocked navigation, C6 run + findings via
//              MSW. Runs are returned `completed` so the 2.5 s poll doesn't
//              re-arm. Covers : ready with findings (table + badges +
//              summary), completed with no findings, not-found, error.
// =============================================================================

import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import RunDetailPage from "@/app/(protected)/projects/[pid]/validation/[rid]/page";
import { server } from "../helpers/msw-server";
import { renderWithProviders } from "../helpers/render";

vi.mock("next/navigation", () => ({
  useParams: () => ({ pid: "p1", rid: "run-1" }),
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => "/projects/p1/validation/run-1",
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

const RUN_URL = "/api/v1/validation/runs/run-1";

function makeRun(over: Partial<Record<string, unknown>> = {}) {
  return {
    run_id: "run-1",
    project_id: "p1",
    domain: "code",
    status: "completed",
    started_at: "2026-01-01T00:00:00Z",
    completed_at: "2026-01-01T00:01:00Z",
    total_findings: 1,
    ...over,
  };
}

function makeFinding(over: Partial<Record<string, unknown>> = {}) {
  return {
    finding_id: "f1",
    severity: "error",
    check_id: "C-001",
    title: "Missing requirement link",
    message: "R-100-001 has no implementing artifact",
    location: "src/app.py:42",
    ...over,
  };
}

describe("RunDetailPage", () => {
  it("renders the run summary + findings table", async () => {
    server.use(
      http.get(RUN_URL, () => HttpResponse.json(makeRun())),
      http.get(`${RUN_URL}/findings`, () =>
        HttpResponse.json({ findings: [makeFinding()], total: 1 }),
      ),
    );
    renderWithProviders(<RunDetailPage />);

    await waitFor(() => expect(screen.getByTestId("run-detail")).toBeInTheDocument());
    expect(screen.getByTestId("findings-table")).toBeInTheDocument();
    expect(screen.getByTestId("finding-row-f1")).toBeInTheDocument();
    expect(screen.getByText("Missing requirement link")).toBeInTheDocument();
    expect(screen.getByText("error")).toBeInTheDocument(); // severity badge
    expect(screen.getByText("completed")).toBeInTheDocument(); // run status badge
  });

  it("shows the no-findings message for a clean completed run", async () => {
    server.use(
      http.get(RUN_URL, () => HttpResponse.json(makeRun({ total_findings: 0 }))),
      http.get(`${RUN_URL}/findings`, () => HttpResponse.json({ findings: [], total: 0 })),
    );
    renderWithProviders(<RunDetailPage />);
    await waitFor(() => expect(screen.getByTestId("run-detail")).toBeInTheDocument());
    expect(screen.getByText(/completed without issues/)).toBeInTheDocument();
  });

  it("renders not-found on a 404", async () => {
    server.use(http.get(RUN_URL, () => HttpResponse.json({ detail: "gone" }, { status: 404 })));
    renderWithProviders(<RunDetailPage />);
    await waitFor(() => expect(screen.getByText(/Run not found/)).toBeInTheDocument());
  });

  it("renders the error state on a non-404 failure", async () => {
    server.use(http.get(RUN_URL, () => HttpResponse.json({ detail: "x" }, { status: 500 })));
    renderWithProviders(<RunDetailPage />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText(/Failed to load:/)).toBeInTheDocument();
  });
});
