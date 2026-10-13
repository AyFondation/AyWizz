// =============================================================================
// File: api-docs.test.tsx
// Version: 1
// Path: ay_platform_ui/tests/integration/api-docs.test.tsx
// Description: Tests for the API-documentation page — the one that makes the
//              generated OpenAPI documents readable in a browser.
//
//              THE PROPERTY WORTH GUARDING is not that the page renders.
//              It is that the spec reaches Swagger UI as a parsed OBJECT
//              rather than as a URL, and that the fetch carries the bearer.
//
//              Both halves are the same defect if either is wrong:
//              `/docs/<service>/openapi.json` sits behind
//              `forward-auth-c2`, which calls C2's `/auth/verify` and reads
//              a bearer HEADER (`HTTPBearer`). The platform keeps its token
//              in `localStorage`, not a cookie. So a request Swagger UI
//              issues ITSELF carries no credential and answers 401 — which
//              is precisely why `/docs/<service>` is unreachable by
//              navigation and why this page exists. Handing Swagger `url`
//              instead of `spec` would recreate the 401 inside the page
//              that was built to avoid it, and the page would still look
//              almost right: tabs, header, a spinner that never resolves.
//
//              A test that only asserted "a document rendered" would pass
//              on the broken version, because a developer reading the DOM
//              cannot see which key was handed to a third-party bundle.
// =============================================================================

// @vitest-environment jsdom

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ApiDocsPage from "@/app/(protected)/docs/page";
import { renderWithProviders } from "../helpers/render";

// `next/script` would not execute in jsdom; the page's `onLoad` is what
// gates rendering, so the stub fires it synchronously.
vi.mock("next/script", () => ({
  default: ({ onLoad }: { onLoad?: () => void }) => {
    onLoad?.();
    return null;
  },
}));

const TOKEN = "header.payload.signature";

vi.mock("@/lib/apiClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/apiClient")>();
  return { ...actual, readStoredToken: () => TOKEN };
});

/** Captures what the page hands the Swagger bundle. */
let swaggerCalls: Array<Record<string, unknown>> = [];

const SPEC = { openapi: "3.1.0", info: { title: "C2 Auth Service" }, paths: {} };

beforeEach(() => {
  swaggerCalls = [];
  (window as unknown as { SwaggerUIBundle: (o: Record<string, unknown>) => void }).SwaggerUIBundle =
    (o) => {
      swaggerCalls.push(o);
    };
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (_url: string, _init?: RequestInit) =>
        new Response(JSON.stringify(SPEC), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    ),
  );
});

describe("ApiDocsPage", () => {
  it("fetches the spec WITH the bearer and hands Swagger the object, not a url", async () => {
    renderWithProviders(<ApiDocsPage />);

    await waitFor(() => expect(swaggerCalls.length).toBe(1));

    // Matched by URL, not by position: `renderWithProviders` fetches
    // `/runtime-config.json` first, so call 0 belongs to the provider.
    const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
    const specCall = fetchMock.mock.calls.find((c) => String(c[0]).startsWith("/docs/")) as
      | [string, RequestInit]
      | undefined;
    expect(specCall, "the page never fetched a spec").toBeDefined();
    const [url, init] = specCall as [string, RequestInit];
    expect(url).toBe("/docs/c2/openapi.json");
    expect((init.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);

    const opts = swaggerCalls[0];
    // The whole point: a parsed document, so Swagger issues no request of
    // its own. `url` present would mean it does — and that request would
    // carry no token.
    expect(opts.spec).toEqual(SPEC);
    expect(opts).not.toHaveProperty("url");
  });

  it("switches component on tab click and re-fetches that component's spec", async () => {
    renderWithProviders(<ApiDocsPage />);
    await waitFor(() => expect(swaggerCalls.length).toBe(1));

    await userEvent.click(screen.getByTestId("api-docs-tab-c5"));

    await waitFor(() => expect(swaggerCalls.length).toBe(2));
    const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
    const urls = fetchMock.mock.calls.map((c) => c[0]);
    expect(urls).toContain("/docs/c5/openapi.json");
  });

  it("names the gateway's refusal rather than showing an empty page on 401", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("", { status: 401 })),
    );
    renderWithProviders(<ApiDocsPage />);

    await waitFor(() => expect(screen.getByTestId("api-docs-error")).toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent(/rejected the token/i);
    // A failed fetch must NOT reach Swagger: handing it `spec: undefined`
    // renders an empty frame that reads as "this component has no API".
    expect(swaggerCalls.length).toBe(0);
  });

  it("reports an undeployed component distinctly from an auth failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("", { status: 502 })),
    );
    renderWithProviders(<ApiDocsPage />);

    await waitFor(() => expect(screen.getByTestId("api-docs-error")).toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent(/HTTP 502/);
    expect(screen.getByRole("alert")).toHaveTextContent(/may not be deployed/i);
  });

  it("offers no 'try it out' control", async () => {
    renderWithProviders(<ApiDocsPage />);
    await waitFor(() => expect(swaggerCalls.length).toBe(1));

    // A request Swagger issues on submit would carry no bearer and answer
    // 401, so every call would read as a broken API. Disabled until the
    // request can be signed — asserted because a future Swagger upgrade
    // changing the default would re-enable it silently.
    expect(swaggerCalls[0].supportedSubmitMethods).toEqual([]);
  });
});
