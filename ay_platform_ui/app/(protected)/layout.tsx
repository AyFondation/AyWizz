// =============================================================================
// File: layout.tsx
// Version: 5
// Path: ay_platform_ui/app/(protected)/layout.tsx
//
// v5 (2026-05-19): wraps the authenticated outlet in
// <WorkspaceProvider> (Increment 3 phase 3a) so per-project UI
// state survives tab navigation + refresh (sessionStorage).
//
// Description: Auth gate for the route group `(protected)`. Every page
//              under this folder is rendered ONLY when the auth state
//              is "authenticated" AND the config bootstrap is "ready" ;
//              "anonymous" triggers a redirect to `/login` ;
//              loading-on-either-axis shows a placeholder.
//
//              The route group syntax `(protected)/` is Next.js App
//              Router idiom : the parentheses scope a layout / state
//              boundary without adding a path segment to the URL —
//              `app/(protected)/dashboard/page.tsx` resolves to
//              `/dashboard`, not `/protected/dashboard`.
//
//              v4 (2026-05-11) : wraps the gate in a `<Suspense>`
//              boundary. Next 16's `next build` prerenders every page
//              by default ; `useSearchParams()` triggers a CSR bailout
//              that the prerender can't tolerate without a Suspense
//              fallback. Splitting the gate into an inner component
//              ("ProtectedGate") under `<Suspense>` lets the build
//              succeed while keeping runtime behaviour identical.
//
//              v3 (2026-04-29) : also gates on the config state.
//              AuthProvider hydrates synchronously from localStorage ;
//              ConfigProvider fetches asynchronously. Without this
//              extra gate, an authenticated user can hit a half-
//              rendered tree where Navbar / pages call
//              `useReadyConfig()` while config is still "loading" —
//              that helper throws by contract. The gate keeps the
//              transition coherent : "Loading…" until both bootstraps
//              succeed, then the page.
//
//              v2 (2026-04-29) : preserves the user's location across
//              re-auth. The redirect carries the current pathname (+
//              query string) as `?redirect=<encoded path>` so the
//              login page can bounce back after a successful sign-in.
// =============================================================================

"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { type ReactNode, Suspense, useEffect } from "react";

import { Navbar } from "@/components/navbar";

import { useAuth } from "../auth-provider";
import { useConfigState } from "../providers";
import { WorkspaceProvider } from "./workspace-store";

function LoadingPlaceholder() {
  return (
    <main className="mx-auto max-w-5xl px-6 py-16">
      <p className="text-neutral-500">Loading…</p>
    </main>
  );
}

/** Inner gate that uses `useSearchParams()`. Split out so it sits
 *  under a `<Suspense>` boundary — required by Next 16's prerender
 *  pipeline (useSearchParams CSR-bails-out at build time and the
 *  static generator needs a fallback to swap in until hydration). */
function ProtectedGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { state: authState } = useAuth();
  const configState = useConfigState();

  // Redirect anonymous users out of the protected tree, carrying their
  // current location so /login can bounce them back after re-auth.
  // Runs in a useEffect because router.replace() must NOT fire during
  // render. URL-encode the path because `?redirect=` is a query value.
  // Fires regardless of config state — the login page itself uses
  // useConfigState() and handles its own loading UI.
  useEffect(() => {
    if (authState.status === "anonymous") {
      const queryString = searchParams.toString();
      const fullPath = queryString ? `${pathname}?${queryString}` : pathname;
      const target = `/login?redirect=${encodeURIComponent(fullPath)}`;
      router.replace(target);
    }
  }, [authState.status, router, pathname, searchParams]);

  // Surface a config bootstrap failure prominently — pages can't
  // render meaningfully without runtime + UX config.
  if (configState.status === "error") {
    return (
      <main className="mx-auto max-w-5xl px-6 py-16">
        <p className="text-red-700">Bootstrap failed: {configState.error}</p>
      </main>
    );
  }

  // Either bootstrap pending → unified placeholder.
  if (authState.status === "loading" || configState.status === "loading") {
    return <LoadingPlaceholder />;
  }

  if (authState.status === "anonymous") {
    // useEffect above will redirect ; render a minimal placeholder
    // for the brief render between mount and redirect.
    return (
      <main className="mx-auto max-w-5xl px-6 py-16">
        <p className="text-neutral-500">Redirecting to login…</p>
      </main>
    );
  }

  return (
    <>
      <Navbar />
      {/* WorkspaceProvider sits ABOVE the router outlet so per-project
          UI state (selected run/doc, active conversation, composer
          draft) survives tab navigation (Increment 3, phase 3a). */}
      <WorkspaceProvider>{children}</WorkspaceProvider>
    </>
  );
}

export default function ProtectedLayout({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<LoadingPlaceholder />}>
      <ProtectedGate>{children}</ProtectedGate>
    </Suspense>
  );
}
