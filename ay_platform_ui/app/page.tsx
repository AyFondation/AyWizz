// =============================================================================
// File: page.tsx
// Version: 3
// Path: ay_platform_ui/app/page.tsx
// Description: Public landing page. Renders brand / feature flags
//              from the runtime config + a Sign-in CTA. Authenticated
//              users are redirected to /dashboard so this route only
//              shows to anonymous visitors.
//
//              v3 (2026-04-29) : added the auth-aware redirect.
// =============================================================================

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "./auth-provider";
import { useConfigState } from "./providers";

export default function LandingPage() {
  const router = useRouter();
  const state = useConfigState();
  const { state: authState } = useAuth();

  useEffect(() => {
    if (authState.status === "authenticated") {
      router.replace("/dashboard");
    }
  }, [authState.status, router]);

  if (state.status === "loading") {
    return (
      <main className="mx-auto max-w-2xl px-6 py-16">
        <p className="text-neutral-500">Loading platform config…</p>
      </main>
    );
  }

  if (state.status === "error") {
    return (
      <main className="mx-auto max-w-2xl px-6 py-16">
        <h1 className="text-2xl font-semibold text-red-700">Bootstrap failed</h1>
        <p className="mt-2 text-neutral-700">{state.error}</p>
        <p className="mt-4 text-sm text-neutral-500">
          Check that <code>/runtime-config.json</code> is reachable and that the platform gateway
          answers <code>GET /ux/config</code>.
        </p>
      </main>
    );
  }

  const { ux, runtime } = state.config;
  const accent = ux.brand.accent_color_hex;

  return (
    <main className="mx-auto max-w-2xl px-6 py-16">
      <h1 className="text-4xl font-semibold tracking-tight" style={{ color: accent }}>
        {ux.brand.name}
      </h1>
      <p className="mt-4 text-lg text-neutral-600">
        Requirements-driven artifact generation for code, documentation and presentations.
      </p>

      <section className="mt-10 rounded-lg border border-neutral-200 bg-white p-6">
        <h2 className="text-sm uppercase tracking-wide text-neutral-500">Platform status</h2>
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
          <dt className="text-neutral-500">API version</dt>
          <dd>
            <code className="rounded bg-neutral-100 px-2 py-0.5">{ux.api_version}</code>
          </dd>
          <dt className="text-neutral-500">Auth mode</dt>
          <dd>
            <code className="rounded bg-neutral-100 px-2 py-0.5">{ux.auth_mode}</code>
          </dd>
          <dt className="text-neutral-500">API base URL</dt>
          <dd>
            <code className="rounded bg-neutral-100 px-2 py-0.5">
              {runtime.apiBaseUrl || "(same-origin)"}
            </code>
          </dd>
        </dl>
      </section>

      <section className="mt-6 rounded-lg border border-neutral-200 bg-white p-6">
        <h2 className="text-sm uppercase tracking-wide text-neutral-500">Features</h2>
        <ul className="mt-3 space-y-1 text-sm">
          <li>chat: {ux.features.chat_enabled ? "✓" : "—"}</li>
          <li>knowledge graph: {ux.features.kg_enabled ? "✓" : "—"}</li>
          <li>file download: {ux.features.file_download_enabled ? "✓" : "—"}</li>
          <li>cross-tenant sources: {ux.features.cross_tenant_enabled ? "✓" : "—"}</li>
        </ul>
      </section>

      <section className="mt-6">
        <Link
          href="/login"
          className="inline-block rounded-md px-4 py-2 text-sm font-medium text-white"
          style={{ backgroundColor: accent }}
        >
          Sign in
        </Link>
      </section>

      <footer className="mt-16 text-xs text-neutral-400">
        Frontend bootstrap reads `/runtime-config.json` (deployment-time) + `/ux/config`
        (server-time). Change either without rebuilding.
      </footer>
    </main>
  );
}
