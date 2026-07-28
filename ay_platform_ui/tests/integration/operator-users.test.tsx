// =============================================================================
// File: operator-users.test.tsx
// Path: ay_platform_ui/tests/integration/operator-users.test.tsx
// Description: Tests for the cross-tenant user oversight page (platform
//              operator, platform_manager). Covers: forbidden; cross-tenant
//              list; tenant filter; deactivate; reactivate.
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

beforeEach(() => window.localStorage.clear());

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
