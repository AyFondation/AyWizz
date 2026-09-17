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
  it("forbids a non-platform_manager", async () => {
    seedToken(["admin"]);
    renderPage();
    await waitFor(() => expect(screen.getByTestId("registry-forbidden")).toBeInTheDocument());
  });

  it("lists models and resolves the provider name", async () => {
    seedToken(["platform_manager"]);
    withProviders();
    server.use(http.get(REG, () => HttpResponse.json({ models: [model()] })));
    renderPage();
    await waitFor(() => expect(screen.getByTestId("registry-table")).toBeInTheDocument());
    const row = screen.getByTestId("registry-row-claude-haiku-fast");
    expect(row).toHaveTextContent("Anthropic"); // provider_id resolved to name
  });

  it("creates a model via POST with a provider from the dropdown", async () => {
    seedToken(["platform_manager"]);
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
    seedToken(["platform_manager"]);
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
    seedToken(["platform_manager"]);
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
    seedToken(["platform_manager"]);
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

  // -------------------------------------------------------------------------
  // Capabilities are measured, never ticked (R-800-152)
  // -------------------------------------------------------------------------

  /** A model carrying the full v3 capability shape. The `model()` fixture
   *  above deliberately does NOT — it is the pre-R-800-152 document shape,
   *  which the page must still render. */
  function measuredModel(over: Partial<Record<string, unknown>> = {}) {
    return model({
      capabilities: {
        vision: false,
        tool_calling: true,
        thinking: false,
        context_window: 200000,
        provenance: {
          vision: "measured",
          tool_calling: "measured",
          thinking: "unknown",
          context_window: "asserted",
        },
        disabled: { vision: false, tool_calling: false, thinking: false },
      },
      ...over,
    });
  }

  it("renders a pre-R-800-152 model without a disabled block", async () => {
    // Regression guard: the first version of this page read
    // `capabilities.disabled.vision` unguarded and crashed the whole table on
    // every model stored before the field existed.
    seedToken(["platform_manager"]);
    withProviders();
    server.use(http.get(REG, () => HttpResponse.json({ models: [model()] })));
    renderPage();
    await waitFor(() => expect(screen.getByTestId("registry-table")).toBeInTheDocument());
    expect(screen.getByTestId("registry-row-claude-haiku-fast")).toBeInTheDocument();
  });

  it("offers no capability checkbox in the edit form", async () => {
    seedToken(["platform_manager"]);
    withProviders();
    server.use(http.get(REG, () => HttpResponse.json({ models: [measuredModel()] })));
    renderPage();
    await waitFor(() => expect(screen.getByTestId("registry-table")).toBeInTheDocument());
    await userEvent.setup().click(screen.getByTestId("registry-edit-claude-haiku-fast"));

    // The old controls are gone: a capability cannot be granted by hand.
    expect(screen.queryByTestId("registry-form-vision")).not.toBeInTheDocument();
    expect(screen.queryByTestId("registry-form-tools")).not.toBeInTheDocument();
    expect(screen.getByTestId("registry-form-capabilities")).toBeInTheDocument();
  });

  it("shows provenance and offers 'do not use' only where supported", async () => {
    seedToken(["platform_manager"]);
    withProviders();
    server.use(http.get(REG, () => HttpResponse.json({ models: [measuredModel()] })));
    renderPage();
    await waitFor(() => expect(screen.getByTestId("registry-table")).toBeInTheDocument());
    await userEvent.setup().click(screen.getByTestId("registry-edit-claude-haiku-fast"));

    // Supported → the operator may refuse it.
    expect(screen.getByTestId("capability-tool_calling-disable")).toBeInTheDocument();
    // Not supported → no toggle at all. Offering "don't use" there would imply
    // an opposite switch exists somewhere; it does not, by construction.
    expect(screen.queryByTestId("capability-vision-disable")).not.toBeInTheDocument();
    // "never checked" is visually distinct from "checked, and it cannot".
    expect(screen.getByTestId("capability-thinking")).toHaveTextContent("never checked");
  });

  it("hides a disabled capability from the row summary", async () => {
    seedToken(["platform_manager"]);
    withProviders();
    server.use(
      http.get(REG, () =>
        HttpResponse.json({
          models: [
            measuredModel({
              capabilities: {
                vision: true,
                tool_calling: true,
                thinking: false,
                context_window: 200000,
                provenance: {
                  vision: "measured",
                  tool_calling: "measured",
                  thinking: "measured",
                  context_window: "asserted",
                },
                // The model CAN see; the operator chose not to use it.
                disabled: { vision: true, tool_calling: false, thinking: false },
              },
            }),
          ],
        }),
      ),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("registry-table")).toBeInTheDocument());

    const row = screen.getByTestId("registry-row-claude-haiku-fast");
    // The summary describes what the platform MAY use — listing a refused
    // capability would describe routing that will not happen.
    expect(row).not.toHaveTextContent("vision");
    expect(row).toHaveTextContent("tools");
  });

  it("writes measured capabilities back after a probe", async () => {
    seedToken(["platform_manager"]);
    withProviders();
    let written: { capabilities: Record<string, unknown> } | null = null;
    server.use(
      http.get(REG, () => HttpResponse.json({ models: [measuredModel()] })),
      http.post(`${REG}/m1/probe`, () =>
        HttpResponse.json({
          model_id: "m1",
          alias: "claude-haiku-fast",
          provider_id: "p1",
          outcome: "ok",
          resolved_target: "anthropic/claude-haiku-4-5-20251001",
          status_code: 200,
          latency_ms: 412,
          error: null,
          capabilities: [
            { capability: "tool_calling", supported: true, evidence: "measured", detail: null },
            { capability: "vision", supported: false, evidence: "measured", detail: "refused" },
            { capability: "thinking", supported: true, evidence: "measured", detail: null },
          ],
        }),
      ),
      http.put(`${REG}/m1`, async ({ request }) => {
        written = (await request.json()) as { capabilities: Record<string, unknown> };
        return HttpResponse.json(measuredModel());
      }),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("registry-table")).toBeInTheDocument());
    await userEvent.setup().click(screen.getByTestId("registry-probe-claude-haiku-fast"));

    // The WRITE is the point: a measurement left on screen would leave the
    // registry holding the guess it just disproved, and routing reads the
    // registry.
    await waitFor(() => expect(written).not.toBeNull());
    const body = written as unknown as { capabilities: Record<string, unknown> };
    expect(body.capabilities.tool_calling).toBe(true);
    expect(body.capabilities.vision).toBe(false);
    expect(body.capabilities.thinking).toBe(true);
    expect(body.capabilities.provenance).toMatchObject({
      vision: "measured",
      tool_calling: "measured",
      thinking: "measured",
    });
  });

  it("reports a failed probe verbatim instead of writing anything", async () => {
    seedToken(["platform_manager"]);
    withProviders();
    const put = vi.fn(async () => HttpResponse.json(measuredModel()));
    server.use(
      http.get(REG, () => HttpResponse.json({ models: [measuredModel()] })),
      http.post(`${REG}/m1/probe`, () =>
        HttpResponse.json({
          model_id: "m1",
          alias: "claude-haiku-fast",
          provider_id: "p1",
          outcome: "rejected",
          resolved_target: "anthropic/claude-haiku-4-5-20251001",
          status_code: 401,
          latency_ms: 88,
          error: "invalid x-api-key",
          capabilities: [],
        }),
      ),
      http.put(`${REG}/m1`, put),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("registry-table")).toBeInTheDocument());
    await userEvent.setup().click(screen.getByTestId("registry-probe-claude-haiku-fast"));

    const err = await screen.findByTestId("registry-error");
    // Upstream text verbatim: replacing it with something friendlier is how
    // the 2026-09-09 outage reached end users as a blank message.
    expect(err).toHaveTextContent("invalid x-api-key");
    expect(err).toHaveTextContent("anthropic/claude-haiku-4-5-20251001");
    expect(put).not.toHaveBeenCalled();
  });
});
