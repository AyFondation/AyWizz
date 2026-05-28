// =============================================================================
// File: runtimeConfig.ts
// Version: 1
// Path: ay_platform_ui/lib/runtimeConfig.ts
// Description: Two-stage UX bootstrap loader.
//                Stage 1: fetch `/runtime-config.json` (static, mounted
//                  from K8s ConfigMap at deploy time) → discover the
//                  API base URL.
//                Stage 2: fetch `<apiBaseUrl>/ux/config` (dynamic, served
//                  by C2) → discover brand, feature flags, auth mode.
//              The result is a `PlatformConfig` that the React
//              `<ConfigProvider>` (app/providers.tsx) exposes to every
//              Client Component. Both stages run in the BROWSER —
//              Server Components don't go through this path.
// =============================================================================

import type { PlatformConfig, RuntimeConfig, UxConfig } from "./types";

/** Raised when bootstrap fails. The provider catches it and renders an
 *  error state rather than letting the app crash. */
export class ConfigError extends Error {
  constructor(stage: "runtime" | "ux", cause: string) {
    super(`bootstrap stage=${stage}: ${cause}`);
    this.name = "ConfigError";
  }
}

async function fetchJSON<T>(stage: "runtime" | "ux", url: string): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(url, { cache: "no-store" });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new ConfigError(stage, `network error fetching ${url}: ${msg}`);
  }
  if (!resp.ok) {
    throw new ConfigError(stage, `HTTP ${resp.status} from ${url}`);
  }
  try {
    return (await resp.json()) as T;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new ConfigError(stage, `invalid JSON from ${url}: ${msg}`);
  }
}

/** Stage 1 — static deployment-time config. Always lives at the UI's
 *  origin under `/runtime-config.json`. */
export async function loadRuntimeConfig(): Promise<RuntimeConfig> {
  return fetchJSON<RuntimeConfig>("runtime", "/runtime-config.json");
}

/** Stage 2 — dynamic config served by C2. The UI hits this AFTER it
 *  has the apiBaseUrl from stage 1. */
export async function loadUxConfig(apiBaseUrl: string): Promise<UxConfig> {
  // Empty `apiBaseUrl` means relative URL — same-origin via Traefik
  // (prod) or via Next.js dev rewrites (dev).
  const url = apiBaseUrl ? `${apiBaseUrl}/ux/config` : "/ux/config";
  return fetchJSON<UxConfig>("ux", url);
}

/** Run both stages. Called once by `<ConfigProvider>` on mount. */
export async function bootstrapConfig(): Promise<PlatformConfig> {
  const runtime = await loadRuntimeConfig();
  const ux = await loadUxConfig(runtime.apiBaseUrl);
  return { runtime, ux };
}
