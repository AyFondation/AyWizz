// =============================================================================
// File: run-detail.test.tsx
// Version: 2
// Path: ay_platform_ui/tests/integration/run-detail.test.tsx
// Description: Tests for the validation run detail page. renderWithProviders
//              (useConfigState) + mocked navigation, C6 run + findings via
//              MSW. Runs are returned `completed` so the 2.5 s poll doesn't
//              re-arm. Covers : ready with findings (table + badges +
//              summary), completed with no findings, not-found, error.
//
//              v2 (2026-10-07) : THE FIXTURES WERE THE BUG. They served
//              `{ findings: [...] }`, `total_findings` and a finding
//              `title` — none of which C6 has ever sent — so this file
//              stayed green while the page threw `undefined.length` on
//              every real completed run. A mock is only as good as the
//              contract it encodes, and this one encoded the UI's belief.
//
//              The fixtures are now TYPED (`ValidationRun`, `Finding`,
//              `FindingPage` from `@/lib/types`) instead of
//              `Record<string, unknown>`, which is what let them drift:
//              `tsc` now rejects a fixture that leaves the wire shape,
//              and `lib/types.ts` is itself pinned to the Python models
//              by `ay_platform_core/tests/coherence/
//              test_ui_api_chain.py`. Backend → types → mock, checked
//              end to end.
// =============================================================================

import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import RunDetailPage from "@/app/(protected)/projects/[pid]/validation/[rid]/page";
import type { Finding, FindingPage, ValidationRun } from "@/lib/types";
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

function makeRun(over: Partial<ValidationRun> = {}): ValidationRun {
  return {
    run_id: "run-1",
    project_id: "p1",
    domain: "code",
    check_ids: ["C-001"],
    status: "completed",
    findings_count: { blocking: 1, advisory: 0, info: 0 },
    started_at: "2026-01-01T00:00:00Z",
    completed_at: "2026-01-01T00:01:00Z",
    ...over,
  };
}

function makeFinding(over: Partial<Finding> = {}): Finding {
  return {
    finding_id: "f1",
    run_id: "run-1",
    check_id: "C-001",
    domain: "code",
    severity: "blocking",
    status: "open",
    message: "R-100-001 has no implementing artifact",
    fix_hint: "Add an `@relation implements:R-100-001` marker",
    location: "src/app.py:42",
    created_at: "2026-01-01T00:01:00Z",
    ...over,
  };
}

function makePage(items: Finding[]): FindingPage {
  return { run_id: "run-1", total: items.length, items };
}

describe("RunDetailPage", () => {
  it("renders the run summary + findings table", async () => {
    server.use(
      http.get(RUN_URL, () => HttpResponse.json(makeRun())),
      http.get(`${RUN_URL}/findings`, () => HttpResponse.json(makePage([makeFinding()]))),
    );
    renderWithProviders(<RunDetailPage />);

    await waitFor(() => expect(screen.getByTestId("run-detail")).toBeInTheDocument());
    expect(screen.getByTestId("findings-table")).toBeInTheDocument();
    expect(screen.getByTestId("finding-row-f1")).toBeInTheDocument();
    expect(screen.getByText("R-100-001 has no implementing artifact")).toBeInTheDocument();
    expect(screen.getByText("Add an `@relation implements:R-100-001` marker")).toBeInTheDocument(); // fix_hint, previously invisible to the UI
    expect(screen.getByText("blocking")).toBeInTheDocument(); // severity badge
    expect(screen.getByText("completed")).toBeInTheDocument(); // run status badge
    // The per-severity summary C6 actually serves, in place of the
    // `total_findings` scalar the page used to read as `undefined`.
    expect(screen.getByText(/1 blocking · 0 advisory · 0 info/)).toBeInTheDocument();
  });

  it("shows the no-findings message for a clean completed run", async () => {
    server.use(
      http.get(RUN_URL, () =>
        HttpResponse.json(makeRun({ findings_count: { blocking: 0, advisory: 0, info: 0 } })),
      ),
      http.get(`${RUN_URL}/findings`, () => HttpResponse.json(makePage([]))),
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
