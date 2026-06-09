// =============================================================================
// File: operator-quotas.test.tsx
// Path: ay_platform_ui/tests/integration/operator-quotas.test.tsx
// Description: Tests for the global LLM quota console (platform operator,
//              tenant_manager). Covers: forbidden; policy load + edit + save;
//              per-tenant status (verdict + per-window usage/state).
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import QuotasAdminPage from "@/app/(protected)/operator/quotas/page";
import { AuthProvider } from "@/app/auth-provider";
import { fakeJWT } from "../helpers/msw-handlers";
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

vi.mock("@/app/providers", () => ({ useReadyConfig: () => READY_CONFIG }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

const POLICY_URL = "/admin/v1/quota/policy";
const STATUS_URL = "/admin/v1/quota/status";

function policy() {
  return {
    windows: [
      {
        key: "session",
        label: "Session (5h)",
        duration_seconds: 18000,
        anchor: "first_use",
        warn_threshold_pct: 80,
        limits: {},
      },
    ],
    updated_at: null,
  };
}

function seedToken(roles: string[]) {
  window.localStorage.setItem(
    "aywizz.token",
    fakeJWT({
      sub: "u1",
      username: "root",
      tenant_id: "t-platform",
      roles,
      exp: Math.floor(Date.now() / 1000) + 3600,
      iat: Math.floor(Date.now() / 1000),
    }),
  );
}

function renderPage() {
  return render(
    <AuthProvider>
      <QuotasAdminPage />
    </AuthProvider>,
  );
}

beforeEach(() => window.localStorage.clear());

describe("QuotasAdminPage", () => {
  it("forbids a non-tenant_manager", async () => {
    seedToken(["admin"]);
    renderPage();
    await waitFor(() => expect(screen.getByTestId("quotas-forbidden")).toBeInTheDocument());
  });

  it("loads the policy windows", async () => {
    seedToken(["tenant_manager"]);
    server.use(http.get(POLICY_URL, () => HttpResponse.json(policy())));
    renderPage();
    await waitFor(() => expect(screen.getByTestId("quota-window-session")).toBeInTheDocument());
  });

  it("edits a limit and saves the policy", async () => {
    seedToken(["tenant_manager"]);
    let sent: Record<string, unknown> | null = null;
    const put = vi.fn(async ({ request }) => {
      sent = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json(policy());
    });
    server.use(
      http.get(POLICY_URL, () => HttpResponse.json(policy())),
      http.put(POLICY_URL, put),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("quota-window-session")).toBeInTheDocument());
    const user = userEvent.setup();
    // Set the per-USER cost cap (e.g. ~$173 ≈ 160€/month at the user level).
    await user.type(screen.getByTestId("quota-session-user-cost"), "173");
    await user.click(screen.getByTestId("quotas-save"));
    await waitFor(() => expect(put).toHaveBeenCalled());
    expect(
      (sent as unknown as { windows: { limits: { user: { max_cost_usd: number } } }[] }).windows[0]
        .limits.user.max_cost_usd,
    ).toBe(173);
  });

  it("shows a tenant status with an exceeded window", async () => {
    seedToken(["tenant_manager"]);
    server.use(
      http.get(POLICY_URL, () => HttpResponse.json(policy())),
      http.get(STATUS_URL, () =>
        HttpResponse.json({
          tenant_id: "acme",
          warned: false,
          blocked: true,
          windows: [
            {
              key: "session",
              label: "Session (5h)",
              duration_seconds: 18000,
              anchor: "first_use",
              state: "exceeded",
              levels: [],
              usage_cost_usd: 12.0,
              usage_tokens: 2700,
              max_cost_usd: 10.0,
              max_tokens: null,
              cost_pct: 120.0,
              tokens_pct: null,
              seconds_until_reset: null,
            },
          ],
        }),
      ),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("quota-window-session")).toBeInTheDocument());
    const user = userEvent.setup();
    await user.type(screen.getByTestId("quotas-status-tenant"), "acme");
    await user.click(screen.getByTestId("quotas-status-check"));
    await waitFor(() =>
      expect(screen.getByTestId("quotas-status-verdict")).toHaveTextContent("BLOCKED"),
    );
    expect(screen.getByTestId("quota-status-session")).toHaveTextContent("exceeded");
  });
});
