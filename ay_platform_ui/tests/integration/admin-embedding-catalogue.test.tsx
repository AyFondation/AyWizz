// =============================================================================
// File: admin-embedding-catalogue.test.tsx
// Path: ay_platform_ui/tests/integration/admin-embedding-catalogue.test.tsx
// Description: Tests for the tenant embedding catalogue page. Covers: forbidden;
//              list + picker; add from picker; toggle enabled/default; remove;
//              409 remove-in-use.
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import EmbeddingCataloguePage from "@/app/(protected)/admin/embedding-catalogue/page";
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

const CAT = "/api/v1/llm/embedding-catalog";
const AVAIL = "/api/v1/llm/embedding-catalog/available";

function registry(over: Partial<Record<string, unknown>> = {}) {
  return {
    model_id: "m1",
    alias: "all-minilm",
    provider_id: "p1",
    upstream_model: "all-minilm",
    dimension: 384,
    enabled: true,
    effective_from: "2026-08-21T00:00:00+00:00",
    ...over,
  };
}

function catEntry(over: Partial<Record<string, unknown>> = {}) {
  return {
    tenant_id: "t1",
    model_id: "m1",
    enabled: true,
    default_for_new_projects: false,
    registry: registry(),
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
      <EmbeddingCataloguePage />
    </AuthProvider>,
  );
}

beforeEach(() => window.localStorage.clear());

describe("EmbeddingCataloguePage", () => {
  it("forbids a non-admin", async () => {
    seedToken(["project_owner"]);
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("embedding-catalogue-forbidden")).toBeInTheDocument(),
    );
  });

  it("adds a model from the available picker", async () => {
    seedToken(["admin"]);
    const put = vi.fn(() => HttpResponse.json(catEntry({ model_id: "m2" })));
    server.use(
      http.get(CAT, () => HttpResponse.json({ models: [] })),
      http.get(AVAIL, () =>
        HttpResponse.json({ models: [registry({ model_id: "m2", alias: "e5", dimension: 768 })] }),
      ),
      http.put(`${CAT}/m2`, put),
    );
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("embedding-catalogue-add-select")).toBeInTheDocument(),
    );
    const user = userEvent.setup();
    await user.selectOptions(screen.getByTestId("embedding-catalogue-add-select"), "m2");
    await user.click(screen.getByTestId("embedding-catalogue-add-button"));
    await waitFor(() => expect(put).toHaveBeenCalled());
  });

  it("toggles enabled + default, then removes", async () => {
    seedToken(["tenant_admin"]);
    const put = vi.fn(() => HttpResponse.json(catEntry()));
    const del = vi.fn(() => new HttpResponse(null, { status: 204 }));
    server.use(
      http.get(CAT, () => HttpResponse.json({ models: [catEntry()] })),
      http.get(AVAIL, () => HttpResponse.json({ models: [] })),
      http.put(`${CAT}/m1`, put),
      http.delete(`${CAT}/m1`, del),
    );
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("embedding-catalogue-table")).toBeInTheDocument(),
    );
    const user = userEvent.setup();
    await user.click(screen.getByTestId("embedding-catalogue-default-all-minilm"));
    await waitFor(() => expect(put).toHaveBeenCalled());
    await user.click(screen.getByTestId("embedding-catalogue-remove-all-minilm"));
    await waitFor(() => expect(del).toHaveBeenCalled());
  });

  it("surfaces a 409 when removing a model still used by a project", async () => {
    seedToken(["admin"]);
    server.use(
      http.get(CAT, () => HttpResponse.json({ models: [catEntry()] })),
      http.get(AVAIL, () => HttpResponse.json({ models: [] })),
      http.delete(`${CAT}/m1`, () => HttpResponse.json({ detail: "in use" }, { status: 409 })),
    );
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("embedding-catalogue-table")).toBeInTheDocument(),
    );
    const user = userEvent.setup();
    await user.click(screen.getByTestId("embedding-catalogue-remove-all-minilm"));
    await waitFor(() =>
      expect(screen.getByTestId("embedding-catalogue-error")).toHaveTextContent(/still uses/i),
    );
  });
});
