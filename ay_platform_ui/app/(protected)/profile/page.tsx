// =============================================================================
// File: page.tsx
// Version: 1
// Path: ay_platform_ui/app/(protected)/profile/page.tsx
// Description: User self-profile view. Surfaces every JWT claim
//              relevant to operators (identity, roles, project scopes,
//              token lifetime) in a layout that's quick to scan but
//              forensic — handy for "why don't I have access to X?"
//              triage. v1 is read-only ; password change / display
//              name edit land later (require dedicated C2 endpoints
//              that don't yet exist).
//
//              Eventually replaces /dashboard ; the legacy route
//              stays for one release as a redirect-friendly fallback.
// =============================================================================

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { useAuth } from "../../auth-provider";

export default function ProfilePage() {
  const router = useRouter();
  const { state, clearAuth } = useAuth();
  // Tick a counter every 30s so the "expires in" relative time stays
  // honest without a heavy timer.
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 30_000);
    return () => clearInterval(id);
  }, []);

  if (state.status !== "authenticated") return null;
  const { claims, token } = state;

  function handleSignOut() {
    clearAuth();
    router.push("/login");
  }

  const expiresAt = new Date(claims.exp * 1000);
  const issuedAt = claims.iat ? new Date(claims.iat * 1000) : null;
  const ttlMs = expiresAt.getTime() - Date.now();
  const relativeExpiry = formatRelativeTime(ttlMs);

  const projectScopes = (claims.project_scopes ?? {}) as Record<string, string[]>;
  const projectIds = Object.keys(projectScopes);

  return (
    <main className="mx-auto max-w-7xl px-6 py-10">
      <header className="flex items-baseline justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Your profile</h1>
          <p className="mt-1 text-sm text-neutral-500">
            Identity, roles and token info — read-only in v1.
          </p>
        </div>
        <div className="flex gap-2">
          <Link
            href="/preferences"
            className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
            data-testid="profile-preferences-link"
          >
            Preferences
          </Link>
          <button
            type="button"
            onClick={handleSignOut}
            className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
            data-testid="profile-signout"
          >
            Sign out
          </button>
        </div>
      </header>

      <section
        className="mt-8 rounded-lg border border-neutral-200 bg-white p-6"
        data-testid="profile-identity"
      >
        <h2 className="text-sm font-medium uppercase tracking-wide text-neutral-500">Identity</h2>
        <dl className="mt-4 grid grid-cols-1 gap-x-6 gap-y-3 text-sm md:grid-cols-2">
          <Field label="Username" value={claims.username ?? "(not set)"} mono />
          <Field label="Display name" value={(claims.name as string) ?? "(not set)"} />
          <Field label="User id (sub)" value={claims.sub} mono />
          <Field label="Email" value={(claims.email as string) ?? "(not set)"} />
          <Field label="Tenant" value={claims.tenant_id ?? "(not scoped)"} mono />
          <Field label="Auth mode" value={(claims.auth_mode as string) ?? "(unknown)"} mono />
        </dl>
      </section>

      <section
        className="mt-6 rounded-lg border border-neutral-200 bg-white p-6"
        data-testid="profile-roles"
      >
        <h2 className="text-sm font-medium uppercase tracking-wide text-neutral-500">Roles</h2>
        <div className="mt-4">
          <p className="text-xs uppercase tracking-wide text-neutral-400">Global</p>
          <ul className="mt-1 flex flex-wrap gap-1">
            {(claims.roles ?? []).length > 0 ? (
              (claims.roles ?? []).map((r) => (
                <li
                  key={r}
                  className="rounded bg-blue-100 px-2 py-0.5 font-mono text-xs text-blue-900"
                >
                  {r}
                </li>
              ))
            ) : (
              <li className="text-sm text-neutral-400">none</li>
            )}
          </ul>
        </div>
        <div className="mt-5">
          <p className="text-xs uppercase tracking-wide text-neutral-400">Project scopes</p>
          {projectIds.length === 0 ? (
            <p className="mt-1 text-sm text-neutral-400">
              No per-project grants (admin / tenant_admin roles see every project in their tenant
              regardless).
            </p>
          ) : (
            <ul className="mt-2 space-y-1.5 text-sm">
              {projectIds.map((pid) => (
                <li key={pid} className="flex flex-wrap items-center gap-2">
                  <Link
                    href={`/projects/${encodeURIComponent(pid)}`}
                    className="rounded bg-neutral-100 px-2 py-0.5 font-mono text-xs text-neutral-900 hover:bg-neutral-200"
                  >
                    {pid}
                  </Link>
                  {projectScopes[pid].map((r) => (
                    <span
                      key={r}
                      className="rounded bg-emerald-100 px-2 py-0.5 font-mono text-xs text-emerald-900"
                    >
                      {r}
                    </span>
                  ))}
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <section
        className="mt-6 rounded-lg border border-neutral-200 bg-white p-6"
        data-testid="profile-session"
      >
        <h2 className="text-sm font-medium uppercase tracking-wide text-neutral-500">Session</h2>
        <dl className="mt-4 grid grid-cols-1 gap-x-6 gap-y-3 text-sm md:grid-cols-2">
          <Field
            label="Token expires"
            value={`${expiresAt.toLocaleString()} (${relativeExpiry})`}
          />
          {issuedAt ? <Field label="Issued at" value={issuedAt.toLocaleString()} /> : null}
          <Field label="Token id (jti)" value={(claims.jti as string) ?? "(absent)"} mono />
          <Field label="Bearer" value={`${token.slice(0, 24)}…`} mono />
        </dl>
        <p className="mt-4 text-xs text-neutral-400">
          Re-authenticate before expiration to avoid losing your current UI state — the watchdog
          redirects you to /login automatically when the token expires.
        </p>
      </section>

      <section className="mt-6 rounded-lg border border-dashed border-neutral-300 p-5 text-sm text-neutral-500">
        <p>Coming later :</p>
        <ul className="mt-1 list-disc pl-5">
          <li>Change password (C2 endpoint to be added)</li>
          <li>Edit display name / email (C2 endpoint to be added)</li>
          <li>Active sessions list with revocation</li>
        </ul>
      </section>
    </main>
  );
}

function Field({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-neutral-400">{label}</dt>
      <dd className={["mt-0.5 text-neutral-900", mono ? "font-mono text-xs" : "text-sm"].join(" ")}>
        {value}
      </dd>
    </div>
  );
}

/** Compact relative-time formatter for token TTL. Output: "in 58 min",
 *  "in 2 h 5 min", or "expired" if ms <= 0. Doesn't try to be a full
 *  Intl.RelativeTimeFormat (which would localize but lose the short form). */
function formatRelativeTime(ms: number): string {
  if (ms <= 0) return "expired";
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 60) return `in ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const remMin = minutes % 60;
  return remMin === 0 ? `in ${hours} h` : `in ${hours} h ${remMin} min`;
}
