// =============================================================================
// File: admin-llm-providers.test.tsx
// Path: ay_platform_ui/tests/integration/admin-llm-providers.test.tsx
// Description: Tests for the LLM provider registry page. Covers: forbidden;
//              list + key status; create with a key in one submit; 503 partial
//              success; edit by id; delete by id.
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LlmProvidersPage from "@/app/(protected)/admin/llm-providers/page";
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

const PROV = "/admin/v1/llm/providers";

function provider(over: Partial<Record<string, unknown>> = {}) {
  return {
    provider_id: "p1",
    name: "Anthropic",
    base_url: "https://api.anthropic.com",
    wire_format: "anthropic",
    effective_from: "2026-06-08T00:00:00+00:00",
    key_status: "not_set",
    api_key_hint: "",
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

function renderPage() {
  return render(
    <AuthProvider>
      <LlmProvidersPage />
    </AuthProvider>,
  );
}

beforeEach(() => window.localStorage.clear());

describe("LlmProvidersPage", () => {
  it("forbids a non-platform_manager", async () => {
    seedToken(["admin"]);
    renderPage();
    await waitFor(() => expect(screen.getByTestId("providers-forbidden")).toBeInTheDocument());
  });

  it("lists providers with key status", async () => {
    seedToken(["platform_manager"]);
    server.use(
      http.get(PROV, () =>
        HttpResponse.json({
          providers: [provider({ key_status: "set", api_key_hint: "…abcd" })],
        }),
      ),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("providers-table")).toBeInTheDocument());
    expect(screen.getByTestId("provider-keystatus-p1")).toHaveTextContent("set …abcd");
  });

  it("creates a provider AND stores its key in one submit", async () => {
    seedToken(["platform_manager"]);
    let body: Record<string, unknown> | null = null;
    const post = vi.fn(async ({ request }) => {
      body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json(provider({ provider_id: "p9", name: "OpenAI" }));
    });
    const putKey = vi.fn(() =>
      HttpResponse.json(provider({ provider_id: "p9", key_status: "set" })),
    );
    server.use(
      http.get(PROV, () => HttpResponse.json({ providers: [] })),
      http.put(`${PROV}/:id/api-key`, putKey),
      http.post(PROV, post),
    );
    renderPage();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("providers-add"));
    await user.type(screen.getByTestId("provider-form-name"), "OpenAI");
    await user.type(screen.getByTestId("provider-form-baseurl"), "https://api.openai.com/v1");
    await user.clear(screen.getByTestId("provider-form-wire"));
    await user.type(screen.getByTestId("provider-form-wire"), "openai");
    await user.type(screen.getByTestId("provider-form-key"), "sk-secret");
    await user.click(screen.getByTestId("provider-form-submit"));
    await waitFor(() => expect(post).toHaveBeenCalled());
    await waitFor(() => expect(putKey).toHaveBeenCalled());
    expect(body).toMatchObject({
      name: "OpenAI",
      base_url: "https://api.openai.com/v1",
      wire_format: "openai",
    });
  });

  it("reports a partial success when key storage is unavailable (503)", async () => {
    seedToken(["platform_manager"]);
    server.use(
      http.get(PROV, () => HttpResponse.json({ providers: [] })),
      http.put(`${PROV}/:id/api-key`, () =>
        HttpResponse.json({ detail: "no key" }, { status: 503 }),
      ),
      http.post(PROV, () => HttpResponse.json(provider({ provider_id: "p9" }))),
    );
    renderPage();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("providers-add"));
    await user.type(screen.getByTestId("provider-form-name"), "X");
    await user.type(screen.getByTestId("provider-form-baseurl"), "https://x");
    await user.type(screen.getByTestId("provider-form-key"), "sk-x");
    await user.click(screen.getByTestId("provider-form-submit"));
    await waitFor(() =>
      expect(screen.getByTestId("providers-notice")).toHaveTextContent(/NOT stored/i),
    );
  });

  it("edits then deletes a provider by id", async () => {
    seedToken(["platform_manager"]);
    const put = vi.fn(() => HttpResponse.json(provider({ base_url: "https://new" })));
    const del = vi.fn(() => new HttpResponse(null, { status: 204 }));
    server.use(
      http.get(PROV, () => HttpResponse.json({ providers: [provider()] })),
      http.put(`${PROV}/p1`, put),
      http.delete(`${PROV}/p1`, del),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("providers-table")).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(screen.getByTestId("provider-edit-p1"));
    await user.click(screen.getByTestId("provider-form-submit"));
    await waitFor(() => expect(put).toHaveBeenCalled());
    await user.click(screen.getByTestId("provider-delete-p1"));
    await waitFor(() => expect(del).toHaveBeenCalled());
  });

  // -------------------------------------------------------------------------
  // Probe (R-800-150) — through the pipeline, verdict reported verbatim
  // -------------------------------------------------------------------------

  it("reports a reachable provider with the model that carried the probe", async () => {
    seedToken(["platform_manager"]);
    server.use(
      http.get(PROV, () => HttpResponse.json({ providers: [provider()] })),
      http.post(`${PROV}/p1/probe`, () =>
        HttpResponse.json({
          provider_id: "p1",
          outcome: "ok",
          effective_url: "https://api.anthropic.com",
          status_code: 200,
          latency_ms: 341,
          error: null,
          api_key_hint: "…abcd",
          via_model_id: "m1",
          via_alias: "claude-haiku-fast",
        }),
      ),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("providers-table")).toBeInTheDocument());
    await userEvent.setup().click(screen.getByTestId("provider-probe-p1"));

    const notice = await screen.findByTestId("providers-notice");
    // Which model was tried matters: a failure may be the provider OR that one
    // model, and the operator cannot tell them apart without this.
    expect(notice).toHaveTextContent("claude-haiku-fast");
    expect(notice).toHaveTextContent("https://api.anthropic.com");
  });

  it("surfaces the upstream error and the resolved api_base on failure", async () => {
    seedToken(["platform_manager"]);
    server.use(
      http.get(PROV, () => HttpResponse.json({ providers: [provider()] })),
      http.post(`${PROV}/p1/probe`, () =>
        HttpResponse.json({
          provider_id: "p1",
          outcome: "unreachable",
          effective_url: "https://api.anthropic.com",
          status_code: null,
          latency_ms: 15000,
          error: "name or service not known",
          api_key_hint: "…abcd",
          via_model_id: "m1",
          via_alias: "claude-haiku-fast",
        }),
      ),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("providers-table")).toBeInTheDocument());
    await userEvent.setup().click(screen.getByTestId("provider-probe-p1"));

    const err = await screen.findByTestId("providers-error");
    expect(err).toHaveTextContent("unreachable");
    expect(err).toHaveTextContent("name or service not known");
    expect(err).toHaveTextContent("api_base https://api.anthropic.com");
  });

  it("explains that a provider with no model has nothing to exercise", async () => {
    seedToken(["platform_manager"]);
    server.use(
      http.get(PROV, () => HttpResponse.json({ providers: [provider()] })),
      http.post(`${PROV}/p1/probe`, () =>
        HttpResponse.json({
          provider_id: "p1",
          outcome: "not_configured",
          effective_url: "https://api.anthropic.com",
          status_code: null,
          latency_ms: null,
          error:
            "no model is configured for this provider, so there is no pipeline path to exercise. Add a model, then probe.",
          api_key_hint: "…abcd",
          via_model_id: null,
          via_alias: null,
        }),
      ),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("providers-table")).toBeInTheDocument());
    await userEvent.setup().click(screen.getByTestId("provider-probe-p1"));

    // Honest "nothing verified" rather than a fabricated green.
    const err = await screen.findByTestId("providers-error");
    expect(err).toHaveTextContent("no model is configured");
  });
});
