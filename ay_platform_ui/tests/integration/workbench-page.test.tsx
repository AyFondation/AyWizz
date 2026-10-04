// =============================================================================
// File: workbench-page.test.tsx
// Path: ay_platform_ui/tests/integration/workbench-page.test.tsx
// Description: The workbench route — 500-SPEC R-500-015, realising R-310-220.
//
//              THE REQUIREMENT THIS FILE GUARDS is "no navigation": the five
//              regions are mounted together and changing container, object or
//              expansion mutates state without a route push. Asserted two
//              ways, because either alone is weak:
//                (1) all five regions are present in one render, and
//                (2) switching container re-renders the objects WITHOUT any
//                    router navigation having been called.
//
//              A spy on the Next router is what makes (2) real. Without it
//              the test would pass against a page that navigates, since
//              jsdom has no address bar to observe.
// =============================================================================

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import WorkbenchPage from "@/app/(protected)/projects/[pid]/workbench/page";
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

const push = vi.fn();
const replace = vi.fn();

vi.mock("@/app/providers", () => ({ useReadyConfig: () => READY_CONFIG }));
vi.mock("next/navigation", () => ({
  useParams: () => ({ pid: "p1" }),
  useRouter: () => ({ push, replace, back: vi.fn(), forward: vi.fn() }),
}));

const BASE = "/api/v1/projects/p1";

const CYCLE = {
  cycle_id: "C-AUTO",
  version: 2,
  title: "Automotive cycle",
  containers: [
    {
      slug: "030-ARCH",
      ordinal: 30,
      scope_id: "SC-002",
      scope: "Architecture design",
      coverage_obligatory: true,
    },
    {
      slug: "020-FUNC",
      ordinal: 20,
      scope_id: "SC-001",
      scope: "Functional description",
      coverage_obligatory: true,
    },
  ],
};

const FUNC_OBJECTS = {
  objects: [
    {
      object_id: "FD-010",
      container: "020-FUNC",
      type: "paragraph",
      ordinal: 1,
      version: 1,
      review_state: "proposed",
      body: "Pedal release shall zero deceleration.",
      produced_by: { workflow: "WF-AUTHOR", workflow_version: 2 },
    },
  ],
};

const ARCH_OBJECTS = {
  objects: [
    {
      object_id: "AD-100",
      container: "030-ARCH",
      type: "paragraph",
      ordinal: 1,
      version: 3,
      review_state: "accepted",
      body: "The brake controller ramps torque.",
    },
  ],
};

function coverage(container: string) {
  return {
    container,
    allocated: ["CUST-001", "CUST-002"],
    uncovered: ["CUST-002"],
    weak: [],
    stale: [],
  };
}

function handlers() {
  return [
    http.get(`${BASE}/process/cycles/C-AUTO/resolved`, () => HttpResponse.json(CYCLE)),
    http.get(`${BASE}/containers/020-FUNC/objects`, () => HttpResponse.json(FUNC_OBJECTS)),
    http.get(`${BASE}/containers/030-ARCH/objects`, () => HttpResponse.json(ARCH_OBJECTS)),
    http.get(`${BASE}/coverage/containers/020-FUNC`, () => HttpResponse.json(coverage("020-FUNC"))),
    http.get(`${BASE}/coverage/containers/030-ARCH`, () => HttpResponse.json(coverage("030-ARCH"))),
    http.get(`${BASE}/coverage/suspect`, () => HttpResponse.json({ links: [] })),
    http.get(`${BASE}/coverage/speculative`, () =>
      HttpResponse.json({
        markings: [
          {
            object_id: "FD-010",
            container: "020-FUNC",
            unaccepted_targets: ["CUST-001"],
            advanced_targets: [],
          },
        ],
        count: 1,
        stale_count: 0,
      }),
    ),
    http.get(`${BASE}/coverage/requirements/:rid`, ({ params }) =>
      HttpResponse.json({
        requirement_id: params.rid,
        out_of_project: false,
        fragments: [],
        allocations: [
          {
            container: "020-FUNC",
            is_covered: true,
            links: [
              {
                object_id: "FD-010",
                container: "020-FUNC",
                target_id: params.rid,
                pinned_version: 2,
                strength: "covered",
                state: "auto-accepted",
                actor: "o.mathieu",
                at: "2026-10-01T09:00:00Z",
              },
            ],
          },
        ],
      }),
    ),
  ];
}

describe("WorkbenchPage (R-500-015)", () => {
  beforeEach(() => {
    push.mockClear();
    replace.mockClear();
    server.use(...handlers());
  });

  const page = () => render(<WorkbenchPage />);

  it("mounts all five regions in one viewport", async () => {
    page();
    await waitFor(() => {
      expect(screen.getByTestId("container-navigator")).toBeInTheDocument();
    });
    expect(screen.getByTestId("object-region")).toBeInTheDocument();
    expect(screen.getByTestId("findings-region")).toBeInTheDocument();
    expect(screen.getByTestId("coverage-region")).toBeInTheDocument();
    expect(screen.getByTestId("conversation-rail")).toBeInTheDocument();
  });

  it("opens the first container of the cascade on arrival", async () => {
    // Useful on arrival rather than five empty panels — and it must be the
    // cycle's FIRST ordinal, not the first element of the response array.
    page();
    await waitFor(() => {
      expect(screen.getByTestId("object-FD-010")).toBeInTheDocument();
    });
  });

  it("switches container WITHOUT navigating (R-310-220)", async () => {
    page();
    await waitFor(() => {
      expect(screen.getByTestId("object-FD-010")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId("container-030-ARCH"));
    await waitFor(() => {
      expect(screen.getByTestId("object-AD-100")).toBeInTheDocument();
    });

    // The whole point: state changed, the route did not.
    expect(push).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });

  it("surfaces the container's uncovered allocations in the findings region", async () => {
    page();
    await waitFor(() => {
      expect(screen.getByTestId("finding-CUST-002")).toBeInTheDocument();
    });
    expect(screen.getByTestId("finding-CUST-002")).toHaveTextContent("not yet answered");
  });

  it("surfaces a speculative object as a finding", async () => {
    page();
    await waitFor(() => {
      expect(screen.getByTestId("finding-FD-010")).toBeInTheDocument();
    });
    expect(screen.getByTestId("finding-FD-010")).toHaveTextContent("not yet accepted");
  });

  it("renders the container figure with its auto-accepted share", async () => {
    page();
    await waitFor(() => {
      expect(screen.getByTestId("container-figure-auto")).toBeInTheDocument();
    });
    // Nothing fetched as auto-accepted yet, and zero is still stated.
    expect(screen.getByTestId("container-figure-auto")).toHaveTextContent("auto-accepted");
  });

  it("expands a link in place and counts its auto-accepted link", async () => {
    page();
    await waitFor(() => {
      expect(screen.getByTestId("object-FD-010")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId("link-FD-010-CUST-001"));
    const row = screen.getByTestId("object-FD-010");
    await waitFor(() => {
      expect(within(row).getByTestId("expansion-CUST-001")).toBeInTheDocument();
    });
    // The fetched link is `auto-accepted`, so the container figure must now
    // report a non-zero share — R-500-019 end to end.
    await waitFor(() => {
      expect(screen.getByTestId("container-figure-auto")).toHaveTextContent("1 of 1 auto-accepted");
    });
    expect(push).not.toHaveBeenCalled();
  });

  it("sends the selection with a conversation turn", async () => {
    page();
    await waitFor(() => {
      expect(screen.getByTestId("object-FD-010")).toBeInTheDocument();
    });
    await userEvent.click(within(screen.getByTestId("object-FD-010")).getByText(/Pedal release/));
    await userEvent.type(screen.getByTestId("rail-input"), "review this");
    await userEvent.click(screen.getByTestId("rail-send"));
    expect(screen.getByTestId("turn-t1")).toHaveTextContent(
      "re: container 020-FUNC, object FD-010",
    );
  });

  it("reports an unavailable cycle instead of an empty navigator", async () => {
    server.use(
      http.get(`${BASE}/process/cycles/C-AUTO/resolved`, () =>
        HttpResponse.json({ detail: "no published cycle" }, { status: 404 }),
      ),
    );
    page();
    await waitFor(() => {
      expect(screen.getByTestId("cycle-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("no-containers")).toBeInTheDocument();
  });

  it("keeps the surface usable when the findings endpoints fail", async () => {
    // A failing panel must not take the page down: the review loop still
    // works without the project-wide findings.
    server.use(
      http.get(`${BASE}/coverage/suspect`, () => HttpResponse.error()),
      http.get(`${BASE}/coverage/speculative`, () => HttpResponse.error()),
    );
    page();
    await waitFor(() => {
      expect(screen.getByTestId("object-FD-010")).toBeInTheDocument();
    });
    expect(screen.getByTestId("findings-region")).toBeInTheDocument();
  });

  it("keeps the objects region usable when coverage fails", async () => {
    server.use(http.get(`${BASE}/coverage/containers/020-FUNC`, () => HttpResponse.error()));
    page();
    await waitFor(() => {
      expect(screen.getByTestId("object-FD-010")).toBeInTheDocument();
    });
    expect(screen.getByTestId("coverage-empty")).toBeInTheDocument();
  });
});
