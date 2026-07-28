// =============================================================================
// File: admin-llm-catalogue.test.tsx
// Path: ay_platform_ui/tests/integration/admin-llm-catalogue.test.tsx
// Description: Tests for the per-tenant LLM catalogue admin page. Covers :
//              forbidden view for a non-admin ; list render ; enable toggle ;
//              markup save ; remove ; re-add via the /available picker (800 v10) ;
//              the 404 (model no longer in the platform registry) message.
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LlmCataloguePage from "@/app/(protected)/admin/llm-catalogue/page";
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
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

const CATALOG_URL = "/api/v1/llm/catalog";
const AVAILABLE_URL = "/api/v1/llm/catalog/available";

function regModel(over: Partial<Record<string, unknown>> = {}) {
  return {
    model_id: "m1",
    alias: "claude-haiku-fast",
    provider_id: "p1",
    upstream_model: "claude-haiku-4-5-20251001",
    capabilities: { vision: true, tool_calling: true, context_window: 200000 },
    provider_cost_in_per_1m: 0.8,
    provider_cost_out_per_1m: 4.0,
    default_model_quality: "low",
    enabled: true,
    effective_from: "2026-06-05T00:00:00+00:00",
    ...over,
  };
}

function entry(over: Partial<Record<string, unknown>> = {}) {
  return {
    tenant_id: "t1",
    model_id: "m1",
    enabled: true,
    rate_in_per_1m: null,
    rate_out_per_1m: null,
    markup_pct: null,
    default_for_new_projects: false,
    registry: {
      model_id: "m1",
      alias: "claude-haiku-fast",
      provider_id: "p1",
      upstream_model: "claude-haiku-4-5-20251001",
      capabilities: { vision: true, tool_calling: true, context_window: 200000 },
      provider_cost_in_per_1m: 0.8,
      provider_cost_out_per_1m: 4.0,
      default_model_quality: "low",
      enabled: true,
      effective_from: "2026-06-05T00:00:00+00:00",
    },
    ...over,
  };
}

function seedToken(roles: string[]) {
  window.localStorage.setItem(
    "aywizz.token",
    fakeJWT({
      sub: "u1",
      username: "admin",
      tenant_id: "t1",
      roles,
      exp: Math.floor(Date.now() / 1000) + 3600,
      iat: Math.floor(Date.now() / 1000),
    }),
  );
}

function renderPage() {
  return render(
    <AuthProvider>
      <LlmCataloguePage />
    </AuthProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  // Default: nothing available to re-add (opt-out — all models catalogued).
  // Tests that exercise the picker override this.
  server.use(http.get(AVAILABLE_URL, () => HttpResponse.json({ models: [] })));
});

describe("LlmCataloguePage", () => {
  it("forbids a non-admin", async () => {
    seedToken(["project_owner"]);
    renderPage();
    await waitFor(() => expect(screen.getByTestId("catalogue-forbidden")).toBeInTheDocument());
  });

  it("lists catalogued models for tenant_admin", async () => {
    seedToken(["tenant_admin"]);
    server.use(http.get(CATALOG_URL, () => HttpResponse.json({ models: [entry()] })));
    renderPage();
    await waitFor(() => expect(screen.getByTestId("catalogue-table")).toBeInTheDocument());
    expect(screen.getByTestId("catalogue-row-claude-haiku-fast")).toBeInTheDocument();
  });

  it("shows empty state when the catalogue has no models", async () => {
    seedToken(["admin"]);
    server.use(http.get(CATALOG_URL, () => HttpResponse.json({ models: [] })));
    renderPage();
    await waitFor(() => expect(screen.getByTestId("catalogue-empty")).toBeInTheDocument());
  });

  it("toggles enabled", async () => {
    seedToken(["admin"]);
    const put = vi.fn(() => HttpResponse.json(entry({ enabled: false })));
    server.use(
      http.get(CATALOG_URL, () => HttpResponse.json({ models: [entry()] })),
      http.put(`${CATALOG_URL}/m1`, put),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("catalogue-table")).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(screen.getByTestId("catalogue-enabled-claude-haiku-fast"));
    await waitFor(() => expect(put).toHaveBeenCalled());
  });

  it("toggles default-for-new-projects", async () => {
    seedToken(["admin"]);
    let sent: Record<string, unknown> | null = null;
    const put = vi.fn(async ({ request }) => {
      sent = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json(entry({ default_for_new_projects: true }));
    });
    server.use(
      http.get(CATALOG_URL, () => HttpResponse.json({ models: [entry()] })),
      http.put(`${CATALOG_URL}/m1`, put),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("catalogue-table")).toBeInTheDocument());
    await userEvent.setup().click(screen.getByTestId("catalogue-default-claude-haiku-fast"));
    await waitFor(() => expect(put).toHaveBeenCalled());
    expect(sent).toMatchObject({ default_for_new_projects: true });
  });

  it("saves a markup and removes a model", async () => {
    seedToken(["admin"]);
    const put = vi.fn(() => HttpResponse.json(entry({ markup_pct: 15 })));
    const del = vi.fn(() => new HttpResponse(null, { status: 204 }));
    server.use(
      http.get(CATALOG_URL, () => HttpResponse.json({ models: [entry()] })),
      http.put(`${CATALOG_URL}/m1`, put),
      http.delete(`${CATALOG_URL}/m1`, del),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("catalogue-table")).toBeInTheDocument());
    const user = userEvent.setup();
    await user.type(screen.getByTestId("catalogue-markup-claude-haiku-fast"), "15");
    await user.click(screen.getByTestId("catalogue-markup-save-claude-haiku-fast"));
    await waitFor(() => expect(put).toHaveBeenCalled());
    await user.click(screen.getByTestId("catalogue-remove-claude-haiku-fast"));
    await waitFor(() => expect(del).toHaveBeenCalled());
  });

  it("re-adds a model selected from the /available picker", async () => {
    seedToken(["admin"]);
    const put = vi.fn(() => HttpResponse.json(entry()));
    server.use(
      http.get(CATALOG_URL, () => HttpResponse.json({ models: [] })),
      http.get(AVAILABLE_URL, () => HttpResponse.json({ models: [regModel()] })),
      http.put(`${CATALOG_URL}/m1`, put),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("catalogue-add-select")).toBeInTheDocument());
    const user = userEvent.setup();
    await user.selectOptions(screen.getByTestId("catalogue-add-select"), "m1");
    await user.click(screen.getByTestId("catalogue-add-button"));
    await waitFor(() => expect(put).toHaveBeenCalled());
  });

  it("404 when the picked model was meanwhile removed from the registry", async () => {
    seedToken(["admin"]);
    server.use(
      http.get(CATALOG_URL, () => HttpResponse.json({ models: [] })),
      http.get(AVAILABLE_URL, () => HttpResponse.json({ models: [regModel()] })),
      http.put(`${CATALOG_URL}/m1`, () =>
        HttpResponse.json({ detail: "not in registry" }, { status: 404 }),
      ),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("catalogue-add-select")).toBeInTheDocument());
    const user = userEvent.setup();
    await user.selectOptions(screen.getByTestId("catalogue-add-select"), "m1");
    await user.click(screen.getByTestId("catalogue-add-button"));
    await waitFor(() =>
      expect(screen.getByTestId("catalogue-error")).toHaveTextContent(/Unknown model id/i),
    );
  });
});
