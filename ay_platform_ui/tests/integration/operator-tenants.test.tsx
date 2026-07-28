// =============================================================================
// File: operator-tenants.test.tsx
// Path: ay_platform_ui/tests/integration/operator-tenants.test.tsx
// Description: Tests for the tenant management console (platform operator,
//              platform_manager). Covers: forbidden for non-platform_manager;
//              list render with active/deactivated status; create; deactivate;
//              reactivate; delete.
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TenantsAdminPage from "@/app/(protected)/operator/tenants/page";
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

const TENANTS_URL = "/admin/tenants";

function tenant(over: Partial<Record<string, unknown>> = {}) {
  return {
    tenant_id: "acme",
    name: "Acme",
    created_at: "2026-06-05T00:00:00+00:00",
    active: true,
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
      <TenantsAdminPage />
    </AuthProvider>,
  );
}

beforeEach(() => window.localStorage.clear());

describe("TenantsAdminPage", () => {
  it("forbids a non-platform_manager", async () => {
    seedToken(["admin"]);
    renderPage();
    await waitFor(() => expect(screen.getByTestId("tenants-forbidden")).toBeInTheDocument());
  });

  it("lists tenants with status", async () => {
    seedToken(["platform_manager"]);
    server.use(
      http.get(TENANTS_URL, () =>
        HttpResponse.json({ items: [tenant(), tenant({ tenant_id: "off", active: false })] }),
      ),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("tenants-table")).toBeInTheDocument());
    expect(screen.getByTestId("tenant-status-acme")).toHaveTextContent("active");
    expect(screen.getByTestId("tenant-status-off")).toHaveTextContent("deactivated");
  });

  it("creates a tenant", async () => {
    seedToken(["platform_manager"]);
    const post = vi.fn(() => HttpResponse.json(tenant({ tenant_id: "newco" })));
    server.use(
      http.get(TENANTS_URL, () => HttpResponse.json({ items: [] })),
      http.post(TENANTS_URL, post),
    );
    renderPage();
    const user = userEvent.setup();
    await user.type(screen.getByTestId("tenants-create-id"), "newco");
    await user.type(screen.getByTestId("tenants-create-name"), "New Co");
    await user.click(screen.getByTestId("tenants-create-submit"));
    await waitFor(() => expect(post).toHaveBeenCalled());
  });

  it("deactivates then deletes a tenant", async () => {
    seedToken(["platform_manager"]);
    const deact = vi.fn(() => HttpResponse.json(tenant({ active: false })));
    const del = vi.fn(() => new HttpResponse(null, { status: 204 }));
    server.use(
      http.get(TENANTS_URL, () => HttpResponse.json({ items: [tenant()] })),
      http.post(`${TENANTS_URL}/acme/deactivate`, deact),
      http.delete(`${TENANTS_URL}/acme`, del),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("tenants-table")).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(screen.getByTestId("tenant-deactivate-acme"));
    await waitFor(() => expect(deact).toHaveBeenCalled());
    await user.click(screen.getByTestId("tenant-delete-acme"));
    await waitFor(() => expect(del).toHaveBeenCalled());
  });

  it("reactivates a deactivated tenant", async () => {
    seedToken(["platform_manager"]);
    const react = vi.fn(() => HttpResponse.json(tenant({ active: true })));
    server.use(
      http.get(TENANTS_URL, () => HttpResponse.json({ items: [tenant({ active: false })] })),
      http.post(`${TENANTS_URL}/acme/reactivate`, react),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("tenants-table")).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(screen.getByTestId("tenant-reactivate-acme"));
    await waitFor(() => expect(react).toHaveBeenCalled());
  });
});
