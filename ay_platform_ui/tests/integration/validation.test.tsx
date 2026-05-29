// =============================================================================
// File: validation.test.tsx
// Path: ay_platform_ui/tests/integration/validation.test.tsx
// Description: Tests for the Validation kick-off page. renderWithProviders
//              (useConfigState) + mocked navigation, C6 plugins + run-trigger
//              endpoints via MSW. Covers : plugins ready → domain form +
//              trigger → navigate to the run detail ; plugins empty + error
//              states ; trigger HTTP error surfaced.
// =============================================================================

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ValidationPage from "@/app/(protected)/projects/[pid]/validation/page";
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
  useParams: () => ({ pid: "p1" }),
  useRouter: () => mockRouter,
  usePathname: () => "/projects/p1/validation",
}));

const PLUGINS = "/api/v1/validation/plugins";
const RUNS = "/api/v1/validation/runs";

beforeEach(() => {
  mockRouter.push.mockClear();
});

describe("ValidationPage", () => {
  it("lists plugin domains and triggers a run → navigates to the run detail", async () => {
    server.use(
      http.get(PLUGINS, () =>
        HttpResponse.json([{ plugin_id: "code-checks", domain: "code", version: "1.0" }]),
      ),
      http.post(RUNS, () => HttpResponse.json({ run_id: "run-9" })),
    );
    renderWithProviders(<ValidationPage />);

    await waitFor(() => expect(screen.getByTestId("trigger-domain-select")).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(screen.getByTestId("trigger-submit"));

    await waitFor(() =>
      expect(mockRouter.push).toHaveBeenCalledWith("/projects/p1/validation/run-9"),
    );
  });

  it("shows the no-plugins message when none are installed", async () => {
    server.use(http.get(PLUGINS, () => HttpResponse.json([])));
    renderWithProviders(<ValidationPage />);
    await waitFor(() =>
      expect(screen.getByText(/No validation plugins installed/)).toBeInTheDocument(),
    );
  });

  it("surfaces a plugins load error", async () => {
    server.use(http.get(PLUGINS, () => HttpResponse.json({ detail: "x" }, { status: 500 })));
    renderWithProviders(<ValidationPage />);
    await waitFor(() => expect(screen.getByText(/Failed to load plugins/)).toBeInTheDocument());
  });

  it("surfaces a trigger error without navigating", async () => {
    server.use(
      http.get(PLUGINS, () =>
        HttpResponse.json([{ plugin_id: "code-checks", domain: "code", version: "1.0" }]),
      ),
      http.post(RUNS, () => HttpResponse.json({ detail: "bad" }, { status: 422 })),
    );
    renderWithProviders(<ValidationPage />);
    await waitFor(() => expect(screen.getByTestId("trigger-submit")).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByTestId("trigger-submit"));
    await waitFor(() => expect(screen.getByText(/Trigger failed \(HTTP 422/)).toBeInTheDocument());
    expect(mockRouter.push).not.toHaveBeenCalled();
  });
});
