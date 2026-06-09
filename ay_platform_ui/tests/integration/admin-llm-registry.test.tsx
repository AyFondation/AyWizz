// =============================================================================
// File: admin-llm-registry.test.tsx
// Path: ay_platform_ui/tests/integration/admin-llm-registry.test.tsx
// Description: Tests for the platform LLM model registry page (v4: provider
//              normalisation + stable ids). Covers: forbidden; list render
//              (provider name resolved); create (POST, provider from dropdown);
//              edit a model's cost by id; delete by id; default quality-asc
//              sort + flip. No api_base / key fields (those are on the provider).
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LlmRegistryPage from "@/app/(protected)/admin/llm-registry/page";
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

const REG = "/admin/v1/llm/registry";
const PROV = "/admin/v1/llm/providers";

function provider(over: Partial<Record<string, unknown>> = {}) {
  return {
    provider_id: "p1",
    name: "Anthropic",
    base_url: "https://api.anthropic.com",
    wire_format: "anthropic",
    effective_from: "2026-06-08T00:00:00+00:00",
    key_status: "set",
    api_key_hint: "…abcd",
    ...over,
  };
}

function model(over: Partial<Record<string, unknown>> = {}) {
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
    effective_from: "2026-06-08T00:00:00+00:00",
    ...over,
  };
}

function seedToken(roles: string[]) {
  window.localStorage.setItem(
    "aywizz.token",
    fakeJWT({
      sub: "u1",
      username: "root",
      tenant_id: "t1",
      roles,
      exp: Math.floor(Date.now() / 1000) + 3600,
      iat: Math.floor(Date.now() / 1000),
    }),
  );
}

function withProviders() {
  server.use(http.get(PROV, () => HttpResponse.json({ providers: [provider()] })));
}

function renderPage() {
  return render(
    <AuthProvider>
      <LlmRegistryPage />
    </AuthProvider>,
  );
}

beforeEach(() => window.localStorage.clear());

describe("LlmRegistryPage", () => {
  it("forbids a non-tenant_manager", async () => {
    seedToken(["admin"]);
    renderPage();
    await waitFor(() => expect(screen.getByTestId("registry-forbidden")).toBeInTheDocument());
  });

  it("lists models and resolves the provider name", async () => {
    seedToken(["tenant_manager"]);
    withProviders();
    server.use(http.get(REG, () => HttpResponse.json({ models: [model()] })));
    renderPage();
    await waitFor(() => expect(screen.getByTestId("registry-table")).toBeInTheDocument());
    const row = screen.getByTestId("registry-row-claude-haiku-fast");
    expect(row).toHaveTextContent("Anthropic"); // provider_id resolved to name
  });

  it("creates a model via POST with a provider from the dropdown", async () => {
    seedToken(["tenant_manager"]);
    withProviders();
    let sentBody: Record<string, unknown> | null = null;
    const post = vi.fn(async ({ request }) => {
      sentBody = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json(model({ model_id: "m9", alias: "new" }));
    });
    server.use(
      http.get(REG, () => HttpResponse.json({ models: [] })),
      http.post(REG, post),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("registry-add")).toBeEnabled());
    const user = userEvent.setup();
    await user.click(screen.getByTestId("registry-add"));
    await user.type(screen.getByTestId("registry-form-alias"), "new");
    await user.type(screen.getByTestId("registry-form-upstream"), "gpt-4o");
    await user.click(screen.getByTestId("registry-form-submit"));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(sentBody).toMatchObject({ alias: "new", provider_id: "p1", upstream_model: "gpt-4o" });
  });

  it("edits a model's cost by id (PUT)", async () => {
    seedToken(["tenant_manager"]);
    withProviders();
    let sentBody: Record<string, unknown> | null = null;
    const put = vi.fn(async ({ request }) => {
      sentBody = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json(model());
    });
    server.use(
      http.get(REG, () => HttpResponse.json({ models: [model()] })),
      http.put(`${REG}/m1`, put),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("registry-table")).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(screen.getByTestId("registry-edit-claude-haiku-fast"));
    const costIn = screen.getByTestId("registry-form-costin");
    await user.clear(costIn);
    await user.type(costIn, "1.5");
    await user.click(screen.getByTestId("registry-form-submit"));
    await waitFor(() => expect(put).toHaveBeenCalled());
    expect(sentBody).toMatchObject({ provider_cost_in_per_1m: 1.5 });
  });

  it("deletes a model by id", async () => {
    seedToken(["tenant_manager"]);
    withProviders();
    const del = vi.fn(() => new HttpResponse(null, { status: 204 }));
    server.use(
      http.get(REG, () => HttpResponse.json({ models: [model()] })),
      http.delete(`${REG}/m1`, del),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("registry-table")).toBeInTheDocument());
    await userEvent.setup().click(screen.getByTestId("registry-delete-claude-haiku-fast"));
    await waitFor(() => expect(del).toHaveBeenCalled());
  });

  it("sorts by quality ascending by default and flips on header click", async () => {
    seedToken(["tenant_manager"]);
    withProviders();
    server.use(
      http.get(REG, () =>
        HttpResponse.json({
          models: [
            model({ model_id: "o", alias: "opus", default_model_quality: "high" }),
            model({ model_id: "h", alias: "haiku", default_model_quality: "low" }),
          ],
        }),
      ),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("registry-table")).toBeInTheDocument());
    let rows = screen.getAllByTestId(/^registry-row-/);
    expect(rows[0]).toHaveAttribute("data-testid", "registry-row-haiku");
    await userEvent.setup().click(screen.getByTestId("registry-sort-quality"));
    rows = screen.getAllByTestId(/^registry-row-/);
    expect(rows[0]).toHaveAttribute("data-testid", "registry-row-opus");
  });
});
