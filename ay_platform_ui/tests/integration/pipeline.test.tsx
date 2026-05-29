// =============================================================================
// File: pipeline.test.tsx
// Version: 1
// Path: ay_platform_ui/tests/integration/pipeline.test.tsx
// Description: Integration tests for the code-profile Pipeline page. The
//              page calls useReadyConfig() (which throws until the
//              ConfigProvider is ready), so we mock @/app/providers to
//              hand back a ready config directly and mock next/navigation
//              (useParams / useRouter / useSearchParams). Orchestrator
//              endpoints are scripted via MSW. Covers :
//                - idle : Run disabled until a goal is typed ;
//                - submit → run paused on Gate A (plan) → phase stepper +
//                  Approve button + ?run= persisted ; approve → completed
//                  success panel ;
//                - deep-link to a BLOCKED run → block panel + Retry →
//                  resume to completed ;
//                - run-creation HTTP error surfaces the error panel ;
//                - "New run" resets goal + URL.
//
//              Statuses returned never stay `running` beyond the plan
//              gate, so the 2 s polling loop is never armed (keeps the
//              test free of fake timers).
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PipelinePage from "@/app/(protected)/projects/[pid]/pipeline/page";

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

const { mockRouter, sp } = vi.hoisted(() => {
  const sp = { current: new URLSearchParams() };
  // Mirror Next's router: replace/push update the URL, which is what
  // useSearchParams reads back. Without this the page's "restore from
  // ?run=" effect sees a null run id and resets the freshly-created run.
  const setFromUrl = (url: string) => {
    const q = url.includes("?") ? url.slice(url.indexOf("?") + 1) : "";
    sp.current = new URLSearchParams(q);
  };
  return {
    sp,
    mockRouter: {
      push: vi.fn(setFromUrl),
      replace: vi.fn(setFromUrl),
      back: vi.fn(),
      refresh: vi.fn(),
      prefetch: vi.fn(),
    },
  };
});

vi.mock("@/app/providers", () => ({
  useReadyConfig: () => READY_CONFIG,
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ pid: "p1" }),
  useRouter: () => mockRouter,
  useSearchParams: () => sp.current,
}));

const RUNS = "/api/v1/orchestrator/runs";

function makeRun(over: Partial<Record<string, unknown>> = {}) {
  return {
    run_id: "r1",
    status: "running",
    current_phase: "plan",
    concerns: [],
    trace: [],
    block_reason: null,
    ...over,
  };
}

beforeEach(() => {
  mockRouter.push.mockClear();
  mockRouter.replace.mockClear();
  sp.current = new URLSearchParams();
});

describe("PipelinePage idle + submit", () => {
  it("disables Run until a goal is typed", async () => {
    render(<PipelinePage />);
    const runBtn = screen.getByRole("button", { name: "Run" });
    expect(runBtn).toBeDisabled();

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Goal"), "Build an IBAN validator");
    expect(runBtn).toBeEnabled();
  });

  it("submits a run, pauses on Gate A, then approves to completion", async () => {
    server.use(
      http.post(RUNS, () => HttpResponse.json(makeRun())),
      http.post(`${RUNS}/r1/feedback`, () =>
        HttpResponse.json(makeRun({ status: "completed", current_phase: "review" })),
      ),
    );
    render(<PipelinePage />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Goal"), "Build an IBAN validator");
    await user.click(screen.getByRole("button", { name: "Run" }));

    // Gate A panel + run id persisted in the URL
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Approve plan/ })).toBeInTheDocument(),
    );
    expect(screen.getByText(/Run r1/)).toBeInTheDocument();
    expect(mockRouter.replace).toHaveBeenCalledWith("/projects/p1/pipeline?run=r1");

    await user.click(screen.getByRole("button", { name: /Approve plan/ }));
    await waitFor(() => expect(screen.getByText(/Run completed/)).toBeInTheDocument());
  });

  it("surfaces a run-creation HTTP error", async () => {
    server.use(http.post(RUNS, () => HttpResponse.json({ detail: "nope" }, { status: 500 })));
    render(<PipelinePage />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Goal"), "x");
    await user.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(screen.getByText(/Run creation failed \(500/)).toBeInTheDocument());
  });

  it("resets goal + URL on 'New run'", async () => {
    server.use(http.post(RUNS, () => HttpResponse.json(makeRun())));
    render(<PipelinePage />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Goal"), "something");
    await user.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(screen.getByText(/Run r1/)).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "New run" }));
    expect(mockRouter.replace).toHaveBeenLastCalledWith("/projects/p1/pipeline");
    expect(screen.queryByText(/Run r1/)).not.toBeInTheDocument();
  });
});

describe("PipelinePage deep-link + blocked run", () => {
  it("loads a BLOCKED run from ?run= and retries to completion", async () => {
    sp.current = new URLSearchParams("run=r2");
    let getCalls = 0;
    server.use(
      http.get(`${RUNS}/r2`, () => {
        getCalls += 1;
        return HttpResponse.json(
          makeRun({
            run_id: "r2",
            status: "blocked",
            current_phase: "generate",
            block_reason: "tests failed",
          }),
        );
      }),
      http.post(`${RUNS}/r2/resume`, () =>
        HttpResponse.json(makeRun({ run_id: "r2", status: "completed", current_phase: "review" })),
      ),
    );
    render(<PipelinePage />);

    await waitFor(() => expect(screen.getByText(/Run blocked at/)).toBeInTheDocument());
    expect(screen.getByText("tests failed")).toBeInTheDocument();
    expect(getCalls).toBe(1);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Retry phase/ }));
    await waitFor(() => expect(screen.getByText(/Run completed/)).toBeInTheDocument());
  });

  it("shows an error panel when the deep-linked run cannot be loaded", async () => {
    sp.current = new URLSearchParams("run=r3");
    server.use(
      http.get(`${RUNS}/r3`, () => HttpResponse.json({ detail: "gone" }, { status: 404 })),
    );
    render(<PipelinePage />);
    await waitFor(() => expect(screen.getByText(/Could not load run r3/)).toBeInTheDocument());
  });
});
