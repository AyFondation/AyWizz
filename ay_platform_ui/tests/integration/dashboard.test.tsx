// =============================================================================
// File: dashboard.test.tsx
// Version: 1
// Path: ay_platform_ui/tests/integration/dashboard.test.tsx
// Description: Integration tests for the /dashboard page. Renders
//              decoded JWT claims so the developer can confirm
//              auth-state propagation end-to-end. The page assumes
//              ProtectedLayout has already gated on authenticated,
//              so we render directly inside AuthProvider with a
//              seeded token.
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import DashboardPage from "@/app/(protected)/dashboard/page";
import { AuthProvider } from "@/app/auth-provider";
import { ConfigProvider } from "@/app/providers";

import { fakeJWT } from "../helpers/msw-handlers";

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
  useRouter: () => mockRouter,
}));

const NOW = Math.floor(Date.now() / 1000);

function seedAndRender(claims: Record<string, unknown>) {
  window.localStorage.setItem(
    "aywizz.token",
    fakeJWT({
      sub: "user-alice",
      username: "alice",
      tenant_id: "tenant-x",
      roles: ["project_editor"],
      exp: NOW + 3600,
      iat: NOW,
      ...claims,
    }),
  );
  return render(
    <ConfigProvider>
      <AuthProvider>
        <DashboardPage />
      </AuthProvider>
    </ConfigProvider>,
  );
}

describe("DashboardPage rendering", () => {
  it("displays the welcome message with username", async () => {
    seedAndRender({ username: "platform-admin" });
    await waitFor(() => {
      expect(screen.getByText(/welcome, platform-admin/i)).toBeInTheDocument();
    });
  });

  it("falls back to sub when username is absent", async () => {
    seedAndRender({ username: undefined });
    await waitFor(() => {
      expect(screen.getByText(/welcome, user-alice/i)).toBeInTheDocument();
    });
  });

  it("renders all JWT claims in the claims panel", async () => {
    seedAndRender({
      sub: "u-1",
      username: "alice",
      tenant_id: "t-acme",
      roles: ["admin", "project_owner"],
    });
    await waitFor(() => {
      expect(screen.getByText("u-1")).toBeInTheDocument();
    });
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("t-acme")).toBeInTheDocument();
    expect(screen.getByText("admin")).toBeInTheDocument();
    expect(screen.getByText("project_owner")).toBeInTheDocument();
  });

  it('shows "(not set)" / "(not scoped)" for missing optional claims', async () => {
    seedAndRender({ username: undefined, tenant_id: undefined, roles: [] });
    await waitFor(() => {
      // Username falls back to sub in the welcome line, but the
      // dedicated row says "(not set)".
      expect(screen.getByText("(not set)")).toBeInTheDocument();
    });
    expect(screen.getByText("(not scoped)")).toBeInTheDocument();
    expect(screen.getByText("none")).toBeInTheDocument();
  });

  it("renders the token expiration as a human-readable timestamp", async () => {
    const exp = NOW + 60;
    seedAndRender({ exp });
    await waitFor(() => {
      // toLocaleString output varies by env ; assert that the year
      // (computed from exp) is present in the rendered text.
      const expectedYear = String(new Date(exp * 1000).getFullYear());
      expect(screen.getByText(new RegExp(expectedYear))).toBeInTheDocument();
    });
  });

  it("returns null when the auth state is not authenticated", () => {
    // No token seeded → AuthProvider hydrates as anonymous.
    // The page short-circuits with `return null`.
    const { container } = render(
      <ConfigProvider>
        <AuthProvider>
          <DashboardPage />
        </AuthProvider>
      </ConfigProvider>,
    );
    // Dashboard heading SHALL NOT appear during initial loading or
    // after hydrating to anonymous.
    expect(container.querySelector("h1")).toBeNull();
  });
});
