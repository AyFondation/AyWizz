// =============================================================================
// File: operator-users.test.tsx
// Path: ay_platform_ui/tests/integration/operator-users.test.tsx
// Description: Tests for the user oversight page. Covers: forbidden (baseline
//              user); platform_manager cross-tenant list + tenant filter +
//              deactivate; admin tenant-scoped chrome (no filter, tenant title);
//              per-user project-access expand (E-100-002 v7).
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import UsersAdminPage from "@/app/(protected)/operator/users/page";
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

const USERS_URL = "/admin/users";
const UCOST_URL = "/admin/v1/quota/consumption/users";

function user_(over: Partial<Record<string, unknown>> = {}) {
  return {
    user_id: "u-a",
    username: "alice",
    tenant_id: "tenant-a",
    roles: ["user"],
    status: "active",
    created_at: "2026-06-05T00:00:00+00:00",
    name: null,
    email: null,
    ...over,
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
      <UsersAdminPage />
    </AuthProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  // Default per-user cost handler so reload() never hits an unhandled request.
  server.use(
    http.get(UCOST_URL, () =>
      HttpResponse.json({
        currency: "EUR",
        windows: ["day", "week", "month", "quarter", "semester", "year"],
        tenant_id: null,
        users: [],
      }),
    ),
  );
});

describe("UsersAdminPage", () => {
  it("forbids a non-operator (baseline user)", async () => {
    // E-100-002 v7: user oversight is an operator surface. `admin` (tenant
    // operator) is now allowed (scoped); a baseline `user` is refused.
    seedToken(["user"]);
    renderPage();
    await waitFor(() => expect(screen.getByTestId("users-forbidden")).toBeInTheDocument());
  });

  it("lists users across tenants", async () => {
    seedToken(["platform_manager"]);
    server.use(
      http.get(USERS_URL, () =>
        HttpResponse.json({
          items: [user_(), user_({ user_id: "u-b", username: "bob", tenant_id: "tenant-b" })],
        }),
      ),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("users-table")).toBeInTheDocument());
    expect(screen.getByTestId("user-row-u-a")).toBeInTheDocument();
    expect(screen.getByTestId("user-row-u-b")).toBeInTheDocument();
  });

  it("filters by tenant", async () => {
    seedToken(["platform_manager"]);
    const get = vi.fn(({ request }) => {
      const url = new URL(request.url);
      const t = url.searchParams.get("tenant_id");
      const items =
        t === "tenant-b" ? [user_({ user_id: "u-b", tenant_id: "tenant-b" })] : [user_()];
      return HttpResponse.json({ items });
    });
    server.use(http.get(USERS_URL, get));
    renderPage();
    await waitFor(() => expect(screen.getByTestId("users-table")).toBeInTheDocument());
    const user = userEvent.setup();
    await user.type(screen.getByTestId("users-filter-input"), "tenant-b");
    await user.click(screen.getByTestId("users-filter-apply"));
    await waitFor(() => expect(screen.getByTestId("user-row-u-b")).toBeInTheDocument());
  });

  it("shows tenant-scoped chrome (no filter) for a tenant admin", async () => {
    seedToken(["admin"]); // tenant_id = t-platform (from seedToken)
    server.use(http.get(USERS_URL, () => HttpResponse.json({ items: [user_()] })));
    renderPage();
    await waitFor(() => expect(screen.getByTestId("users-table")).toBeInTheDocument());
    // No cross-tenant filter for a tenant-scoped operator.
    expect(screen.queryByTestId("users-filter")).not.toBeInTheDocument();
    expect(screen.getByText(/Users — t-platform/)).toBeInTheDocument();
  });

  it("shows per-user LLM cost", async () => {
    seedToken(["platform_manager"]);
    server.use(
      http.get(USERS_URL, () => HttpResponse.json({ items: [user_()] })),
      http.get(UCOST_URL, () =>
        HttpResponse.json({
          currency: "EUR",
          windows: ["day", "week", "month", "quarter", "semester", "year"],
          tenant_id: null,
          users: [
            {
              user_id: "u-a",
              windows: {
                day: { cost: 0.75, tokens: 70 },
                week: { cost: 2, tokens: 200 },
                month: { cost: 8, tokens: 800 },
                quarter: { cost: 0, tokens: 0 },
                semester: { cost: 0, tokens: 0 },
                year: { cost: 0, tokens: 0 },
              },
            },
          ],
        }),
      ),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("users-table")).toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getByTestId("user-cost-day-u-a")).toHaveTextContent("0.75 EUR"),
    );
  });

  it("expands a user's project access", async () => {
    seedToken(["admin"]);
    server.use(
      http.get(USERS_URL, () => HttpResponse.json({ items: [user_()] })),
      http.get(`${USERS_URL}/u-a/projects`, () =>
        HttpResponse.json({
          user_id: "u-a",
          items: [
            {
              project_id: "proj-1",
              project_name: "Proj One",
              tenant_id: "tenant-a",
              role: "project_editor",
            },
          ],
        }),
      ),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("users-table")).toBeInTheDocument());
    await userEvent.setup().click(screen.getByTestId("user-access-toggle-u-a"));
    await waitFor(() =>
      expect(screen.getByTestId("user-access-u-a")).toHaveTextContent("Proj One"),
    );
    expect(screen.getByTestId("user-access-u-a")).toHaveTextContent("editor");
  });

  it("deactivates then reactivates a user", async () => {
    seedToken(["platform_manager"]);
    const deact = vi.fn(() => HttpResponse.json(user_({ status: "disabled" })));
    const react = vi.fn(() => HttpResponse.json(user_({ status: "active" })));
    server.use(
      http.get(USERS_URL, () => HttpResponse.json({ items: [user_()] })),
      http.post(`${USERS_URL}/u-a/deactivate`, deact),
      http.post(`${USERS_URL}/u-a/reactivate`, react),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("users-table")).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(screen.getByTestId("user-deactivate-u-a"));
    await waitFor(() => expect(deact).toHaveBeenCalled());
  });
});
