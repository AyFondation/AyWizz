// =============================================================================
// File: navbar.tsx
// Version: 8
// Path: ay_platform_ui/components/navbar.tsx
// Description: Top header rendered on every protected page. Brand
//              (left) is read from the runtime config ; user menu
//              (right) is read from the auth provider's decoded JWT
//              claims. Logout button clears auth + redirects to login.
//
//              v8 (E-100-002 v7) : the tenant operator `admin`
//              (= tenant_admin) is now a content-blind, tenant-scoped
//              mirror of platform_manager. It gets the operator
//              Projects + Users governance links (backend scopes them
//              to its tenant) and the classic /projects workspace is
//              hidden for it. Platform-only links (Tenants, Providers,
//              LLM registry, global Quotas) stay platform_manager-only.
//
//              v6 : a two-line `BuildStamp` block (UI build / API
//              build) is rendered to the LEFT of the avatar on the
//              right-hand side so the operator can verify after every
//              rebuild that both stamps actually moved. Hidden below
//              `sm` to keep the mobile header compact.
//
//              v5 (2026-05-12) : the user area is now a compact
//              **trigram avatar** (3-4 letters) with a native tooltip
//              showing the full name on hover (~1 s delay). Click
//              navigates to `/preferences` where the trigram can be
//              edited. The full username + tenant + roles line is
//              kept in `/profile`.
//
//              v4 (2026-05-11) : the user info area becomes a link
//              to `/profile` (Phase B). Active state mirrors the
//              same `usePathname()` prefix pattern as the Projects
//              link.
//
//              v3 (2026-05-11) : adds a `Projects` nav link (between
//              brand and user info) so any protected page surfaces
//              the project list one click away. Active state hooks
//              on `usePathname()` matching `/projects` prefix.
//
//              v2 (2026-04-29) : reads config defensively via
//              `useConfigState()` (not `useReadyConfig()`). The
//              protected layout gates on both bootstraps so this
//              component never sees an unready state in production,
//              but tests that mount the navbar directly without the
//              layout get a clean null render instead of a thrown
//              error. Hooks always run in the same order (no early
//              returns before useAuth/useRouter).
// =============================================================================

"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import { useAuth } from "@/app/auth-provider";
import { useConfigState } from "@/app/providers";
import { Avatar } from "@/components/avatar";
import { BuildStamp } from "@/components/build-stamp";
import { QuotaIndicator } from "@/components/quota-indicator";
import { fullNameForTooltip, getEffectiveTrigram } from "@/lib/preferences";

export function Navbar() {
  const router = useRouter();
  const pathname = usePathname();
  const configState = useConfigState();
  const { state, clearAuth } = useAuth();

  function handleLogout() {
    clearAuth();
    router.push("/login");
  }

  // Belt-and-braces guards : layout already gates, but rendering
  // outside the layout (e.g. in tests, or a future surface) shouldn't
  // throw.
  if (state.status !== "authenticated") return null;
  if (configState.status !== "ready") return null;

  const config = configState.config;
  const accent = config.ux.brand.accent_color_hex;
  const trigram = getEffectiveTrigram(state.claims);
  const fullName = fullNameForTooltip(state.claims);

  // Operator nav (role-gated, E-100-002 v7). Two operator tiers:
  //  - platform_manager: cross-tenant PLATFORM operator (tenants, LLM
  //    registry/providers, global quota policy) + governance.
  //  - admin / tenant_admin: tenant-SCOPED operator, mirror of
  //    platform_manager confined to its own tenant — project governance +
  //    user oversight. Both are content-blind (no classic project workspace).
  const roles = new Set(state.claims.roles ?? []);
  const isPlatformManager = roles.has("platform_manager");
  const isTenantOperator = roles.has("admin") || roles.has("tenant_admin");
  const isOperator = isPlatformManager || isTenantOperator;
  // Platform-only surfaces (cross-tenant): registry, providers, tenants,
  // global quotas. `showCatalogue` = the tenant LLM catalogue (tenant ops).
  const showRegistry = isPlatformManager;
  const showCatalogue = isTenantOperator;

  const navLinkClass = (active: boolean | undefined): string =>
    [
      "rounded-md px-3 py-1.5 transition-colors",
      active ? "bg-neutral-100 text-neutral-900" : "text-neutral-600 hover:bg-neutral-50",
    ].join(" ");

  return (
    <header
      className="border-b border-neutral-200 bg-white"
      style={{ borderTopColor: accent, borderTopWidth: 3 }}
    >
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-6 py-3">
        <div className="flex items-center gap-6">
          <Link
            href={isOperator ? "/operator/projects" : "/projects"}
            className="text-lg font-semibold tracking-tight"
            style={{ color: accent }}
          >
            {config.ux.brand.short_name}
          </Link>
          <nav aria-label="Primary" className="hidden md:block">
            <ul className="flex items-center gap-1 text-sm">
              {/* Classic project workspace — hidden for the content-blind
                  operators (platform_manager AND admin/tenant_admin, v7),
                  whose project surface is the governance page
                  /operator/projects below. Without this an operator sees two
                  "Projects" links, the classic one 403-ing on /api/v1/projects. */}
              {!isOperator && (
                <li>
                  <Link
                    href="/projects"
                    className={[
                      "rounded-md px-3 py-1.5 transition-colors",
                      pathname?.startsWith("/projects")
                        ? "bg-neutral-100 text-neutral-900"
                        : "text-neutral-600 hover:bg-neutral-50",
                    ].join(" ")}
                    data-testid="navbar-link-projects"
                  >
                    Projects
                  </Link>
                </li>
              )}
              {showRegistry && (
                <li>
                  <Link
                    href="/admin/llm-providers"
                    className={navLinkClass(pathname?.startsWith("/admin/llm-providers"))}
                    data-testid="navbar-link-llm-providers"
                  >
                    Providers
                  </Link>
                </li>
              )}
              {showRegistry && (
                <li>
                  <Link
                    href="/admin/llm-registry"
                    className={navLinkClass(pathname?.startsWith("/admin/llm-registry"))}
                    data-testid="navbar-link-llm-registry"
                  >
                    LLM registry
                  </Link>
                </li>
              )}
              {showCatalogue && (
                <li>
                  <Link
                    href="/admin/llm-catalogue"
                    className={navLinkClass(pathname?.startsWith("/admin/llm-catalogue"))}
                    data-testid="navbar-link-llm-catalogue"
                  >
                    LLM catalogue
                  </Link>
                </li>
              )}
              {showRegistry && (
                <li>
                  <Link
                    href="/operator/tenants"
                    className={navLinkClass(pathname?.startsWith("/operator/tenants"))}
                    data-testid="navbar-link-tenants"
                  >
                    Tenants
                  </Link>
                </li>
              )}
              {isOperator && (
                <li>
                  <Link
                    href="/operator/projects"
                    className={navLinkClass(pathname?.startsWith("/operator/projects"))}
                    data-testid="navbar-link-projects"
                  >
                    Projects
                  </Link>
                </li>
              )}
              {isOperator && (
                <li>
                  <Link
                    href="/operator/users"
                    className={navLinkClass(pathname?.startsWith("/operator/users"))}
                    data-testid="navbar-link-users"
                  >
                    Users
                  </Link>
                </li>
              )}
              {showRegistry && (
                <li>
                  <Link
                    href="/operator/quotas"
                    className={navLinkClass(pathname?.startsWith("/operator/quotas"))}
                    data-testid="navbar-link-quotas"
                  >
                    Quotas
                  </Link>
                </li>
              )}
            </ul>
          </nav>
        </div>

        <div className="flex items-center gap-3 text-sm">
          {/* Build-version block — UI bundle on top line, API tier
           *  below. Two lines so a ~20-char ISO timestamp fits without
           *  ballooning the header height. Hidden < sm to keep the
           *  mobile bar compact. */}
          <div className="hidden sm:block">
            <BuildStamp />
          </div>
          <QuotaIndicator />
          <Link
            href="/preferences"
            className={[
              "block rounded-full p-0.5 transition-colors",
              pathname?.startsWith("/preferences") || pathname?.startsWith("/profile")
                ? "ring-2 ring-blue-300"
                : "hover:ring-2 hover:ring-neutral-200",
            ].join(" ")}
            data-testid="navbar-link-profile"
            aria-label={`Open preferences (signed in as ${fullName})`}
          >
            <Avatar trigram={trigram} fullName={fullName} variant="user" />
          </Link>
          <button
            type="button"
            onClick={handleLogout}
            className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
          >
            Sign out
          </button>
        </div>
      </div>
    </header>
  );
}
