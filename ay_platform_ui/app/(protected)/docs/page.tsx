// =============================================================================
// File: page.tsx
// Version: 1
// Path: ay_platform_ui/app/(protected)/docs/page.tsx
// Description: The platform's generated API documentation, readable in a
//              browser by an authenticated user.
//
//              WHY THIS PAGE EXISTS. Each component serves FastAPI's
//              generated Swagger UI at `/docs/<service>` behind
//              `forward-auth-c2`, and that middleware calls C2's
//              `/auth/verify`, which reads a BEARER header (`HTTPBearer`).
//              The platform keeps its token in `localStorage`, not a cookie,
//              so a browser NAVIGATION to one of those URLs carries no
//              credential and gets 401. The documentation was reachable by
//              curl and unreachable by clicking.
//
//              WHY NOT A COOKIE. Teaching `/auth/verify` to accept one would
//              make every route cookie-authenticable and open a CSRF surface
//              across the whole API — for the sake of a documentation page.
//              This platform has no cookie anywhere, deliberately.
//
//              HOW THIS WORKS INSTEAD. The page sits inside `(protected)`,
//              so the existing auth gate is the authentication: an anonymous
//              visitor is bounced to `/login` before any of this renders.
//              It then FETCHES the spec with the bearer — which the
//              `/docs/<svc>/openapi.json` route already accepts — and hands
//              the parsed object to Swagger UI as `spec`, not `url`. Swagger
//              therefore never issues a request of its own, so there is
//              nothing for it to fail to authenticate.
//
//              THE CDN IS NOT A NEW DEPENDENCY. `fastapi.openapi.docs`
//              already loads `swagger-ui-dist@5` from jsdelivr for the pages
//              the components serve; this uses the same origin and the same
//              major. No `npm install`, no bundled copy to keep in step with
//              the backend's.
// =============================================================================

"use client";

import Script from "next/script";
import { useCallback, useEffect, useRef, useState } from "react";

import { readStoredToken } from "@/lib/apiClient";

const SWAGGER_JS = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js";
const SWAGGER_CSS = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css";

/** One entry per component serving an OpenAPI document.
 *
 *  `service` is the Traefik service name, which is also the path segment
 *  (`api_docs.docs_urls`) — the same string in `routers.yml`, in the URL and
 *  here, so a reader checking one place has checked all three. */
const COMPONENTS: ReadonlyArray<{ service: string; label: string; blurb: string }> = [
  { service: "c2", label: "C2 — Auth", blurb: "Identity, tenants, projects, roles" },
  { service: "c3", label: "C3 — Conversations", blurb: "Chat turns and RAG answers" },
  { service: "c4", label: "C4 — Orchestrator", blurb: "The five-phase pipeline" },
  { service: "c5", label: "C5 — Requirements", blurb: "Corpus, traceability, plans" },
  { service: "c6", label: "C6 — Validation", blurb: "Runs, findings, verdicts" },
  { service: "c7", label: "C7 — Memory", blurb: "Sources, embeddings, retrieval" },
  { service: "c8-admin", label: "C8 — LLM admin", blurb: "Registries, quotas, storage" },
  { service: "c9", label: "C9 — MCP", blurb: "Capabilities as agent tools" },
  { service: "c16-backup", label: "C16 — Backup", blurb: "Project snapshot and restore" },
];

type LoadState =
  | { status: "idle" }
  | { status: "loading"; service: string }
  | { status: "ready"; service: string }
  | { status: "error"; service: string; message: string };

export default function ApiDocsPage() {
  const [selected, setSelected] = useState<string>(COMPONENTS[0].service);
  const [state, setState] = useState<LoadState>({ status: "idle" });
  const [scriptReady, setScriptReady] = useState(false);
  const mountRef = useRef<HTMLDivElement | null>(null);

  const render = useCallback(async (service: string): Promise<void> => {
    setState({ status: "loading", service });
    const token = readStoredToken();
    if (!token) {
      // The `(protected)` gate should have redirected already; this is the
      // honest message for the window where a token was just cleared.
      setState({ status: "error", service, message: "No session token — sign in again." });
      return;
    }
    let spec: unknown;
    try {
      const resp = await fetch(`/docs/${service}/openapi.json`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
      });
      if (!resp.ok) {
        setState({
          status: "error",
          service,
          message:
            resp.status === 401
              ? "The gateway rejected the token. Sign in again."
              : `The component answered HTTP ${resp.status}. It may not be deployed.`,
        });
        return;
      }
      spec = await resp.json();
    } catch (err) {
      setState({
        status: "error",
        service,
        message: err instanceof Error ? err.message : String(err),
      });
      return;
    }

    const bundle = (window as unknown as { SwaggerUIBundle?: (o: object) => void }).SwaggerUIBundle;
    if (!bundle || !mountRef.current) {
      setState({ status: "error", service, message: "Swagger UI did not load." });
      return;
    }
    // `spec`, never `url`: handing Swagger the parsed document means it
    // issues no request of its own and so needs no credential. Passing a
    // url here is exactly the mistake that makes the page 401.
    bundle({
      spec,
      domNode: mountRef.current,
      deepLinking: true,
      // The injected identity headers are hidden from the document
      // (`api_docs._hide_injected_headers`), and a "try it out" request
      // from this page would carry no bearer, so every call would 401 and
      // read as a broken API. Left off until the request can be signed.
      supportedSubmitMethods: [],
    });
    setState({ status: "ready", service });
  }, []);

  useEffect(() => {
    if (!scriptReady) return;
    void render(selected);
  }, [scriptReady, selected, render]);

  return (
    <main className="px-6 py-10" data-testid="api-docs">
      <link rel="stylesheet" href={SWAGGER_CSS} />
      <Script src={SWAGGER_JS} onLoad={() => setScriptReady(true)} strategy="afterInteractive" />

      <header>
        <h2 className="text-2xl font-semibold tracking-tight">API documentation</h2>
        <p className="mt-2 max-w-3xl text-sm text-neutral-600">
          Generated from the running code, one document per component. Every route below is reached
          through the gateway with a bearer token; the identity headers the gateway injects are not
          part of the contract and a client that sets them has them replaced.
        </p>
      </header>

      <nav className="mt-6 flex flex-wrap gap-2" aria-label="Components">
        {COMPONENTS.map((c) => (
          <button
            key={c.service}
            type="button"
            onClick={() => setSelected(c.service)}
            title={c.blurb}
            data-testid={`api-docs-tab-${c.service}`}
            className={`rounded-md border px-3 py-1.5 text-sm ${
              c.service === selected
                ? "border-neutral-900 bg-neutral-900 text-white"
                : "border-neutral-300 bg-white text-neutral-700 hover:bg-neutral-50"
            }`}
          >
            {c.label}
          </button>
        ))}
      </nav>

      {state.status === "loading" ? (
        <p className="mt-6 text-sm text-neutral-500" data-testid="api-docs-loading">
          Loading {state.service}…
        </p>
      ) : null}
      {state.status === "error" ? (
        <p className="mt-6 text-sm text-red-700" role="alert" data-testid="api-docs-error">
          {state.message}
        </p>
      ) : null}

      <div ref={mountRef} className="mt-6" data-testid="api-docs-mount" />
    </main>
  );
}
