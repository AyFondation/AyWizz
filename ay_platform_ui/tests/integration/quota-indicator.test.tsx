// =============================================================================
// File: quota-indicator.test.tsx
// Path: ay_platform_ui/tests/integration/quota-indicator.test.tsx
// Description: Tests for the always-on personal quota pill (navbar). Covers:
//              hidden when not authenticated; warn pill + popover breakdown;
//              blocked pill. Polls GET /api/v1/quota/me.
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/app/auth-provider";
import { ConfigProvider } from "@/app/providers";
import { QuotaIndicator } from "@/components/quota-indicator";
import { fakeJWT } from "../helpers/msw-handlers";
import { server } from "../helpers/msw-server";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/",
}));

const NOW = Math.floor(Date.now() / 1000);
const ME = "/api/v1/quota/me";

function win(over: Partial<Record<string, unknown>> = {}) {
  return {
    key: "session",
    label: "Session (5h)",
    duration_seconds: 18000,
    usage_cost_usd: 8.5,
    usage_tokens: 1000,
    max_cost_usd: 10.0,
    max_tokens: null,
    cost_pct: 85.0,
    tokens_pct: null,
    state: "warn",
    seconds_until_reset: 5520, // 1h32
    anchor: "first_use",
    levels: [
      {
        level: "user",
        usage_cost_usd: 8.5,
        usage_tokens: 1000,
        max_cost_usd: 10.0,
        max_tokens: null,
        cost_pct: 85.0,
        tokens_pct: null,
        state: "warn",
      },
    ],
    ...over,
  };
}

function seedAuthenticated(): void {
  window.localStorage.setItem(
    "aywizz.token",
    fakeJWT({
      sub: "u1",
      username: "alice",
      tenant_id: "tenant-x",
      roles: ["project_editor"],
      exp: NOW + 3600,
      iat: NOW,
    }),
  );
}

function renderIndicator() {
  return render(
    <ConfigProvider>
      <AuthProvider>
        <QuotaIndicator />
      </AuthProvider>
    </ConfigProvider>,
  );
}

beforeEach(() => window.localStorage.clear());

describe("QuotaIndicator", () => {
  it("renders nothing when not authenticated", async () => {
    const { container } = renderIndicator();
    await waitFor(() => expect(container.querySelector("button")).toBeNull());
  });

  it("shows a warn pill and opens a per-window popover", async () => {
    seedAuthenticated();
    server.use(
      http.get(ME, () =>
        HttpResponse.json({
          tenant_id: "tenant-x",
          warned: true,
          blocked: false,
          windows: [
            win(),
            win({ key: "week", label: "Week", cost_pct: null, max_cost_usd: null, state: "ok" }),
          ],
        }),
      ),
    );
    renderIndicator();
    // Default framing is "remaining": 85% used → 15% left, with the reset countdown.
    await waitFor(() =>
      expect(screen.getByTestId("quota-indicator")).toHaveTextContent("15% left · resets in 1h32"),
    );
    const user = userEvent.setup();
    await user.click(screen.getByTestId("quota-indicator"));
    expect(screen.getByTestId("quota-popover")).toBeInTheDocument();
    expect(screen.getByTestId("quota-pop-session")).toHaveTextContent("85% used");
    // Consumed AND remaining are both shown: $8.50 used, $1.50 left.
    expect(screen.getByTestId("quota-consumed-session")).toHaveTextContent("Used $8.50");
    expect(screen.getByTestId("quota-remaining-session")).toHaveTextContent("Left $1.50");
    // Per-level breakdown: the user level shows "You 85%".
    expect(screen.getByTestId("quota-level-session-user")).toHaveTextContent("You 85%");
    // The limitless window reads "no limit".
    expect(screen.getByTestId("quota-pop-week")).toHaveTextContent("no limit");
  });

  it("respects the 'consumed' framing preference", async () => {
    seedAuthenticated();
    // The preference is per-user localStorage, keyed by sub.
    window.localStorage.setItem("aywizz.prefs.u1", JSON.stringify({ quotaFraming: "consumed" }));
    server.use(
      http.get(ME, () =>
        HttpResponse.json({
          tenant_id: "tenant-x",
          warned: true,
          blocked: false,
          windows: [win()],
        }),
      ),
    );
    renderIndicator();
    await waitFor(() =>
      expect(screen.getByTestId("quota-indicator")).toHaveTextContent("85% used"),
    );
  });

  it("shows an exceeded/blocked pill", async () => {
    seedAuthenticated();
    server.use(
      http.get(ME, () =>
        HttpResponse.json({
          tenant_id: "tenant-x",
          warned: false,
          blocked: true,
          windows: [win({ usage_cost_usd: 12, cost_pct: 120, state: "exceeded" })],
        }),
      ),
    );
    renderIndicator();
    await waitFor(() =>
      expect(screen.getByTestId("quota-indicator")).toHaveTextContent("Quota exceeded"),
    );
  });
});
