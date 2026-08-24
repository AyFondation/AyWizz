// =============================================================================
// File: admin-embedding-providers.test.tsx
// Path: ay_platform_ui/tests/integration/admin-embedding-providers.test.tsx
// Description: Tests for the embedding provider registry page. Covers:
//              forbidden; list + key status; create with a key + adapter in one
//              submit; 503 partial success; edit; delete.
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import EmbeddingProvidersPage from "@/app/(protected)/admin/embedding-providers/page";
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

const PROV = "/admin/v1/llm/embedding-providers";

function provider(over: Partial<Record<string, unknown>> = {}) {
  return {
    provider_id: "p1",
    name: "Ollama",
    adapter: "ollama",
    base_url: "http://ollama:11434",
    effective_from: "2026-08-21T00:00:00+00:00",
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
      <EmbeddingProvidersPage />
    </AuthProvider>,
  );
}

beforeEach(() => window.localStorage.clear());

describe("EmbeddingProvidersPage", () => {
  it("forbids a non-platform_manager", async () => {
    seedToken(["admin"]);
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("embedding-providers-forbidden")).toBeInTheDocument(),
    );
  });

  it("lists providers with key status", async () => {
    seedToken(["platform_manager"]);
    server.use(
      http.get(PROV, () =>
        HttpResponse.json({
          providers: [provider({ adapter: "openai", key_status: "set", api_key_hint: "…abcd" })],
        }),
      ),
    );
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("embedding-providers-table")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("embedding-provider-keystatus-p1")).toHaveTextContent("set …abcd");
  });

  it("creates a provider with a chosen adapter AND stores its key", async () => {
    seedToken(["platform_manager"]);
    let body: Record<string, unknown> | null = null;
    const post = vi.fn(async ({ request }) => {
      body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json(provider({ provider_id: "p9", name: "OpenAI", adapter: "openai" }));
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
    await user.click(screen.getByTestId("embedding-providers-add"));
    await user.type(screen.getByTestId("embedding-provider-form-name"), "OpenAI");
    await user.selectOptions(screen.getByTestId("embedding-provider-form-adapter"), "openai");
    await user.type(
      screen.getByTestId("embedding-provider-form-baseurl"),
      "https://api.openai.com/v1",
    );
    await user.type(screen.getByTestId("embedding-provider-form-key"), "sk-secret");
    await user.click(screen.getByTestId("embedding-provider-form-submit"));
    await waitFor(() => expect(post).toHaveBeenCalled());
    await waitFor(() => expect(putKey).toHaveBeenCalled());
    expect(body).toMatchObject({
      name: "OpenAI",
      adapter: "openai",
      base_url: "https://api.openai.com/v1",
    });
  });

  it("reports a partial success when key storage is unavailable (503)", async () => {
    seedToken(["platform_manager"]);
    server.use(
      http.get(PROV, () => HttpResponse.json({ providers: [] })),
      http.put(`${PROV}/:id/api-key`, () =>
        HttpResponse.json({ detail: "no key" }, { status: 503 }),
      ),
      http.post(PROV, () => HttpResponse.json(provider({ provider_id: "p9", adapter: "openai" }))),
    );
    renderPage();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("embedding-providers-add"));
    await user.type(screen.getByTestId("embedding-provider-form-name"), "X");
    await user.selectOptions(screen.getByTestId("embedding-provider-form-adapter"), "openai");
    await user.type(screen.getByTestId("embedding-provider-form-baseurl"), "https://x");
    await user.type(screen.getByTestId("embedding-provider-form-key"), "sk-x");
    await user.click(screen.getByTestId("embedding-provider-form-submit"));
    await waitFor(() =>
      expect(screen.getByTestId("embedding-providers-notice")).toHaveTextContent(/NOT stored/i),
    );
  });

  it("edits then deletes a provider by id", async () => {
    seedToken(["platform_manager"]);
    const put = vi.fn(() => HttpResponse.json(provider({ base_url: "http://new" })));
    const del = vi.fn(() => new HttpResponse(null, { status: 204 }));
    server.use(
      http.get(PROV, () => HttpResponse.json({ providers: [provider()] })),
      http.put(`${PROV}/p1`, put),
      http.delete(`${PROV}/p1`, del),
    );
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("embedding-providers-table")).toBeInTheDocument(),
    );
    const user = userEvent.setup();
    await user.click(screen.getByTestId("embedding-provider-edit-p1"));
    await user.click(screen.getByTestId("embedding-provider-form-submit"));
    await waitFor(() => expect(put).toHaveBeenCalled());
    await user.click(screen.getByTestId("embedding-provider-delete-p1"));
    await waitFor(() => expect(del).toHaveBeenCalled());
  });
});
