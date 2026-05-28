// =============================================================================
// File: page.tsx
// Version: 4
// Path: ay_platform_ui/app/login/page.tsx
// Description: Login form. Posts username/password to C2 `/auth/login`
//              via the runtime-config-derived ApiClient ; on success
//              the AuthProvider stores the JWT (decoded claims +
//              localStorage in one shot) and the page redirects to
//              the user's prior location (or `/dashboard` by default).
//
//              v4 (2026-04-29) : renders a "DEV ONLY" credentials
//              panel below the form when `config.ux.dev_credentials`
//              is non-empty (server-side gated by
//              `C2_UX_DEV_MODE_ENABLED`). Each row is clickable and
//              auto-fills the username + password inputs. Yellow
//              banner makes the dev-only nature unmissable.
//
//              v3 (2026-04-29) : honours `?redirect=<path>` so users
//              bounced here by the protected-layout watchdog land
//              back on the page they were viewing. The redirect
//              value is sanitised (lib/auth.sanitizeRedirect) to
//              reject open-redirect attacks ; falls back to
//              `/dashboard` on null.
//
//              v2 (2026-04-29) : delegates token persistence to the
//              AuthProvider (`auth.setToken(token)`) instead of
//              writing localStorage directly ; redirects to
//              /dashboard instead of /. Authenticated users landing
//              here get auto-redirected too.
// =============================================================================

"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, Suspense, useEffect, useMemo, useState } from "react";

import { ApiClient, ApiError } from "@/lib/apiClient";
import { sanitizeRedirect } from "@/lib/auth";

import { useAuth } from "../auth-provider";
import { useConfigState } from "../providers";

// `LoginForm` uses `useSearchParams()` for `?redirect=` handling.
// Next 16's prerender pipeline CSR-bails-out on that hook, so the
// page exports a thin wrapper that puts the form under a `<Suspense>`
// boundary — runtime behaviour is unchanged.
function LoginForm() {
  const state = useConfigState();
  const router = useRouter();
  const searchParams = useSearchParams();
  const { state: authState, setToken } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // The post-login destination. `sanitizeRedirect` returns null for
  // empty / external / malformed inputs ; the `||` fallback substitutes
  // /dashboard. Re-evaluated each render — cheap, and searchParams is
  // stable across renders so the value is stable when the URL is.
  const redirectTarget = sanitizeRedirect(searchParams.get("redirect")) ?? "/dashboard";

  // Already authenticated → bounce to the redirect target. Guards
  // against accidentally landing here while logged in (e.g. user hits
  // the back button after sign-in).
  useEffect(() => {
    if (authState.status === "authenticated") {
      router.replace(redirectTarget);
    }
  }, [authState.status, router, redirectTarget]);

  const apiClient = useMemo(() => {
    if (state.status !== "ready") return null;
    return new ApiClient(state.config);
  }, [state]);

  if (state.status === "loading") {
    return (
      <main className="mx-auto max-w-md px-6 py-16">
        <p className="text-neutral-500">Loading…</p>
      </main>
    );
  }
  if (state.status === "error") {
    return (
      <main className="mx-auto max-w-md px-6 py-16">
        <p className="text-red-700">Bootstrap failed: {state.error}</p>
      </main>
    );
  }

  const accent = state.config.ux.brand.accent_color_hex;
  const devCredentials = state.config.ux.dev_credentials ?? null;

  function autofillCredential(u: string, p: string) {
    setUsername(u);
    setPassword(p);
    setError(null);
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!apiClient) return;
    setError(null);
    setSubmitting(true);
    try {
      const token = await apiClient.login(username, password);
      setToken(token);
      router.push(redirectTarget);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.status === 401 ? "Invalid credentials" : `Login failed (HTTP ${err.status})`);
      } else {
        const msg = err instanceof Error ? err.message : String(err);
        setError(`Network error: ${msg}`);
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="mx-auto max-w-md px-6 py-16">
      <h1 className="text-2xl font-semibold">Sign in</h1>
      <p className="mt-2 text-sm text-neutral-500">
        Auth mode:{" "}
        <code className="rounded bg-neutral-100 px-2 py-0.5">{state.config.ux.auth_mode}</code>
      </p>

      <form onSubmit={handleSubmit} className="mt-8 space-y-4">
        <label className="block">
          <span className="text-sm font-medium text-neutral-700">Username</span>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            autoComplete="username"
            className="mt-1 block w-full rounded-md border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2"
            style={{ outlineColor: accent }}
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium text-neutral-700">Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete="current-password"
            className="mt-1 block w-full rounded-md border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2"
            style={{ outlineColor: accent }}
          />
        </label>

        {error ? (
          <p className="text-sm text-red-700" role="alert">
            {error}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-md px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          style={{ backgroundColor: accent }}
        >
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>

      {devCredentials && devCredentials.length > 0 ? (
        <section
          aria-labelledby="dev-credentials-heading"
          data-testid="dev-credentials-panel"
          className="mt-10 rounded-md border-2 border-yellow-400 bg-yellow-50 p-4"
        >
          <h2
            id="dev-credentials-heading"
            className="text-sm font-bold uppercase tracking-wide text-yellow-900"
          >
            ⚠ Dev only — demo credentials
          </h2>
          <p className="mt-1 text-xs text-yellow-800">
            Click a row to auto-fill the form. These accounts exist only when the platform is
            started with{" "}
            <code className="rounded bg-yellow-100 px-1">C2_DEMO_SEED_ENABLED=true</code>.
          </p>
          <ul className="mt-3 space-y-2">
            {devCredentials.map((c) => (
              <li key={c.username}>
                <button
                  type="button"
                  onClick={() => autofillCredential(c.username, c.password)}
                  className="block w-full rounded border border-yellow-300 bg-white p-2 text-left text-xs hover:bg-yellow-100"
                  data-testid={`dev-credential-${c.username}`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-neutral-900">{c.username}</span>
                    <span className="text-neutral-500">{c.role_label}</span>
                  </div>
                  <div className="mt-0.5 text-neutral-500">
                    pw: <code>{c.password}</code>
                  </div>
                  {c.note ? <div className="mt-0.5 text-neutral-400">{c.note}</div> : null}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </main>
  );
}

function LoginLoadingFallback() {
  return (
    <main className="mx-auto max-w-md px-6 py-16">
      <p className="text-neutral-500">Loading…</p>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<LoginLoadingFallback />}>
      <LoginForm />
    </Suspense>
  );
}
