// =============================================================================
// File: page.tsx
// Version: 2
// Path: ay_platform_ui/app/(protected)/dashboard/page.tsx
// Description: Placeholder dashboard for authenticated users. Shows
//              the decoded JWT claims so the developer can confirm
//              auth-state propagation end-to-end. Real dashboard
//              content (project list, recent conversations, etc.)
//              lands in subsequent phases.
//
//              v2 (2026-04-29) : reads config defensively via
//              `useConfigState()` instead of `useReadyConfig()`. The
//              ProtectedLayout gates on both auth + config, so in
//              production the page is never rendered while config is
//              loading — but tests that mount the page directly
//              (bypassing the layout) deserve a non-throwing path.
//              Returns null while config isn't ready (loading or
//              error states), matching what the layout would have
//              shown.
// =============================================================================

"use client";

import { useAuth } from "../../auth-provider";
import { useConfigState } from "../../providers";

export default function DashboardPage() {
  const { state } = useAuth();
  const configState = useConfigState();

  if (state.status !== "authenticated") return null;
  if (configState.status !== "ready") return null;

  const { claims } = state;
  const accent = configState.config.ux.brand.accent_color_hex;

  return (
    <main className="mx-auto max-w-5xl px-6 py-12">
      <h1 className="text-3xl font-semibold tracking-tight" style={{ color: accent }}>
        Dashboard
      </h1>
      <p className="mt-2 text-neutral-600">Welcome, {claims.username || claims.sub}.</p>

      <section className="mt-10 rounded-lg border border-neutral-200 bg-white p-6">
        <h2 className="text-sm uppercase tracking-wide text-neutral-500">JWT claims</h2>
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
          <dt className="text-neutral-500">Subject (user_id)</dt>
          <dd>
            <code className="rounded bg-neutral-100 px-2 py-0.5">{claims.sub}</code>
          </dd>
          <dt className="text-neutral-500">Username</dt>
          <dd>
            <code className="rounded bg-neutral-100 px-2 py-0.5">
              {claims.username ?? "(not set)"}
            </code>
          </dd>
          <dt className="text-neutral-500">Tenant</dt>
          <dd>
            <code className="rounded bg-neutral-100 px-2 py-0.5">
              {claims.tenant_id ?? "(not scoped)"}
            </code>
          </dd>
          <dt className="text-neutral-500">Roles</dt>
          <dd>
            {claims.roles && claims.roles.length > 0 ? (
              <ul className="flex flex-wrap gap-1">
                {claims.roles.map((role) => (
                  <li key={role} className="rounded bg-neutral-100 px-2 py-0.5 text-xs">
                    {role}
                  </li>
                ))}
              </ul>
            ) : (
              <span className="text-neutral-400">none</span>
            )}
          </dd>
          <dt className="text-neutral-500">Token expires</dt>
          <dd className="text-neutral-700">{new Date(claims.exp * 1000).toLocaleString()}</dd>
        </dl>
      </section>

      <section className="mt-6 rounded-lg border border-dashed border-neutral-300 p-6 text-sm text-neutral-500">
        <p>Real dashboard content lands in subsequent phases :</p>
        <ul className="mt-2 list-disc pl-5">
          <li>Project list (C2 / C5)</li>
          <li>Recent conversations (C3)</li>
          <li>Source library + upload (C7)</li>
          <li>Quota + usage</li>
        </ul>
      </section>
    </main>
  );
}
