// =============================================================================
// File: msw-handlers.ts
// Version: 1
// Path: ay_platform_ui/tests/helpers/msw-handlers.ts
// Description: Default MSW request handlers shared by every integration
//              test. Tests that need different responses for a specific
//              endpoint override via `server.use(http.get(...))` in a
//              `beforeEach` block ; the overrides are reset between
//              tests by `server.resetHandlers()` in setup.ts.
//
//              These defaults model a healthy platform :
//                - `/runtime-config.json`   → empty apiBaseUrl
//                - `/ux/config`             → standard brand + features
//                - `/auth/login`            → token for any (alice, *)
//                                             401 for everything else
// =============================================================================

import { HttpResponse, http } from "msw";

export const RUNTIME_CONFIG_DEFAULT = {
  apiBaseUrl: "",
  publicBaseUrl: "",
};

export const UX_CONFIG_DEFAULT = {
  api_version: "v1",
  auth_mode: "local" as const,
  brand: {
    name: "AyWizz Platform",
    short_name: "AyWizz",
    accent_color_hex: "#3b82f6",
  },
  features: {
    chat_enabled: true,
    kg_enabled: true,
    cross_tenant_enabled: false,
    file_download_enabled: true,
  },
};

/** Build a syntactically valid JWT for a given claims set. The
 *  signature segment is fake — tests only exercise the decoder which
 *  doesn't verify signatures. */
export function fakeJWT(claims: Record<string, unknown>): string {
  const enc = (obj: unknown) =>
    Buffer.from(JSON.stringify(obj))
      .toString("base64")
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
  const header = enc({ alg: "HS256", typ: "JWT" });
  const payload = enc(claims);
  return `${header}.${payload}.fake-signature`;
}

const DEFAULT_TOKEN = fakeJWT({
  sub: "user-alice",
  username: "alice",
  tenant_id: "tenant-test",
  roles: ["project_editor", "admin"],
  exp: Math.floor(Date.now() / 1000) + 3600,
  iat: Math.floor(Date.now() / 1000),
});

export const defaultHandlers = [
  // Stage 1 : runtime-config.json — same-origin static JSON.
  http.get("/runtime-config.json", () => HttpResponse.json(RUNTIME_CONFIG_DEFAULT)),
  // Stage 2 : /ux/config — served by C2.
  http.get("/ux/config", () => HttpResponse.json(UX_CONFIG_DEFAULT)),
  // Login : accept "alice" with any password ; 401 otherwise. Tests
  // can override via server.use() to script other scenarios.
  http.post("/auth/login", async ({ request }) => {
    const body = (await request.json()) as {
      username: string;
      password: string;
    };
    if (body.username === "alice") {
      return HttpResponse.json({
        access_token: DEFAULT_TOKEN,
        token_type: "bearer",
        expires_in: 3600,
      });
    }
    return HttpResponse.json({ detail: "invalid credentials" }, { status: 401 });
  }),
];
